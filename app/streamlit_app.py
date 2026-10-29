"""Streamlit demo: robust fuel cap via convex-hull branch-and-bound.

    pip install -r app/requirements.txt
    streamlit run app/streamlit_app.py

Educational only. Not approved for aircraft loading, dispatch or flight.
"""

from __future__ import annotations

import itertools
from math import prod

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import wb_core as wb

st.set_page_config(page_title="Weight & balance fuel cap", layout="wide")

PRESETS = {
    "Boeing 707-124 (demo data)": wb.preset_707,
    "Sloped aft limit (hull fails artificially)": wb.preset_toy,
    "Light twin (illustrative, 64 loadings)": wb.preset_light_twin,
}
COLUMNS = {
    "envelope": ["CG", "Weight"],
    "basics": ["Case", "Weight", "CG"],
    "fixed": ["Load", "Weight", "Station"],
    "items": ["Item", "Weight", "Station"],
    "tanks": ["Tank (fill order)", "Weight", "Arm"],
}

st.title("Robust fuel cap: weight & balance demo")
st.warning("Educational demonstration. Not approved for aircraft loading, dispatch or flight operations.")

preset = st.sidebar.selectbox("Preset", list(PRESETS))
data = PRESETS[preset]()
st.sidebar.markdown(
    "Units are yours to choose (lb/in, kg/m, ...) but must be consistent. "
    "Moment = weight × station. Edit any table; the solver re-runs on every change."
)
crosscheck = st.sidebar.checkbox("Cross-check with brute force (≤ 5,000 configs)", value=True)

tab_in, tab_solve, tab_flow = st.tabs(["1 · Inputs", "2 · Result", "3 · Calculation process"])


def table(key: str, title: str, help_text: str, height: int = 230):
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(help_text)
        return st.data_editor(pd.DataFrame(data[key], columns=COLUMNS[key]), num_rows="dynamic", key=f"{preset}/{key}", width="stretch", height=height)


with tab_in:
    c1, c2, c3 = st.columns(3)
    with c1:
        env_df = table("envelope", "Envelope", "(CG, weight) vertices around a convex polygon.")
        tnk_df = table("tanks", "Fuel tanks", "Fill order. Burn-down is the reverse.", 200)
    with c2:
        bas_df = table("basics", "Basic aircraft cases", "Real cases, e.g. AFT-HEAVY / FWD-LIGHT. Exactly one applies.")
        fix_df = table("fixed", "Fixed loads", "Always present: crew, mandatory payload.", 200)
    with c3:
        itm_df = table("items", "Variable items", "One row per choice. Rows with the same Item are alternatives; add a weight-0 row to make an item optional.", 500)

def rows(df):
    return [tuple(r) for r in df.dropna().itertuples(index=False)]

try:
    problem = wb.problem_from_rows(rows(env_df), rows(bas_df), rows(fix_df), rows(itm_df), rows(tnk_df))
    result = wb.solve(problem)
except ValueError as err:
    st.error(str(err))
    st.stop()


with tab_in:
    st.caption(f"{result.total:,} real configurations = {' × '.join(str(len(s)) for s in problem.slot_states)} choices ({', '.join(problem.slot_names)}).")

with tab_solve:
    lost = result.full - result.cap
    m = st.columns(5)
    m[0].metric("Robust fuel cap", f"{result.cap:,.1f}")
    m[1].metric("Full fuel", f"{result.full:,.1f}")
    m[2].metric("Fuel not usable", f"{lost:,.1f}")
    m[3].metric("Real configurations", f"{result.total:,}")
    m[4].metric("Exact evaluations", f"{result.exact_evals:,}", help="Configurations solved individually; everything else was pruned by hull certificates.")
    if not result.complete:
        st.error("Step budget exhausted: the cap shown is an upper bound, not proven.")
    if result.config is None:
        st.success("No loading combination limits fuel: the full tank content is safe for every configuration.")
    else:
        seg, edge = result.binding
        st.info(f"Limited by a real configuration, which leaves the envelope in fuel segment {seg + 1} through envelope edge {edge + 1}.")
        st.table(pd.Series(problem.describe(result.config), name="Limiting choice").to_frame())
    if crosscheck and result.total <= 5000:
        bf = wb.brute_force(problem)
        (st.success if abs(bf - result.cap) < 1e-6 else st.error)(f"Brute force over all {result.total:,} configurations gives {bf:,.2f}.")

    # ---- envelope / centrogram plot -------------------------------------
    MAX_DRAW = 5000
    for key in ("show_all", "show_elim"):
        st.session_state.setdefault(key, False)

    def toggle(key):
        st.session_state[key] = not st.session_state[key]

    b1, b2, _ = st.columns([1, 1, 3])
    b1.button(("Hide" if st.session_state.show_all else "Show") + " all centrograms", on_click=toggle, args=("show_all",), width="stretch")
    b2.button(("Hide" if st.session_state.show_elim else "Show") + " eliminated centrograms", on_click=toggle, args=("show_elim",), width="stretch")

    def draw(fig, configs, n, upto, name, **style):
        """One trace for many centrograms (None-separated); thinned if there are too many."""
        stride, xs, ys = max(1, -(-n // MAX_DRAW)), [], []
        for cfg in itertools.islice(configs, 0, None, stride):
            pts = problem.centrogram(problem.base_of(cfg), upto)
            xs += [p[1] / p[0] for p in pts] + [None]
            ys += [p[0] for p in pts] + [None]
        fig.add_scatter(x=xs, y=ys, mode="lines", name=f"{name} ({n:,})", hoverinfo="skip", **style)
        if stride > 1:
            st.caption(f"{name}: showing every {stride}th of {n:,} centrograms.")

    fig = go.Figure()
    env = problem.envelope + problem.envelope[:1]
    fig.add_scatter(x=[x for x, _ in env], y=[w for _, w in env], fill="toself", fillcolor="rgba(22,124,69,.12)",
                    line=dict(color="#167c45", width=3), name="Envelope")
    root = tuple(tuple(range(len(s))) for s in problem.slot_states)
    if result.cap > wb.EPS:
        for k, poly in enumerate(problem.certify(root, result.cap).polys):
            outline = wb.sample_poly(poly)
            fig.add_scatter(x=[x for x, _ in outline], y=[w for _, w in outline], fill="toself", fillcolor="rgba(39,125,161,.10)",
                            line=dict(color="rgba(39,125,161,.5)", width=1, dash="dot"), name="Hull of all configs up to the cap",
                            legendgroup="hull", showlegend=k == 0, hoverinfo="skip")
    if st.session_state.show_all:
        everything = lambda: itertools.product(*(range(len(s)) for s in problem.slot_states))
        draw(fig, everything(), result.total, result.full, "Full fuel, unused part", line=dict(color="#94a3b8", width=1, dash="dash"), opacity=0.5)
        draw(fig, everything(), result.total, result.cap, "All centrograms up to the cap", line=dict(color="#277da1", width=1.5), opacity=0.45)
    if st.session_state.show_elim:
        eliminated = itertools.chain.from_iterable(itertools.product(*g) for g in result.pruned_groups)
        draw(fig, eliminated, result.pruned, result.cap, "Eliminated by a hull certificate", line=dict(color="#2a9d8f", width=2), opacity=0.8)
    if result.config is not None:
        pts = problem.centrogram(problem.base_of(result.config), result.cap)
        fig.add_scatter(x=[p[1] / p[0] for p in pts], y=[p[0] for p in pts], mode="lines+markers", line=dict(color="#e05d2f", width=3), name="Limiting configuration")
        fig.add_scatter(x=[pts[-1][1] / pts[-1][0]], y=[pts[-1][0]], mode="markers", marker=dict(color="#e05d2f", size=13, symbol="x"), name="Fuel cap reached")
    fig.update_layout(xaxis_title="CG station", yaxis_title="Gross weight", height=640, legend=dict(orientation="h", y=-0.15), margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")


# ===================================================================
with tab_flow:
    st.markdown("### Flow")
    st.graphviz_chart(
        """digraph {
        rankdir=TB; node [shape=box, style="rounded,filled", fillcolor="#eef2f7", fontname="Helvetica"]; edge [fontname="Helvetica"];
        inputs [label="1. Inputs\\nenvelope · basic cases · fixed loads · items · fuel tanks"];
        space  [label="2. Work in (W, M) space\\nweights and moments add; fuel path is a polyline"];
        edges  [label="3. One constraint per envelope edge\\nq = a·M + b·W² − c·W ≤ 0"];
        seed   [label="4. Seed U = min cap of real extreme loadings\\n(heaviest, lightest, most aft, most forward)"];
        pop    [label="5. Pop a group of configurations"];
        cert   [shape=diamond, fillcolor="#fff4d6", label="Swept hull\\nsatisfies every q ≤ 0\\nup to U?"];
        prune  [fillcolor="#d8f3dc", label="Prune: whole group is safe up to U"];
        single [shape=diamond, fillcolor="#fff4d6", label="Group is one\\nreal configuration?"];
        exact  [fillcolor="#fde2d3", label="Solve it exactly, lower U if it binds"];
        wit    [fillcolor="#fde2d3", label="Real witness = hull vertex maximising the\\nlinearised violated q; lower U if it binds"];
        split  [label="Split the most influential item's choices\\nin two; push both groups"];
        done   [fillcolor="#d8f3dc", label="6. Queue empty: robust cap = U"];
        inputs -> space -> edges -> seed -> pop -> cert;
        cert -> prune [label="yes"]; cert -> single [label="no"];
        single -> exact [label="yes"]; single -> wit [label="no"];
        wit -> cert [label="U lowered: re-certify"]; wit -> split [label="U unchanged"];
        prune -> pop [label="next group"]; exact -> pop; split -> pop; pop -> done [label="empty"];
    }"""
    )
    st.caption("A hull failure is only 'unresolved': U is lowered exclusively by real configurations, so an artificial mixture inside a hull never reduces the fuel cap.")

    st.markdown("### 1-2. Inputs in (W, M) space")
    st.write(f"**{result.total:,}** real configurations = {' × '.join(str(len(s)) for s in problem.slot_states)} choices per slot "
             f"({', '.join(problem.slot_names)}).")
    st.dataframe(pd.DataFrame(
        [(f"after {n}" if i else "empty", f0[0], f0[1], f0[1] / f0[0] if f0[0] else None)
         for i, (n, f0) in enumerate(zip(["empty"] + [t[0] for t in rows(tnk_df)], problem.fuel))],
        columns=["Fuel waypoint", "Cumulative weight", "Cumulative moment", "Fuel arm"]), hide_index=True, width="stretch")

    st.markdown("### 3. Envelope edges to constraints")
    st.markdown(
        "An edge is the half-plane `a·CG + b·W ≤ c`. With `CG = M/W` and `W > 0`: **`q(W, M) = a·M + b·W² − c·W ≤ 0`**. "
        "Safe means every `q ≤ 0`. Along a straight (W, M) segment `q(t) = A·t² + B·t + C` is a parabola, "
        "so its maximum is at an end or at `t = −B/2A` when `A < 0`."
    )
    st.dataframe(pd.DataFrame(
        [(i + 1, f"({a[0]:g}, {a[1]:g}) → ({b[0]:g}, {b[1]:g})", e[1], -e[0], e[2],
          "convex: vertex check exact" if e[0] <= 0 else "concave: edge interior can fail artificially")
         for i, (e, a, b) in enumerate(zip(problem.edges, problem.envelope, problem.envelope[1:] + problem.envelope[:1]))],
        columns=["Edge", "From → to (CG, W)", "a", "b", "c", "Behaviour"]), hide_index=True, width="stretch")

    st.markdown("### 4-5. Branch and bound")
    st.write(f"Fuel limit starts at full fuel = **{result.full:,.2f}**. Real extreme loadings are solved exactly to seed `U`:")
    seeds = problem.seeds(tuple(tuple(range(len(s))) for s in problem.slot_states))
    st.dataframe(pd.DataFrame([{**problem.describe(c), "Cap": problem.cap(problem.base_of(c))[0]} for c in seeds]), hide_index=True, width="stretch")
    st.write(f"After seeding: **U = {min(problem.cap(problem.base_of(c))[0] for c in seeds):,.2f}**.")
    trace = pd.DataFrame(result.trace).drop(columns=["polys", "ok", "tol"])
    trace.columns = ["Step", "Depth", "Group (choices kept)", "Configs", "Hull vertices", "Worst q (hull edges)", "Worst q (vertices)", "Verdict", "Action", "U after"]
    st.dataframe(trace, hide_index=True, width="stretch")
    st.write(f"{result.certificates} certificates, {result.exact_evals} exact evaluations, {result.pruned:,} of {result.total:,} configurations pruned by certificate.")

    st.markdown("#### Inspect a step")
    step = st.select_slider("Step", options=[t["step"] for t in result.trace], value=1)
    t = result.trace[step - 1]
    st.write(f"**Step {step}:** group `{t['group']}` ({t['configs']:,} configs), {t['verdict']}. Worst `q` = {t['worst']:.4g}. {t['action']}.")
    fig = go.Figure()
    env = problem.envelope + problem.envelope[:1]
    fig.add_scatter(x=[x for x, _ in env], y=[w for _, w in env], line=dict(color="#167c45", width=3), name="Envelope")
    for k, poly in enumerate(t["polys"]):
        outline = wb.sample_poly(poly)
        color = "39,125,161" if t["ok"] else "224,93,47"
        fig.add_scatter(x=[x for x, _ in outline], y=[w for _, w in outline], fill="toself", fillcolor=f"rgba({color},.15)",
                        line=dict(color=f"rgb({color})", width=1.5), name=f"Swept hull, fuel segment {k + 1}")
    fig.update_layout(xaxis_title="CG station", yaxis_title="Gross weight", height=520, legend=dict(orientation="h", y=-0.2), margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")

    st.markdown("### 6. Result")
    st.write(f"Robust fuel cap **{result.cap:,.2f}** of {result.full:,.2f} full fuel. It is the minimum over all real configurations of each one's maximum safe fuel *prefix*.")
    if result.config is not None:
        base = problem.base_of(result.config)
        rows_ = [(i, f"{f[0]:,.2f}", s[0], s[1] / s[0], problem.inside(s)) for i, (f, s) in enumerate((f, wb.add(base, f)) for f in problem.fuel)]
        st.write("Limiting configuration, centrogram at each tank boundary:")
        st.dataframe(pd.DataFrame(rows_, columns=["Waypoint", "Fuel", "Gross weight", "CG", "Inside envelope"]), hide_index=True, width="stretch")
        st.caption("The cap can fall between waypoints: on each segment the quadratic q(t) = 0 is solved for every edge and the first unsafe sub-interval ends the prefix.")
