"""Interactive compact branch-and-bound explorer.

Run from the repository root with ``streamlit run demo/streamlit_app.py``.
This viewer deliberately visualises compact groups and real witnesses, not every
centrogram in the Cartesian product.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_DIR.parent
DATA_PATH = REPOSITORY_ROOT / "data" / "boeing_707_124_demo.json"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hull_branch_and_bound as engine  # noqa: E402


st.set_page_config(
    page_title="Compact centrogram explorer",
    page_icon=":material/account_tree:",
    layout="wide",
)


@st.cache_data(max_entries=8)
def default_scenario() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


@st.cache_data(max_entries=16, show_spinner=False)
def solve_compact_search(scenario_json: str, node_budget: int) -> engine.SearchResult:
    """Cache immutable, independent searches between visual reruns."""

    return engine.run_branch_and_bound(
        engine.scenario_from_dict(json.loads(scenario_json)), node_budget=node_budget
    )


def parse_numbers(value: str, name: str) -> list[float]:
    try:
        parsed = [float(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise ValueError(f"{name} must be a comma-separated list of numbers.") from error
    if not parsed:
        raise ValueError(f"{name} needs at least one number.")
    return parsed


def scenario_controls(default: dict[str, Any]) -> tuple[dict[str, Any], int, bool]:
    """Use a form so edits do not start an expensive search on each keystroke."""

    scenario = copy.deepcopy(default)
    inputs = scenario["illustrative_inputs"]
    with st.form("compact-scenario"):
        st.subheader("Scenario inputs")
        st.caption("Input changes are applied only when you run the compact search.")
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("**Fixed empty-fuel load**")
            basic = inputs["basic_aircraft"]
            basic["weight_lb"] = st.number_input(
                "Basic aircraft weight (lb)", min_value=1.0, value=float(basic["weight_lb"]), step=100.0
            )
            basic["cg_station_in"] = st.number_input(
                "Basic aircraft CG station (in)", value=float(basic["cg_station_in"]), step=1.0
            )
            payload = inputs["mandatory_center_payload"]
            payload["weight_lb"] = st.number_input(
                "Mandatory payload (lb)", min_value=0.0, value=float(payload["weight_lb"]), step=100.0
            )
            payload["station_in"] = st.number_input(
                "Payload station (in)", value=float(payload["station_in"]), step=1.0
            )
        with right:
            st.markdown("**Independent passenger perturbations**")
            passengers = inputs["roaming_passengers"]
            passengers["count"] = int(
                st.number_input(
                    "Moving passenger count",
                    min_value=1,
                    max_value=40,
                    value=int(passengers["count"]),
                    step=1,
                    help="The compact solver does not materialise this product. The node budget below controls refinement work instead.",
                )
            )
            weights = st.text_input(
                "Passenger weight endpoints (lb, comma-separated)",
                value=", ".join(map(str, passengers["weight_endpoints_lb"])),
            )
            stations = st.text_input(
                "Passenger station endpoints (in, comma-separated)",
                value=", ".join(map(str, passengers["station_endpoints_in"])),
            )
            passengers["weight_endpoints_lb"] = parse_numbers(weights, "Passenger weight endpoints")
            passengers["station_endpoints_in"] = parse_numbers(stations, "Passenger station endpoints")
            node_budget = int(
                st.select_slider(
                    "Maximum compact groups to examine",
                    options=[32, 64, 128, 256, 512, 1_024, 2_048],
                    value=512,
                    help="A proof-work budget, not a configuration-count limit. Reaching it returns unresolved rather than a false exact result.",
                )
            )
        with st.expander("Fuel path and envelope used by this demo", expanded=False):
            st.caption("These tracked illustrative inputs remain available for inspection; the compact search uses all of them.")
            st.dataframe(
                pd.DataFrame(scenario["fuel_tanks"])[["name", "usable_weight_lb", "moment_arm_in", "fuel_sequence_order"]],
                hide_index=True,
                width="stretch",
                alt="Illustrative fuel tank weights, moment arms, and sequence used by the compact calculation.",
            )
            st.dataframe(
                pd.DataFrame(
                    scenario["envelope"]["calculation_points_cg_station_in_gross_weight_lb"],
                    columns=["CG station (in)", "gross weight (lb)"],
                ),
                hide_index=True,
                width="stretch",
                alt="Illustrative conservative CG and gross-weight envelope vertices used by the compact calculation.",
            )
        submitted = st.form_submit_button("Run compact search", type="primary", icon=":material/play_arrow:")
    return scenario, node_budget, submitted


def compact_count(value: int) -> str:
    rendered = f"{value:,}"
    if len(rendered) <= 14:
        return rendered
    leading = str(value)[:4]
    return f"{leading[0]}.{leading[1:]} × 10^{len(str(value)) - 1}"


def configuration_label(scenario: engine.Scenario, choices: Sequence[int]) -> str:
    counts = pd.Series([scenario.choice_labels[index] for index in choices]).value_counts()
    return " · ".join(f"{count}× {label}" for label, count in counts.items())


def mapped_hull_points(hull: Sequence[engine.State], samples_per_edge: int = 18) -> tuple[list[float], list[float]]:
    """Map W--M edges as curved paths in CG / gross-weight coordinates."""

    if len(hull) == 1:
        return [hull[0].cg], [hull[0].weight]
    states: list[engine.State] = []
    for start, end in zip(hull, hull[1:] + hull[:1]):
        states.extend(engine.interpolate(start, end, index / samples_per_edge) for index in range(samples_per_edge))
    states.append(states[0])
    return [state.cg for state in states], [state.weight for state in states]


def add_envelope(figure: go.Figure, scenario: engine.Scenario) -> None:
    points = list(scenario.envelope)
    figure.add_trace(
        go.Scatter(
            x=[point[0] for point in points] + [points[0][0]],
            y=[point[1] for point in points] + [points[0][1]],
            mode="lines",
            name="Calculation envelope",
            fill="toself",
            fillcolor="rgba(34, 197, 94, 0.16)",
            line={"color": "#15803d", "width": 2},
            hovertemplate="CG %{x:.2f} in<br>Weight %{y:,.0f} lb<extra>Envelope</extra>",
        )
    )


def certificate_figure(result: engine.SearchResult, record: engine.SearchRecord) -> go.Figure:
    """Visualise one compact certificate and its selected real witness."""

    certificate = record.certificate
    if certificate is None:
        raise ValueError("The selected search record has no certificate.")
    scenario = result.scenario
    figure = go.Figure()
    add_envelope(figure, scenario)
    base_x, base_y = mapped_hull_points(certificate.base_hull)
    figure.add_trace(
        go.Scatter(
            x=base_x,
            y=base_y,
            mode="lines",
            name="Factorised base-state hull",
            fill="toself",
            fillcolor="rgba(37, 99, 235, 0.16)",
            line={"color": "#2563eb", "width": 2},
            hovertemplate="Group base boundary<br>CG %{x:.2f} in<br>Weight %{y:,.0f} lb<extra></extra>",
        )
    )
    focus = max(certificate.segments, key=lambda segment: segment.worst_q)
    if focus.name != "empty fuel":
        swept_x, swept_y = mapped_hull_points(focus.swept_hull)
        figure.add_trace(
            go.Scatter(
                x=swept_x,
                y=swept_y,
                mode="lines",
                name=f"Swept hull — {focus.name}",
                fill="toself",
                fillcolor="rgba(245, 158, 11, 0.18)",
                line={"color": "#d97706", "width": 2.5},
                hovertemplate="Swept group boundary<br>CG %{x:.2f} in<br>Weight %{y:,.0f} lb<extra></extra>",
            )
        )
    witness = engine.centrogram(
        scenario, engine.real_base(scenario, result.witness_choices), result.candidate_cap
    )
    figure.add_trace(
        go.Scatter(
            x=[state.cg for state in witness],
            y=[state.weight for state in witness],
            mode="lines+markers",
            name="Real witness centrogram",
            line={"color": "#7c3aed", "width": 3},
            marker={"size": 6, "color": "#7c3aed"},
            hovertemplate="Real witness<br>CG %{x:.2f} in<br>Weight %{y:,.0f} lb<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[witness[-1].cg],
            y=[witness[-1].weight],
            mode="markers",
            name="Candidate cap U",
            marker={"size": 13, "color": "#dc2626", "symbol": "x"},
            hovertemplate=f"Candidate cap U: {result.candidate_cap:,.0f} lb<extra></extra>",
        )
    )
    visible = list(certificate.base_hull) + list(focus.swept_hull) + list(witness)
    cgs = [point[0] for point in scenario.envelope] + [state.cg for state in visible]
    weights = [point[1] for point in scenario.envelope] + [state.weight for state in visible]
    cg_pad = max(2.0, (max(cgs) - min(cgs)) * 0.08)
    weight_pad = max(2_000.0, (max(weights) - min(weights)) * 0.08)
    state = "certified safe" if record.outcome == "certified" else "unresolved enclosing hull"
    figure.update_layout(
        title=f"Node {record.node_id}: {state}; focus = {focus.name}",
        xaxis_title="Longitudinal CG station (inches aft of datum)",
        yaxis_title="Gross weight (lb)",
        xaxis={"range": [min(cgs) - cg_pad, max(cgs) + cg_pad]},
        yaxis={"range": [min(weights) - weight_pad, max(weights) + weight_pad]},
        legend={"orientation": "h", "y": -0.18},
        margin={"l": 20, "r": 20, "t": 58, "b": 68},
        height=640,
        template="plotly_white",
    )
    return figure


def flow_figure(result: engine.SearchResult) -> go.Figure:
    """Draw the real, compact algorithm flow from this search result."""

    scenario = result.scenario
    modeled = len(scenario.choice_states) ** scenario.passenger_count
    boxes = [
        (0, "Discrete endpoints", f"{scenario.passenger_count} passengers<br>{len(scenario.choice_states)} choices each", "#2563eb"),
        (1, "Minkowski hull", "conv(S₁) ⊕ ··· ⊕ conv(Sₙ)<br>no Cartesian product", "#7c3aed"),
        (2, "Sweep fuel", "Hull ⊕ fuel segment<br>maximise q(W, M)", "#d97706"),
        (3, "Certificate?", f"{result.certified_groups} certified<br>{result.split_groups} split", "#0891b2"),
        (4, "Real paths only", f"{result.real_evaluations} evaluated<br>of {compact_count(modeled)} modeled", "#dc2626"),
    ]
    figure = go.Figure()
    for x, title, detail, color in boxes:
        red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
        figure.add_shape(
            type="rect", x0=x - 0.42, x1=x + 0.42, y0=-0.42, y1=0.42,
            line={"color": color, "width": 2}, fillcolor=f"rgba({red},{green},{blue},0.12)",
        )
        figure.add_annotation(
            x=x, y=0.05, text=f"<b>{title}</b><br><span style='font-size:11px'>{detail}</span>",
            showarrow=False, align="center",
        )
    for x in range(len(boxes) - 1):
        figure.add_annotation(
            x=x + 0.58, y=0, ax=x + 0.42, ay=0, showarrow=True,
            arrowhead=3, arrowsize=1.2, arrowwidth=2, arrowcolor="#64748b",
        )
    verdict = "Exact cap proved" if result.complete else f"Unresolved after {result.node_budget:,} group checks"
    cap_meaning = "the robust cap" if result.complete else "only a real-configuration upper bound"
    figure.add_annotation(
        x=2, y=-0.85, text=f"<b>{verdict}</b> — U = {result.candidate_cap:,.0f} lb is {cap_meaning}.",
        showarrow=False, font={"size": 13, "color": "#0f172a"},
    )
    figure.update_xaxes(visible=False, range=[-0.7, 4.7])
    figure.update_yaxes(visible=False, range=[-1.1, 0.7])
    figure.update_layout(height=300, margin={"l": 10, "r": 10, "t": 10, "b": 10}, template="plotly_white")
    return figure


def seed_dataframe(result: engine.SearchResult) -> pd.DataFrame:
    scenario = result.scenario
    return pd.DataFrame([
        {
            "real seed configuration": configuration_label(scenario, seed.choices),
            "maximum safe fuel (lb)": round(seed.cap, 3),
            "zero-fuel feasible": seed.feasible,
            "role": "establishes U" if abs(seed.cap - result.candidate_cap) <= engine.EPSILON else "upper-bound probe",
        }
        for seed in result.seeds
    ])


def search_dataframe(result: engine.SearchResult) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "node": record.node_id,
            "parent": "—" if record.parent_id is None else str(record.parent_id),
            "depth": record.depth,
            "outcome": record.outcome,
            "real combinations represented": compact_count(record.group.combinations),
            "factorisation": engine.group_summary(record.group),
            "hull vertices": record.hull_vertices,
            "fuel checks": record.fuel_segments,
            "largest q": f"{record.worst_q:.4g}",
            "U before (lb)": round(record.cap_before, 3),
            "U after (lb)": round(record.cap_after, 3),
            "decision": record.note,
        }
        for record in result.records
    ])


def render_overview(result: engine.SearchResult) -> None:
    st.subheader("What the solver actually evaluated")
    st.caption("Only these few seeds and any real leaves are centrograms. Every other trace row is a compact factorised group.")
    st.dataframe(
        seed_dataframe(result), hide_index=True, width="stretch",
        alt="The handful of real uniform endpoint centrograms used to establish the candidate fuel upper bound.",
    )
    st.subheader("Branch-and-bound trace")
    st.dataframe(
        search_dataframe(result), hide_index=True, width="stretch", height=430,
        alt="Chronological compact group decisions showing certification, splitting, real leaf evaluation, and updates to the fuel candidate.",
    )


def render_certificate_view(result: engine.SearchResult) -> None:
    st.subheader("Inspect one compact group certificate")
    records = [record for record in result.records if record.certificate is not None]
    selected_id = st.selectbox(
        "Search node", options=[record.node_id for record in records], key="certificate_node",
        format_func=lambda node_id: next(
            f"Node {record.node_id} — {record.outcome} — {compact_count(record.group.combinations)} combinations"
            for record in records if record.node_id == node_id
        ),
    )
    record = next(item for item in records if item.node_id == selected_id)
    certificate = record.certificate
    assert certificate is not None
    first, second, third = st.columns(3)
    first.metric("Group combinations", compact_count(record.group.combinations))
    second.metric("Base-hull vertices", f"{len(certificate.base_hull):,}")
    third.metric("Largest q", f"{certificate.worst_q:.4g}")
    if record.outcome == "certified":
        st.success("The group hull is inside the envelope through U, so every real configuration in it is removed without individual centrogram evaluation.")
    else:
        st.info("This hull is unresolved. Its orange area may include artificial mixed loads, so the solver splits it rather than treating it as a flight violation.")
    st.plotly_chart(
        certificate_figure(result, record), width="stretch", config={"displaylogo": False},
        alt="The selected compact passenger group hull and its most restrictive fuel sweep in the CG and gross-weight envelope, with one real witness path.",
    )
    st.dataframe(
        pd.DataFrame([
            {
                "fuel segment": segment.name,
                "fuel start (lb)": round(segment.fuel_start, 3),
                "fuel end / U (lb)": round(segment.fuel_end, 3),
                "swept-hull vertices": len(segment.swept_hull),
                "largest q": f"{segment.worst_q:.4g}",
                "status": "safe" if segment.worst_q <= engine.CERTIFICATE_TOLERANCE else "unresolved",
            }
            for segment in certificate.segments
        ]),
        hide_index=True, width="stretch",
        alt="Fuel-segment transformed envelope-constraint values for the selected compact group.",
    )


def render_explanation(result: engine.SearchResult) -> None:
    st.subheader("The algorithm being simulated")
    st.plotly_chart(
        flow_figure(result), width="stretch", config={"displaylogo": False, "staticPlot": True},
        alt="A left-to-right diagram of the factorised convex-hull branch-and-bound algorithm from endpoints to certificates and real witnesses.",
    )
    scenario = result.scenario
    endpoint_count = len(scenario.choice_states)
    modeled = endpoint_count**scenario.passenger_count

    with st.container(border=True):
        st.markdown("**1. Keep uncertainty factorised**")
        st.markdown(
            "Represent every passenger by its own small endpoint contribution set in additive weight–moment coordinates. "
            "Do not construct the Cartesian product."
        )
        st.latex(r"S_i = \{(w,\;w x) \mid (w,x) \text{ is an allowed endpoint for passenger } i\}")
        st.caption(
            f"Active scenario: {scenario.passenger_count} independent passengers × {endpoint_count} endpoints = "
            f"{compact_count(modeled)} modeled real combinations."
        )

    with st.container(border=True):
        st.markdown("**2. Build one compact hull for a whole group**")
        st.markdown(
            "Convexify each passenger's currently allowed endpoints, then add the small polygons with Minkowski sums. "
            "This creates the hull of the group's possible base states without first listing those states."
        )
        st.latex(r"H_G = H_{\mathrm{fixed}} \oplus \bigoplus_{i=1}^{n} \operatorname{conv}(S_i^G)")
        st.caption("At the root, each allowed set Sᵢᴳ contains every endpoint for that passenger.")

    with st.container(border=True):
        st.markdown("**3. Establish a real candidate fuel cap U**")
        st.markdown(
            "Evaluate a small set of real seed loads. Their smallest safe prefix is an upper bound on the fuel that can be safe for every configuration."
        )
        st.latex(r"U = \min_{c \in C_{\mathrm{seed}}} F_{\mathrm{safe}}(c)")
        st.caption(
            "A hull result never lowers U. It is only a proof that a group cannot lower it, or a request to refine that group."
        )

    with st.container(border=True):
        st.markdown("**4. Sweep the group hull across each fuel segment**")
        st.markdown(
            "For every fuel segment up to U, add the segment to the base hull. This encloses every real centrogram in the group over that interval."
        )
        st.latex(r"H_{G,j}^{\mathrm{swept}} = H_G \oplus \operatorname{segment}(F_j, F_{j+1})")
        st.latex(r"x = \frac{M}{W}, \qquad a x + bW \leq c \;\Longrightarrow\; q(W,M) = aM + bW^2 - cW \leq 0")

    with st.container(border=True):
        st.markdown("**5. Certify, split, or evaluate a real leaf**")
        st.markdown(
            "Maximise each transformed envelope constraint over every swept-hull edge. The result has exactly three safe interpretations."
        )
        st.latex(r"\max_{p \in H_{G,j}^{\mathrm{swept}}} q_e(p) \leq 0 \quad \text{for every envelope edge } e")
        st.table(
            {
                "Certified": "Remove the entire group: every real member is safe through U.",
                "Unresolved": "Split a passenger's allowed endpoints. An artificial hull point is not an aircraft violation.",
                "Real leaf": "Calculate its centrogram; only this result may reduce U.",
            },
            border="horizontal",
            width="stretch",
        )

    with st.container(border=True):
        st.markdown("**6. Finish only when all groups are covered**")
        st.markdown(
            "Previously certified groups stay safe if U falls, because the required fuel prefix only becomes shorter. "
            "When the queue is empty, the candidate is exact; when the work budget is reached, the answer remains explicitly unresolved."
        )
        st.latex(r"U \leftarrow \min\bigl(U,\;F_{\mathrm{safe}}(c_{\mathrm{real}})\bigr)")
        verdict = "The active run completed, so U is exact." if result.complete else "The active run is unresolved, so U is only an upper bound."
        st.caption(verdict)
    st.caption("The exhaustive reference script remains useful only to validate small cases. This viewer follows the compact search and never renders every centrogram.")


def render_result(result: engine.SearchResult) -> None:
    scenario = result.scenario
    modeled = len(scenario.choice_states) ** scenario.passenger_count
    root = next((record for record in result.records if record.parent_id is None), None)
    st.header("Compact search result", icon=":material/account_tree:")
    if result.complete:
        st.success(
            f"Exact robust cap proved: every modeled combination is safe from empty fuel through {result.candidate_cap:,.0f} lb.",
            icon=":material/verified_user:",
        )
    else:
        st.warning(
            f"Search paused at the {result.node_budget:,}-group budget with {result.pending_groups:,} unresolved group(s). "
            f"{result.candidate_cap:,.0f} lb is a real-configuration upper bound, not an established robust cap.",
            icon=":material/pending:",
        )
    first, second, third, fourth = st.columns(4)
    first.metric("Modeled combinations", compact_count(modeled), help=f"{len(scenario.choice_states)}^{scenario.passenger_count} discrete configurations")
    second.metric("Real centrograms evaluated", f"{result.real_evaluations:,}")
    third.metric("Groups certified", f"{result.certified_groups:,}")
    fourth.metric("Root-hull vertices", f"{root.hull_vertices if root else 0:,}")
    st.caption(
        f"Candidate U comes from the real witness: {configuration_label(scenario, result.witness_choices)}. "
        "A hull proves safety or asks for refinement; it never directly imposes a fuel limit."
    )
    view = st.segmented_control(
        "Explore the compact calculation",
        ["Search overview", "Certificate on envelope", "Algorithm explanation"],
        default="Search overview", key="compact_view",
    )
    if view == "Search overview":
        render_overview(result)
    elif view == "Certificate on envelope":
        render_certificate_view(result)
    else:
        render_explanation(result)


def main() -> None:
    st.title("Convex-hull branch-and-bound explorer", icon=":material/account_tree:")
    st.warning(
        "Educational illustration only — never use this interface or its illustrative Boeing 707 inputs for aircraft loading, dispatch, or flight operations.",
        icon=":material/warning:",
    )
    st.caption("This version keeps passenger combinations factorised. It shows compact hull certificates and selected real witnesses, not every centrogram.")
    scenario, node_budget, submitted = scenario_controls(default_scenario())
    if submitted or "compact_search_request" not in st.session_state:
        st.session_state.compact_search_request = (json.dumps(scenario, sort_keys=True), node_budget)
    scenario_json, active_budget = st.session_state.compact_search_request
    try:
        with st.spinner("Constructing compact hulls and resolving groups…"):
            result = solve_compact_search(scenario_json, active_budget)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        st.error(f"Cannot calculate this scenario: {error}", icon=":material/error:")
        st.stop()
    render_result(result)


if __name__ == "__main__":
    main()
