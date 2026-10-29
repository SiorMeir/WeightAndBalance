"""Parametrised robust fuel-cap solver: branch-and-bound over convex hulls.

Educational only, not dispatch software.  Everything is in (weight W, moment M)
space, where loads add.  Self-check:  python app/wb_core.py
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from math import prod
from pathlib import Path

EPS = 1e-7
REL_TOL = 1e-9  # certificate slack, relative to the magnitude of the q terms
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "boeing_707_124_demo.json"

State = tuple[float, float]  # (weight, moment)


def add(a: State, b: State) -> State:
    return (a[0] + b[0], a[1] + b[1])


def interp(a: State, b: State, t: float) -> State:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def cross(o: State, a: State, b: State) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def hull(points) -> list[State]:
    """Monotone-chain convex hull in (W, M); collinear points dropped."""
    pts = sorted({(round(w, 9), round(m, 9)) for w, m in points})
    if len(pts) <= 1:
        return pts
    half = []
    for seq in (pts, pts[::-1]):
        chain: list[State] = []
        for p in seq:
            while len(chain) >= 2 and cross(chain[-2], chain[-1], p) <= EPS:
                chain.pop()
            chain.append(p)
        half.append(chain[:-1])
    return half[0] + half[1]


def roots(a: float, b: float, c: float) -> list[float]:
    if abs(a) < EPS:
        return [] if abs(b) < EPS else [-c / b]
    d = b * b - 4 * a * c
    if d < -EPS:
        return []
    r = max(0.0, d) ** 0.5
    return [(-b - r) / (2 * a), (-b + r) / (2 * a)]


def q(e: tuple, s: State) -> float:
    """Envelope-edge constraint q(W, M) = a*M + b*W^2 - c*W <= 0 (safe)."""
    dx, dy, k = e
    return -(dx * s[0] ** 2 + k * s[0] - dy * s[1])


def seg_q(e: tuple, s0: State, s1: State) -> tuple[float, float, float]:
    """q along s0 -> s1 as A t^2 + B t + C."""
    dx, dy, k = e
    dw, dm = s1[0] - s0[0], s1[1] - s0[1]
    w, m = s0
    return (-dx * dw * dw, -(2 * dx * w * dw + k * dw - dy * dm), -(dx * w * w + k * w - dy * m))


@dataclass
class Cert:
    ok: bool
    worst: float  # max q over swept-hull edges (incl. interior stationary points)
    vertex_worst: float  # max q over swept-hull vertices only
    arg: tuple | None  # (envelope edge, weight at the maximum)
    polys: list = field(default_factory=list)
    hull_n: int = 0


@dataclass
class Result:
    cap: float
    full: float
    config: tuple | None
    binding: tuple | None  # (fuel segment, envelope edge) of the limiting config
    trace: list
    total: int
    exact_evals: int
    certificates: int
    pruned: int
    complete: bool
    evaluated: list
    pruned_groups: list = field(default_factory=list)  # groups certified safe, never enumerated


class Problem:
    def __init__(self, envelope, basics, fixed, items, tanks):
        """envelope [(cg, W)]; basics [(name, W, cg)]; fixed [(name, W, station)];
        items {name: [(W, station)]}; tanks [(name, W, arm)] in fill order."""
        env = [(float(x), float(w)) for x, w in envelope]
        if len(env) < 3:
            raise ValueError("Envelope needs at least 3 vertices.")
        pairs = list(zip(env, env[1:] + env[:1]))
        if sum(x0 * w1 - x1 * w0 for (x0, w0), (x1, w1) in pairs) < 0:
            env.reverse()
            pairs = list(zip(env, env[1:] + env[:1]))
        vecs = [(x1 - x0, w1 - w0) for (x0, w0), (x1, w1) in pairs]
        # relative tolerance: published envelopes carry digitisation noise; the
        # solver uses the intersection of the edge half-planes either way
        if any(a[0] * b[1] - a[1] * b[0] < -1e-4 * (a[0] ** 2 + a[1] ** 2) ** 0.5 * (b[0] ** 2 + b[1] ** 2) ** 0.5 for a, b in zip(vecs, vecs[1:] + vecs[:1])):
            raise ValueError("Envelope must be a simple convex polygon (split non-convex envelopes).")
        self.envelope = env
        # edge (dx, dy, k): inside <=> q = -(dx*W^2 + k*W - dy*M) <= 0, k = dy*x0 - dx*w0
        self.edges = [(dx, dy, dy * x0 - dx * w0) for ((x0, w0), _), (dx, dy) in zip(pairs, vecs)]
        if not basics or any(w <= 0 for _, w, _ in basics):
            raise ValueError("Provide at least one basic-aircraft case with positive weight.")
        if not tanks or any(w <= 0 for _, w, _ in tanks):
            raise ValueError("Provide at least one fuel tank with positive capacity.")
        fixed_state = (0.0, 0.0)
        for _, w, x in fixed:
            fixed_state = add(fixed_state, (w, w * x))
        self.slot_names = ["Basic aircraft"] + list(items)
        self.slot_states = [[add(fixed_state, (w, w * cg)) for _, w, cg in basics]]
        self.slot_labels = [[f"{n} ({w:g} @ CG {cg:g})" for n, w, cg in basics]]
        for opts in items.values():
            opts = list(dict.fromkeys((float(w), float(x)) for w, x in opts))
            self.slot_states.append([(w, w * x) for w, x in opts])
            self.slot_labels.append([f"{w:g} @ {x:g}" for w, x in opts])
        self.fuel = [(0.0, 0.0)]
        for _, w, arm in tanks:
            self.fuel.append(add(self.fuel[-1], (w, w * arm)))
        self.full = self.fuel[-1][0]
        self.total = prod(len(s) for s in self.slot_states)

    # ---- single real configuration -------------------------------------
    def base_of(self, cfg) -> State:
        s = (0.0, 0.0)
        for states, i in zip(self.slot_states, cfg):
            s = add(s, states[i])
        return s

    def describe(self, cfg) -> dict:
        return {n: lbl[i] for n, lbl, i in zip(self.slot_names, self.slot_labels, cfg)}

    def inside(self, s: State) -> bool:
        return all(q(e, s) <= EPS * s[0] for e in self.edges)

    def worst_edge(self, s: State) -> int:
        return max(range(len(self.edges)), key=lambda i: q(self.edges[i], s) / (s[0] * (abs(self.edges[i][0]) + abs(self.edges[i][1]) + EPS)))

    def breaks(self, s0: State, s1: State) -> list[float]:
        ts = [0.0, 1.0]
        for e in self.edges:
            ts += [r for r in roots(*seg_q(e, s0, s1)) if EPS < r < 1 - EPS]
        return sorted({round(t, 12) for t in ts})

    def cap(self, base: State):
        """(max safe fuel prefix, fuel segment, envelope edge) of one real configuration."""
        if not self.inside(add(base, self.fuel[0])):
            raise ValueError("outside the envelope at zero fuel")
        for i, (f0, f1) in enumerate(zip(self.fuel, self.fuel[1:])):
            s0, s1 = add(base, f0), add(base, f1)
            ts = self.breaks(s0, s1)
            for left, right in zip(ts, ts[1:]):
                if not self.inside(interp(s0, s1, (left + right) / 2)):
                    return f0[0] + left * (f1[0] - f0[0]), i, self.worst_edge(interp(s0, s1, (left + right) / 2))
            if not self.inside(s1):
                return f0[0], i, self.worst_edge(s1)
        return self.full, None, None

    def centrogram(self, base: State, upto: float) -> list[State]:
        pts = [add(base, self.fuel[0])]
        for f0, f1 in zip(self.fuel, self.fuel[1:]):
            if f0[0] >= upto - EPS:
                break
            pts.append(add(base, f1 if f1[0] <= upto else interp(f0, f1, (upto - f0[0]) / (f1[0] - f0[0]))))
        return pts

    # ---- groups of configurations ---------------------------------------
    def group_hull(self, g) -> list[State]:
        h = [(0.0, 0.0)]
        for states, idx in zip(self.slot_states, g):
            h = hull(add(a, states[i]) for a in h for i in idx)
        return h

    def max_violation(self, poly: list[State]):
        pts = poly if len(poly) > 1 else poly * 2
        worst, arg = float("-inf"), None
        for ei, e in enumerate(self.edges):
            for s0, s1 in zip(pts, pts[1:] + pts[:1]):
                a, b, c = seg_q(e, s0, s1)
                ts = [0.0, 1.0]
                if a < -EPS and EPS < -b / (2 * a) < 1 - EPS:
                    ts.append(-b / (2 * a))
                for t in ts:
                    v = a * t * t + b * t + c
                    if v > worst:
                        worst, arg = v, (ei, s0[0] + t * (s1[0] - s0[0]))
        return worst, arg

    def certify(self, g, upto: float) -> Cert:
        base_hull = self.group_hull(g)
        worst, vworst, arg, mag, polys = float("-inf"), float("-inf"), None, 0.0, []
        for f0, f1 in zip(self.fuel, self.fuel[1:]):
            if f0[0] >= upto - EPS:
                break
            end = f1 if f1[0] <= upto else interp(f0, f1, (upto - f0[0]) / (f1[0] - f0[0]))
            swept = hull(add(b, f) for b in base_hull for f in (f0, end))
            polys.append(swept)
            w, a = self.max_violation(swept)
            if w > worst:
                worst, arg = w, a
            for e in self.edges:
                vworst = max(vworst, *(q(e, v) for v in swept))
                mag = max(mag, *(abs(e[0]) * v[0] ** 2 + abs(e[2]) * v[0] + abs(e[1] * v[1]) for v in swept))
        return Cert(worst <= REL_TOL * mag, worst, vworst, arg, polys, len(base_hull))

    def score(self, e, wstar, s: State) -> float:
        """Linearisation of q at weight wstar: the hull vertex maximising it is a real combination."""
        dx, dy, k = self.edges[e]
        return -(2 * dx * wstar + k) * s[0] + dy * s[1]

    def witness(self, g, e, wstar):
        return tuple(max(idx, key=lambda i: self.score(e, wstar, st[i])) for st, idx in zip(self.slot_states, g))

    def split(self, g, e, wstar):
        """Halve the choices of the slot whose options differ most for the violated constraint."""
        best, spread = None, -1.0
        for slot, (st, idx) in enumerate(zip(self.slot_states, g)):
            if len(idx) > 1:
                sc = [self.score(e, wstar, st[i]) for i in idx]
                if max(sc) - min(sc) > spread:
                    best, spread = slot, max(sc) - min(sc)
        st = self.slot_states[best]
        ordered = sorted(g[best], key=lambda i: self.score(e, wstar, st[i]))
        half = len(ordered) // 2
        return [g[:best] + (tuple(part),) + g[best + 1:] for part in (ordered[:half], ordered[half:])]

    def group_text(self, g) -> str:
        parts = [f"{n} {len(idx)}/{len(st)}" for n, idx, st in zip(self.slot_names, g, self.slot_states) if len(idx) < len(st)]
        return ", ".join(parts) or "all choices"

    def seeds(self, g):
        """Real extreme loadings: heaviest, lightest, most aft, most forward."""
        arm = lambda s: s[1] / s[0] if s[0] else 0.0  # absent optional items have no arm
        keys = [lambda s: s[0], lambda s: -s[0], arm, lambda s: -arm(s)]
        return list(dict.fromkeys(tuple(max(idx, key=lambda i: k(st[i])) for st, idx in zip(self.slot_states, g)) for k in keys))


def solve(p: Problem, budget: int = 5000) -> Result:
    root = tuple(tuple(range(len(s))) for s in p.slot_states)
    evaluated: dict = {}

    def evaluate(cfg):
        if cfg not in evaluated:
            try:
                evaluated[cfg] = p.cap(p.base_of(cfg))
            except ValueError as err:
                raise ValueError(f"Configuration {p.describe(cfg)} is {err}.") from None
        return evaluated[cfg]

    U, best, binding = p.full, None, None

    def lower(cfg) -> bool:
        nonlocal U, best, binding
        c = evaluate(cfg)
        if c[0] < U - EPS:
            U, best, binding = c[0], cfg, c[1:]
            return True
        return False

    trace, certs, pruned, pruned_groups = [], 0, 0, []

    def log(depth, g, cert, verdict, action):
        trace.append(dict(step=len(trace) + 1, depth=depth, group=p.group_text(g), configs=prod(len(i) for i in g),
                          hull_n=cert.hull_n, worst=cert.worst, vertex_worst=cert.vertex_worst, tol=None,
                          verdict=verdict, action=action, U=U, polys=cert.polys, ok=cert.ok))

    seed_note = []
    for cfg in p.seeds(root):
        seed_note.append((cfg, evaluate(cfg)[0], lower(cfg)))
    stack, complete = [(root, 0)], True
    while stack:
        if len(trace) >= budget:
            complete = False
            break
        g, depth = stack.pop()
        while True:
            cert = p.certify(g, U)
            certs += 1
            n = prod(len(i) for i in g)
            if cert.ok:
                pruned += n
                pruned_groups.append(g)
                log(depth, g, cert, "certified", f"pruned {n} configs without enumerating them")
                break
            where = "at a hull vertex (a real combination)" if cert.vertex_worst > 0 else "only inside a hull edge (possibly artificial)"
            if n == 1:
                cfg = tuple(i[0] for i in g)
                lower(cfg)
                log(depth, g, cert, "fails, single config", f"exact evaluation, cap {evaluate(cfg)[0]:,.2f}")
                break
            e, wstar = cert.arg
            w = p.witness(g, e, wstar)
            if lower(w):
                log(depth, g, cert, f"fails {where}", f"real witness lowers U to {U:,.2f}; re-certify")
                continue
            halves = p.split(g, e, wstar)
            log(depth, g, cert, f"fails {where}", "witness does not lower U; split group in two")
            stack.extend((h, depth + 1) for h in reversed(halves))
            break
    return Result(U, p.full, best, binding, trace, p.total, len(evaluated), certs, pruned, complete, list(evaluated), pruned_groups)


def brute_force(p: Problem) -> float:
    return min(p.cap(p.base_of(cfg))[0] for cfg in itertools.product(*(range(len(s)) for s in p.slot_states)))


def sample_poly(poly: list[State], n: int = 24) -> list[tuple[float, float]]:
    """Hull outline in (CG, W) for plotting, with curved edges sampled."""
    pts = poly if len(poly) > 1 else poly * 2
    out = [interp(a, b, j / n) for a, b in zip(pts, pts[1:] + pts[:1]) for j in range(n)]
    return [(m / w, w) for w, m in out + out[:1]]


# ---- presets (table rows, as edited in the app) --------------------------
def preset_707() -> dict:
    d = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    ill = d["illustrative_inputs"]
    pil, pay, pax, basic = ill["stationary_pilots"], ill["mandatory_center_payload"], ill["roaming_passengers"], ill["basic_aircraft"]
    tanks = sorted(d["fuel_tanks"], key=lambda t: t["fuel_sequence_order"])
    return dict(
        envelope=d["envelope"]["calculation_points_cg_station_in_gross_weight_lb"],
        basics=[("Basic aircraft", basic["weight_lb"], basic["cg_station_in"])],
        fixed=[("Pilots", pil["count"] * pil["weight_each_lb"], pil["station_in"]), ("Center payload", pay["weight_lb"], pay["station_in"])],
        items=[(f"Passenger {i}", w, x) for i in range(1, pax["count"] + 1) for w in pax["weight_endpoints_lb"] for x in pax["station_endpoints_in"]],
        tanks=[(t["name"], t["usable_weight_lb"], t["moment_arm_in"]) for t in tanks],
    )


def preset_toy() -> dict:
    """Sloped aft limit x <= 10 + 0.05 W: the hull fails artificially, then B&B refines."""
    return dict(
        envelope=[(10, 80), (14, 80), (20, 200), (10, 200)],
        basics=[("Basic aircraft", 100, 14.9)],
        fixed=[],
        items=[("Optional cargo", 0, 0), ("Optional cargo", 80, 24)],
        tanks=[("Tank", 15, 10)],
    )


def preset_light_twin() -> dict:
    """Illustrative light twin (lb / in), sloped fwd and aft limits. Numbers are fragile: rounding them
    makes the root hull certify immediately."""
    return dict(
        envelope=[(78, 1600), (79, 2400), (81, 3100), (97.8, 3100), (83.3, 1600)],
        basics=[("Basic aircraft", 1800, 86.8)],
        fixed=[("Pilot + front passenger", 360, 80.5)],
        items=[(f"Rear passenger {i}", w, x) for i in (1, 2) for w in (110, 210) for x in (98, 120)]
        + [("Baggage nose", 0, 0), ("Baggage nose", 80, 30), ("Baggage aft", 0, 0), ("Baggage aft", 120, 150)],
        tanks=[("Main", 400, 95), ("Aux", 150, 90)],
    )


def problem_from_rows(envelope, basics, fixed, items, tanks) -> Problem:
    grouped: dict = {}
    for name, w, x in items:
        grouped.setdefault(str(name), []).append((w, x))
    return Problem(envelope, basics, fixed, grouped, tanks)


if __name__ == "__main__":
    for name, preset in (("707", preset_707()), ("toy", preset_toy()), ("light twin", preset_light_twin())):
        p = problem_from_rows(**preset)
        r = solve(p)
        assert r.complete and abs(r.cap - brute_force(p)) < 1e-6, name
        print(f"{name}: cap {r.cap:,.2f} of {r.full:,.2f}; {r.total} configs, {r.exact_evals} exact evals, {r.certificates} certificates, {len(r.trace)} steps")
    toy = solve(problem_from_rows(**preset_toy()))
    assert toy.cap == 15 and not toy.trace[0]["ok"] and "artificial" in toy.trace[0]["verdict"], "toy should fail artificially at the root"
    print("ok")
