# Robust fuel-cap API: design

Status: design for implementation. Educational, not dispatch software.

## 1. Goal

`POST /standard-calc` takes everything it needs in the request body. It returns:

- the robust fuel cap,
- how long the solve took,
- a structured error if the request fails.

There is no database, no cache and no state between requests. The API is a thin translation layer around the existing solver in `app/wb_core.py` (`Problem`, `solve`). The algorithm is described in `ALGORITHMS.md` and `WEIGHT_AND_BALANCE_HANDOFF.md`, and the Streamlit demo in `app/streamlit_app.py` shows it working.

### Decisions already made

| Topic | Decision |
|---|---|
| Several tail numbers | **One fleet cap.** All tails are one choice slot in a single solve. The cap is safe for every tail, and the limiting centrogram names its tail. No per-tail caps. |
| Uncertainty (`weight_delta`, `cg_delta`) | **Discrete endpoint model** (handoff §3). ±Δ means the two bounds only, not every value in between. |
| "Critical centrograms" | Every loading the solver had to evaluate one by one, i.e. **not eliminated** by a hull certificate. The limiting one is flagged. |
| Stack | Python + **FastAPI**. The pydantic models are the schema, the validation and the `/docs` page. |

## 2. Files

| File | Change |
|---|---|
| `app/api.py` (new, ~200 lines) | Pydantic models, request → `Problem` mapping, `POST /standard-calc`, serialisation, timing, error envelope and handlers, logging setup |
| `app/wb_core.py` | Add step-level `logging.debug` calls in `solve()` (§6). No behaviour change. The Streamlit app is unaffected because the default log level is WARNING. |
| `app/test_api.py` (new) | pytest + FastAPI `TestClient` (§8) |
| `app/requirements.txt` | Add `fastapi`, `uvicorn`, `httpx`, `pytest` |
| `examples/light_twin.json` (new) | The §3 request body |
| `README.md` | "Run the API" section (§9) |

## 3. Request

```json
{
  "envelope": [{"cg": 78, "weight": 1600}, {"cg": 79, "weight": 2400}, {"cg": 81, "weight": 3100},
               {"cg": 97.8, "weight": 3100}, {"cg": 83.3, "weight": 1600}],
  "tails": [{"id": "4X-AAA", "weight": 1800, "cg": 86.8}],
  "items": [
    {"name": "Pilot + front passenger", "weight": 360, "cg": 80.5},
    {"name": "Rear passenger 1", "weight": 160, "weight_delta": 50, "cg": 109, "cg_delta": 11},
    {"name": "Rear passenger 2", "weight": 160, "weight_delta": 50, "cg": 109, "cg_delta": 11},
    {"name": "Baggage nose", "weight": 80, "cg": 30, "optional": true},
    {"name": "Baggage aft", "weight": 120, "cg": 150, "optional": true}
  ],
  "fuel_flow": [{"weight": 0, "moment": 0}, {"weight": 400, "moment": 38000}, {"weight": 550, "moment": 51500}],
  "options": {"all_centrograms": false, "max_steps": 5000}
}
```

This is the Streamlit "Light twin" preset. Expected result: `fuel_cap` = 320 of 550, over 64 loadings.

| Field | Meaning | Validation (fails → `invalid_request`, 422) |
|---|---|---|
| `envelope[]` `{cg, weight}` | Polygon vertices, with CG on x and weight on y, in either orientation | 3–200 points. Convexity is checked by `Problem` (→ `invalid_input`). |
| `tails[]` `{id, weight, cg}` | Basic aircraft for each tail number | 1–50 tails, `weight > 0`, unique `id` |
| `items[]` `{name, weight, cg, weight_delta=0, cg_delta=0, optional=false}` | Payload item | 0–40 items, unique `name`, deltas ≥ 0, `weight - weight_delta ≥ 0` |
| `fuel_flow[]` `{weight, moment}` | **Cumulative** fuel weight and moment, in fill order. Linearly interpolated in (weight, moment). | 2–500 points. The first point must be exactly `(0, 0)` and weights must strictly increase. |
| `options.all_centrograms` | Also return the centrogram of every loading | Total loadings ≤ `ALL_CENTROGRAMS_LIMIT` (10,000), otherwise `too_many_centrograms` |
| `options.max_steps` | Branch-and-bound step budget (`solve(budget=…)`) | 1–100,000, default 5000 |

Units can be any consistent set. `moment` must be the true weight × arm, with no index scaling. Unusable or trapped fuel goes in as a non-optional item, because the fuel flow must start at `(0, 0)`.

## 4. Mapping the request to `wb_core.Problem`

All of this lives in `api.py`. There is no new solver code.

- `envelope` → `[(cg, weight)]`.
- `tails` → `basics=[(id, weight, cg)]`, which becomes slot 0. One solve covers every tail, which gives the fleet cap.
- `fixed=[]`.
- Each item becomes its list of endpoint options, passed as `items={name: [(w, x), ...]}` in request order:
  ```python
  ws = {it.weight - it.weight_delta, it.weight + it.weight_delta}   # sets: Δ=0 collapses to one value
  xs = {it.cg - it.cg_delta, it.cg + it.cg_delta}
  opts = [(w, x) for w in ws for x in xs] + ([(0, 0)] if it.optional else [])
  ```
  A non-optional item with zero deltas is a single-option slot, so it acts as a fixed load. Values strictly inside a ±Δ range are deliberately **not** checked (handoff §3).
- `fuel_flow` → `tanks`, as increments. wb_core rebuilds the cumulative path from them:
  `(f"fuel {i}", W_i - W_{i-1}, (M_i - M_{i-1}) / (W_i - W_{i-1}))`.

Errors from the solver are classified by which call raised them, never by parsing the message:

- `Problem(...)` raises `ValueError` for a non-convex envelope and similar input problems. This becomes error `invalid_input`.
- `solve()` raises `ValueError` only when a loading is outside the envelope at zero fuel. This becomes error `infeasible_at_zero_fuel`.

A cap of 0 must never be returned for an infeasible start (§5.2).

## 5. Response

### 5.1 Success (200)

```json
{
  "request_id": "9f1c…",
  "status": "ok",
  "fuel_cap": 320.0,
  "fuel_cap_upper_bound": 320.0,
  "full_fuel": 550.0,
  "limiting": { "…centrogram…" },
  "critical_centrograms": [ "…centrogram…" ],
  "all_centrograms": null,
  "stats": {"loadings": 64, "exact_evaluations": 4, "certificates": 13, "pruned": 64, "steps": 13},
  "timing": {"solve_ms": 3.1, "centrograms_ms": 0.6, "total_ms": 4.2},
  "error": null
}
```

`fuel_cap`, `full_fuel` and `stats` are the real light-twin values through the §4 mapping (checked with `wb.solve` and `wb.brute_force`). `timing` is illustrative.

Fields:

- **`status`**
  - `"ok"` when `result.complete` is true.
  - `"incomplete"` when the step budget ran out. Then `fuel_cap` is `null` and only `fuel_cap_upper_bound` is given. That value is the current U, set only by real loadings. Never report an unproven cap.
- **`limiting`**: the centrogram of `result.config`. It is `null` when nothing limits the fuel, i.e. when `fuel_cap == full_fuel`.
- **`critical_centrograms`**: one entry per config in `result.evaluated`, sorted by `cap` ascending, with the limiting one first.
- **`all_centrograms`**: present only if requested. It holds every config of `itertools.product(*(range(len(s)) for s in p.slot_states))`, using the same object and ordering. If `p.total > ALL_CENTROGRAMS_LIMIT`, return error `too_many_centrograms` before solving.
- **`stats`**: `p.total`, `result.exact_evals`, `result.certificates`, `result.pruned` and `len(result.trace)`.
- **`timing`**: measured with `time.perf_counter()`, in milliseconds, rounded to 0.1.
  - `solve_ms`: the `wb.solve(...)` call only, i.e. the algorithm's time to solve.
  - `centrograms_ms`: building `limiting`, `critical_centrograms` and `all_centrograms`. This is dominated by the enumeration when `all_centrograms` is on.
  - `total_ms`: from request received to response built, including validation and mapping. It is measured from the middleware's clock (§5.2).

### 5.2 Errors

Every failure returns the **same envelope** as a success. `status` is `"error"`, the result fields are `null`, and there is an `error` object. `request_id` and `timing.total_ms` are always present.

```json
{
  "request_id": "9f1c…",
  "status": "error",
  "fuel_cap": null, "fuel_cap_upper_bound": null, "full_fuel": null,
  "limiting": null, "critical_centrograms": null, "all_centrograms": null, "stats": null,
  "timing": {"solve_ms": null, "centrograms_ms": null, "total_ms": 0.8},
  "error": {
    "code": "invalid_request",
    "message": "Request body failed validation.",
    "details": [{"field": "fuel_flow.0.weight", "problem": "first fuel point must be (0, 0)"}]
  }
}
```

| `error.code` | HTTP | When | `details` |
|---|---|---|---|
| `invalid_request` | 422 | Pydantic validation failed (types, ranges, uniqueness, fuel-flow shape) | One `{field, problem}` per pydantic error: `loc` joined by `.`, plus `msg` |
| `invalid_input` | 422 | `wb.Problem(...)` raised `ValueError`, e.g. for a non-convex envelope | `[]`. The message is the `ValueError` text. |
| `infeasible_at_zero_fuel` | 422 | `wb.solve(...)` raised `ValueError`: some loading is outside the envelope before any fuel is loaded | `[]`. The message names the loading (wb_core's text). |
| `too_many_centrograms` | 422 | `all_centrograms` was requested and `p.total > ALL_CENTROGRAMS_LIMIT` | `[{"field": "options.all_centrograms", "problem": "64000 loadings > 10000"}]` |
| `internal_error` | 500 | Any other exception | `[]` and a generic message only. The traceback goes to the log (`logger.exception`) with the `request_id`, never to the client. |

`status: "incomplete"` (step budget exhausted) is **not** an error. It returns HTTP 200 with `error: null`, `fuel_cap: null` and `fuel_cap_upper_bound` set.

Implementation, all in `api.py`:

- `class ApiError(Exception)` with `code`, `message`, `http_status=422` and `details=[]`. The endpoint raises it for the domain cases.
- Exception handlers for `RequestValidationError`, `ApiError` and `Exception`. All three build the envelope above through one `error_response(code, message, status, details)` helper.
- An `@app.middleware("http")` that does four things:
  - creates the `request_id`,
  - sets the `ContextVar` (§6),
  - starts the `total_ms` clock,
  - adds an `X-Request-ID` header.

  It runs before validation, so validation errors also get a `request_id`. The endpoint and the handlers fill `timing.total_ms` from the clock in the `ContextVar`.
- A pydantic response model `CalcResponse` that declares every field, nullable where shown above, so `/docs` shows both shapes.

### 5.3 Centrogram object

```json
{
  "tail": "4X-AAA",
  "items": [{"name": "Rear passenger 1", "present": true, "weight": 210, "cg": 120},
            {"name": "Baggage nose", "present": false, "weight": 0, "cg": null}],
  "zero_fuel": {"weight": 2760, "cg": 92.1},
  "cap": 320.0,
  "binding": true,
  "limited_by_edge": {"from": {"cg": 97.8, "weight": 3100}, "to": {"cg": 83.3, "weight": 1600}},
  "cap_point": {"weight": 3080, "cg": 93.4},
  "points": [{"fuel": 0, "weight": 2760, "cg": 92.1}, {"fuel": 400, "weight": 3160, "cg": 92.5}]
}
```

The values above are illustrative. Each field is built from wb_core, for each config `cfg`:

- **`tail`**: `tails[cfg[0]].id`.
- **`items`**: item k corresponds to `p.slot_states[k+1][cfg[k+1]]`, which is `(w, m)`. Then `cg = m / w`, and the item is absent when `w == 0`.
- **`cap`**: `cap, seg, edge = p.cap(p.base_of(cfg))`, recomputed for each config, because `Result.evaluated` keeps the configs but not their caps. Mark this in the code with a `ponytail:` comment: there are few configs, so it is cheap; store caps in `Result` if it ever shows up in a profile.
- **`binding`**: true when `cfg == result.config`.
- **`limited_by_edge`**: from `p.envelope[edge]` to `p.envelope[(edge + 1) % n]`, or `null` when `edge is None`. Use `p.envelope`, not the request order, because `Problem` may reverse a clockwise envelope.
- **`cap_point`**: `p.centrogram(base, cap)[-1]`, given only when `cap < full_fuel`.
- **`points`**: `p.centrogram(base, p.full)`, with `fuel = W - base_W`.

A straight segment in (W, M) is a slight curve in (CG, W). Clients that plot the centrogram should add points between `points`.

## 6. Debug logging (one line per calculation step)

**In `wb_core.py`**, add `logger = logging.getLogger("wb_core")` and two debug calls:

- In the seed loop of `solve()`:
  ```python
  logger.debug("seed %s cap=%.2f lowers_U=%s", p.describe(cfg), cap, lowered)
  ```
- In the inner `log()` of `solve()`, with exactly the trace row fields:
  ```python
  logger.debug("step=%d depth=%d group=%r configs=%d hull_n=%d worst=%.4g vertex_worst=%.4g U=%.2f verdict=%r action=%r", …)
  ```

**In `api.py`**, use logger `wb_api`:

- DEBUG: the mapped options for each item, the fuel increments, and the envelope after normalisation.
- INFO, one line per request: `tails=… items=… loadings=… status=… fuel_cap=… steps=… solve_ms=… total_ms=…`.
- WARNING for every 422, with `error.code` and the message.
- ERROR with the traceback (`logger.exception`) for every 500.

**Request id:**

- The middleware (§5.2) creates a `request_id` (uuid4 hex) and sets it in a `ContextVar`.
- A `logging.Filter` on the root handler adds it to every record.
- It is returned in the response body and in the `X-Request-ID` header.
- Log format: `%(asctime)s %(levelname)s %(request_id)s %(name)s: %(message)s`, with `-` when no id is set.
- The level comes from the `LOG_LEVEL` environment variable, default `INFO`.

The endpoint is a plain `def`. FastAPI runs it in a worker thread and copies the context. `solve` runs in that same thread, so wb_core's log lines carry the `request_id`. Test 8 checks this.

Example at `LOG_LEVEL=DEBUG` (shape only):

```text
… DEBUG 9f1c… wb_core: seed {'Basic aircraft': '4X-AAA (1800 @ CG 86.8)', …} cap=550.00 lowers_U=False
… DEBUG 9f1c… wb_core: step=1 depth=0 group='all choices' configs=64 hull_n=… worst=… vertex_worst=… U=… verdict='fails only inside a hull edge (possibly artificial)' action='…'
… INFO  9f1c… wb_api: tails=1 items=5 loadings=64 status=ok fuel_cap=320.00 steps=13 solve_ms=3.1 total_ms=4.2
```

## 7. Statelessness and limits

- Each request builds its own `Problem`. There is no module-level mutable state besides constants and the logger, so it is safe to run several uvicorn workers.
- The work is CPU-bound and synchronous, so a long request occupies one worker. Its length is bounded by `max_steps` and the list-length limits in §3.
- Not included: auth, rate limiting, `/health`, an async job queue, and returning the trace in the response (the logs cover it). Add these if the service is deployed beyond a demo.

## 8. Tests (`app/test_api.py`, pytest)

1. **Light twin** (the §3 request): `status ok`, `fuel_cap == 320`, equal to `wb.solve(wb.problem_from_rows(**wb.preset_light_twin())).cap`, `stats.loadings == 64`, and 4 critical centrograms.
2. **Toy preset as a request**:
   - envelope `(10,80),(14,80),(20,200),(10,200)`,
   - tail `100 @ 14.9`,
   - optional item `80 @ 24`,
   - fuel `(0,0),(15,150)`.

   Expect `fuel_cap == 15` and 2 critical centrograms.
3. **Two tails**: `fuel_cap` equals the minimum of the caps for each tail sent alone, and `limiting.tail` is the tail with the lower cap.
4. **`all_centrograms: true`** on the light twin: 64 entries, their minimum `cap == fuel_cap`, and every critical config appears among them.
5. **Errors**. Each case asserts the HTTP status, `status == "error"`, `error.code`, a non-empty `request_id`, and that `timing.total_ms` is present.
   - `invalid_request`:
     - `fuel_flow` does not start at `(0,0)`,
     - fuel weights are not increasing,
     - `weight_delta > weight`,
     - duplicate item names,
     - `tails` is missing.
   - `invalid_input`: non-convex envelope.
   - `infeasible_at_zero_fuel`: a tail far outside the envelope.
   - `too_many_centrograms`: 16 optional items with `all_centrograms`.
   - `internal_error`: monkeypatch `wb.solve` to raise `RuntimeError`. The response must not contain the traceback text.
6. **Incomplete**: light twin with `max_steps: 1` returns HTTP 200, `status incomplete`, `error null`, `fuel_cap null` and `fuel_cap_upper_bound ≥ 320`. (Budgets 1–3 were checked to stop early.)
7. **Timing**: on success `0 < solve_ms ≤ total_ms`, and `centrograms_ms` is a number.
8. **Logging**: with `caplog` at DEBUG, the light-twin request emits one `wb_core` record per step (13) plus the seed records. All of them carry the response's `request_id`.
9. **Solver self-check**: `python app/wb_core.py` still passes, because the logging change does not alter behaviour.

## 9. Run

```bash
pip install -r app/requirements.txt
LOG_LEVEL=DEBUG uvicorn api:app --app-dir app --port 8000     # /docs for the interactive schema
curl -s localhost:8000/standard-calc -H 'content-type: application/json' -d @examples/light_twin.json
pytest app/test_api.py
```
