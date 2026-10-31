# Weight and Balance

An educational Python demonstration of longitudinal aircraft weight-and-balance analysis using convex-hull pruning in weight–moment coordinates.

The current Boeing 707 example enumerates four passenger endpoint choices, draws every centrogram, and distinguishes the configurations that determine the robust fuel cap from those safely pruned by a hull certificate.

Read [the algorithm guide](ALGORITHMS.md) for the input model, certification math,
pseudocode, complexity analysis, and a Lean 4 building-block example. The same
guide is presented as a readable visual overview on the project's GitHub Pages
home page.

## Run

```powershell
python demo/boeing_707_demo.py
```

The script reads its complete 707 input model from [`data/boeing_707_124_demo.json`](data/boeing_707_124_demo.json), then regenerates the SVG visualization, JSON summary, and full centrogram CSV export in `demo/output/`.

## Run the API

The stateless API accepts a complete calculation in each request. It is an
educational demonstration only, not approved for aircraft loading, dispatch, or
flight operations.

```bash
pip install -r app/requirements.txt
LOG_LEVEL=DEBUG uvicorn api:app --app-dir app --port 8000
```

Open `http://localhost:8000/docs` for the interactive schema. The following
examples assume the server is running on port 8000.

Calculate the Light Twin example:

```bash
curl -sS http://localhost:8000/standard-calc \
  -H 'content-type: application/json' \
  --data-binary @examples/light_twin.json
```

Inspect the response headers, including `X-Request-ID`:

```bash
curl -sS -i http://localhost:8000/standard-calc \
  -H 'content-type: application/json' \
  --data-binary @examples/light_twin.json
```

See the structured validation-error envelope with an invalid request:

```bash
curl -sS -i http://localhost:8000/standard-calc \
  -H 'content-type: application/json' \
  --data '{"envelope": []}'
```

Run the API test suite with:

```bash
pytest app/test_api.py
```

## Safety

This repository is an algorithm demonstration only. Its aircraft inputs include deliberately illustrative values and must not be used for real aircraft loading, dispatch, or flight operations. See [the demo notes](demo/README.md) for source provenance and modelling limits.
