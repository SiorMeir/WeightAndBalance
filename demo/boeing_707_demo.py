"""A self-contained, dependency-free weight-and-balance pruning demo.

The demo is educational, not dispatch software.  Run from the repository root:

    python demo/boeing_707_demo.py

It writes an SVG chart and a machine-readable summary to demo/output/.
"""

from __future__ import annotations

import itertools
import json
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
DATA_PATH = ROOT.parent / "data" / "boeing_707_124_demo.json"
EPSILON = 1e-7
# The quadratic constraints use lb-in-scale moments. This absolute slack is
# only for accumulated binary floating-point roundoff at a mathematical
# boundary; production code needs a documented outward-rounded precision plan.
CERTIFICATE_TOLERANCE = 1e-4


@dataclass(frozen=True)
class State:
    """A longitudinal aircraft contribution in lb and lb-in."""

    weight: float
    moment: float

    @property
    def cg(self) -> float:
        return self.moment / self.weight

    def __add__(self, other: "State") -> "State":
        return State(self.weight + other.weight, self.moment + other.moment)


@dataclass(frozen=True)
class PassengerChoice:
    weight: int
    station: int


@dataclass(frozen=True)
class Configuration:
    choices: tuple[PassengerChoice, ...]
    base: State
    group: str

    @property
    def identifier(self) -> str:
        return " / ".join(f"{choice.weight} lb @ {choice.station}" for choice in self.choices)


def load_demo_data(path: Path = DATA_PATH) -> dict:
    """Read every model input from the tracked raw-data file."""
    return json.loads(path.read_text(encoding="utf-8"))


DATA = load_demo_data()
ENVELOPE: tuple[tuple[float, float], ...] = tuple(
    (float(cg), float(weight)) for cg, weight in DATA["envelope"]["calculation_points_cg_station_in_gross_weight_lb"]
)
TANKS = tuple(
    (float(tank["usable_weight_lb"]), float(tank["moment_arm_in"]), tank["name"])
    for tank in sorted(DATA["fuel_tanks"], key=lambda tank: tank["fuel_sequence_order"])
)
ILLUSTRATIVE = DATA["illustrative_inputs"]
BASIC = ILLUSTRATIVE["basic_aircraft"]
BASIC_AIRCRAFT = State(float(BASIC["weight_lb"]), float(BASIC["weight_lb"]) * float(BASIC["cg_station_in"]))
PILOT_DATA = ILLUSTRATIVE["stationary_pilots"]
PILOTS = State(float(PILOT_DATA["count"] * PILOT_DATA["weight_each_lb"]), float(PILOT_DATA["count"] * PILOT_DATA["weight_each_lb"] * PILOT_DATA["station_in"]))
CENTER_PAYLOAD_DATA = ILLUSTRATIVE["mandatory_center_payload"]
CENTER_PAYLOAD = State(float(CENTER_PAYLOAD_DATA["weight_lb"]), float(CENTER_PAYLOAD_DATA["weight_lb"]) * float(CENTER_PAYLOAD_DATA["station_in"]))
PASSENGER_DATA = ILLUSTRATIVE["roaming_passengers"]
PASSENGER_STATIONS = tuple(int(value) for value in PASSENGER_DATA["station_endpoints_in"])
PASSENGER_WEIGHTS = tuple(int(value) for value in PASSENGER_DATA["weight_endpoints_lb"])
PASSENGER_COUNT = int(PASSENGER_DATA["count"])


def fuel_waypoints() -> list[State]:
    """Fuel states, from empty to full, in weight-moment coordinates."""
    points = [State(0.0, 0.0)]
    running = points[0]
    for pounds, arm, _name in TANKS:
        running = running + State(pounds, pounds * arm)
        points.append(running)
    return points


FUEL = fuel_waypoints()
FULL_FUEL = FUEL[-1].weight


def first_light_group(choices: Sequence[PassengerChoice]) -> str:
    """A disjoint branch partition: P1 light, then P2 light, ..., or all heavy."""
    for index, choice in enumerate(choices, start=1):
        if choice.weight == 185:
            return f"P{index} light (hull-pruned)"
    return "all-heavy (limiting group)"


def make_configurations() -> list[Configuration]:
    choices = [PassengerChoice(w, s) for w in PASSENGER_WEIGHTS for s in PASSENGER_STATIONS]
    configurations = []
    for combination in itertools.product(choices, repeat=PASSENGER_COUNT):
        passenger_state = State(
            sum(choice.weight for choice in combination),
            sum(choice.weight * choice.station for choice in combination),
        )
        configurations.append(
            Configuration(combination, BASIC_AIRCRAFT + PILOTS + CENTER_PAYLOAD + passenger_state, first_light_group(combination))
        )
    return configurations


def is_inside_envelope(state: State, tolerance: float = EPSILON) -> bool:
    """Exact predicate (apart from float arithmetic) for a convex CCW polygon."""
    x, w = state.cg, state.weight
    for (x0, w0), (x1, w1) in zip(ENVELOPE, ENVELOPE[1:] + ENVELOPE[:1]):
        if (x1 - x0) * (w - w0) - (w1 - w0) * (x - x0) < -tolerance:
            return False
    return True


def quadratic_roots(a: float, b: float, c: float) -> list[float]:
    """Real roots, with small near-linear terms handled explicitly."""
    if abs(a) < EPSILON:
        return [] if abs(b) < EPSILON else [-c / b]
    discriminant = b * b - 4 * a * c
    if discriminant < -EPSILON:
        return []
    discriminant = max(0.0, discriminant)
    root = discriminant**0.5
    return [(-b - root) / (2 * a), (-b + root) / (2 * a)]


def segment_breaks(start: State, end: State) -> list[float]:
    """All locations where the centrogram can cross an envelope edge."""
    dw, dm = end.weight - start.weight, end.moment - start.moment
    breaks = [0.0, 1.0]
    for (x0, w0), (x1, w1) in zip(ENVELOPE, ENVELOPE[1:] + ENVELOPE[:1]):
        dx, dy = x1 - x0, w1 - w0
        # cross(edge, (M/W, W)) * W = a*t^2 + b*t + c.
        alpha = dx * dw * dw
        beta = 2 * dx * start.weight * dw + (-dx * w0 + dy * x0) * dw - dy * dm
        gamma = dx * start.weight * start.weight + (-dx * w0 + dy * x0) * start.weight - dy * start.moment
        breaks.extend(root for root in quadratic_roots(alpha, beta, gamma) if EPSILON < root < 1 - EPSILON)
    return sorted(set(round(value, 12) for value in breaks))


def interpolate(start: State, end: State, t: float) -> State:
    return State(start.weight + (end.weight - start.weight) * t, start.moment + (end.moment - start.moment) * t)


def maximum_safe_fuel(base: State) -> float:
    """Maximum safe prefix, analytically partitioning every fuel segment."""
    if not is_inside_envelope(base):
        raise ValueError("The zero-fuel starting state is invalid")
    previous_fuel = 0.0
    for f0, f1 in zip(FUEL, FUEL[1:]):
        start, end = base + f0, base + f1
        for left, right in zip(segment_breaks(start, end), segment_breaks(start, end)[1:]):
            midpoint = interpolate(start, end, (left + right) / 2)
            if not is_inside_envelope(midpoint):
                return previous_fuel + f0.weight + left * (f1.weight - f0.weight)
        if not is_inside_envelope(end):
            # The final endpoint can only be invalid after a crossing, which
            # segment_breaks has already exposed; retain this defensive guard.
            return previous_fuel + f0.weight
        previous_fuel = 0.0
    return FULL_FUEL


def cross(origin: State, a: State, b: State) -> float:
    return (a.weight - origin.weight) * (b.moment - origin.moment) - (a.moment - origin.moment) * (b.weight - origin.weight)


def convex_hull(points: Iterable[State]) -> list[State]:
    """Monotone-chain hull in (weight, moment); collinear interior points removed."""
    unique = sorted({(round(point.weight, 9), round(point.moment, 9)) for point in points})
    states = [State(weight, moment) for weight, moment in unique]
    if len(states) <= 1:
        return states
    lower: list[State] = []
    for point in states:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= EPSILON:
            lower.pop()
        lower.append(point)
    upper: list[State] = []
    for point in reversed(states):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= EPSILON:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def maximum_constraint_violation(hull: Sequence[State]) -> float:
    """Maximise -cross*W along each hull edge; <= 0 certifies containment.

    This is the handoff's quadratic certificate.  Each edge of the W-M hull is
    tested at its endpoints and, if concave, at its stationary interior point.
    """
    if not hull:
        return float("-inf")
    edges = list(zip(hull, hull[1:] + hull[:1])) if len(hull) > 1 else [(hull[0], hull[0])]
    worst = float("-inf")
    for (x0, w0), (x1, w1) in zip(ENVELOPE, ENVELOPE[1:] + ENVELOPE[:1]):
        dx, dy = x1 - x0, w1 - w0
        # q = -[dx W^2 + (-dx*w0+dy*x0)W - dy M], so q <= 0 is safe.
        for start, end in edges:
            dw, dm = end.weight - start.weight, end.moment - start.moment
            a = -dx * dw * dw
            b = -(2 * dx * start.weight * dw + (-dx * w0 + dy * x0) * dw - dy * dm)
            c = -(dx * start.weight * start.weight + (-dx * w0 + dy * x0) * start.weight - dy * start.moment)
            candidates = [0.0, 1.0]
            if a < -EPSILON:
                stationary = -b / (2 * a)
                if EPSILON < stationary < 1 - EPSILON:
                    candidates.append(stationary)
            worst = max(worst, *(a * t * t + b * t + c for t in candidates))
    return worst


def group_certificate(configurations: Sequence[Configuration], cap: float) -> tuple[bool, float, int]:
    """Certify every group's centrograms up to cap using swept W-M hulls."""
    base_hull = convex_hull(configuration.base for configuration in configurations)
    worst = float("-inf")
    checks = 0
    for f0, f1 in zip(FUEL, FUEL[1:]):
        if f0.weight >= cap - EPSILON:
            break
        clipped = f1 if f1.weight <= cap else interpolate(f0, f1, (cap - f0.weight) / (f1.weight - f0.weight))
        swept = convex_hull([base + fuel for base in base_hull for fuel in (f0, clipped)])
        worst = max(worst, maximum_constraint_violation(swept))
        checks += 1
    return worst <= CERTIFICATE_TOLERANCE, worst, checks


def fuel_state_at(fuel_weight: float) -> State:
    for f0, f1 in zip(FUEL, FUEL[1:]):
        if fuel_weight <= f1.weight + EPSILON:
            return interpolate(f0, f1, (fuel_weight - f0.weight) / (f1.weight - f0.weight))
    return FUEL[-1]


def centrogram(configuration: Configuration, upto_fuel: float = FULL_FUEL) -> list[State]:
    points = [configuration.base + FUEL[0]]
    for f0, f1 in zip(FUEL, FUEL[1:]):
        if f0.weight >= upto_fuel - EPSILON:
            break
        points.append(configuration.base + (f1 if f1.weight <= upto_fuel else interpolate(f0, f1, (upto_fuel - f0.weight) / (f1.weight - f0.weight))))
    return points


def svg_path(points: Sequence[tuple[float, float]], close: bool = False) -> str:
    command = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return command + (" Z" if close else "")


def write_chart(configurations: Sequence[Configuration], caps: dict[str, float], certificates: dict[str, tuple[bool, float, int]], robust_cap: float) -> Path:
    width, height = 1440, 920
    left, top, chart_width, chart_height = 100, 100, 890, 700
    x_min, x_max, w_min, w_max = 810, 878, 120_000, 252_000
    x = lambda cg: left + (cg - x_min) / (x_max - x_min) * chart_width
    y = lambda weight: top + chart_height - (weight - w_min) / (w_max - w_min) * chart_height
    xy = lambda state: (x(state.cg), y(state.weight))

    critical = [config for config in configurations if config.group.startswith("all-heavy")]
    pruned = [config for config in configurations if config not in critical]
    envelope_path = svg_path([(x(cg), y(weight)) for cg, weight in ENVELOPE], close=True)
    elements = [
        "<svg xmlns='http://www.w3.org/2000/svg' width='1440' height='920' viewBox='0 0 1440 920'>",
        "<style>text{font-family:Arial,sans-serif;fill:#172033}.axis{stroke:#52627a;stroke-width:1}.grid{stroke:#d8e0ea;stroke-width:1}.small{font-size:14px}.label{font-size:17px;font-weight:600}.title{font-size:26px;font-weight:700}.note{font-size:13px;fill:#45546b}</style>",
        "<rect width='100%' height='100%' fill='#f8fafc'/>",
        "<text class='title' x='100' y='42'>Boeing 707-124 demonstration: centrograms with 8,000 lb center payload</text>",
        "<text class='small' x='100' y='70'>Case I CG envelope and tank capacities/arms: FAA TCDS 4A21 Rev. 8 (1973). Aircraft basic data, passenger area, center payload, and fuel sequence are illustrative.</text>",
    ]
    for weight in range(130_000, 251_000, 10_000):
        elements.extend([
            f"<line class='grid' x1='{left}' y1='{y(weight):.2f}' x2='{left + chart_width}' y2='{y(weight):.2f}'/>",
            f"<text class='small' x='{left - 12}' y='{y(weight) + 5:.2f}' text-anchor='end'>{weight // 1000}k</text>",
        ])
    for cg in range(810, 879, 5):
        elements.extend([
            f"<line class='grid' x1='{x(cg):.2f}' y1='{top}' x2='{x(cg):.2f}' y2='{top + chart_height}'/>",
            f"<text class='small' x='{x(cg):.2f}' y='{top + chart_height + 25}' text-anchor='middle'>{cg}</text>",
        ])
    elements.append(f"<path d='{envelope_path}' fill='#d8f3dc' stroke='#167c45' stroke-width='3'/>")
    # Full trajectories are faint/dashed because the new central cargo means
    # every configuration would exceed the max-weight boundary at full fuel.
    for configuration in configurations:
        elements.append(f"<path d='{svg_path([xy(point) for point in centrogram(configuration)])}' fill='none' stroke='#94a3b8' stroke-opacity='.32' stroke-width='1.0' stroke-dasharray='5 4'/>")
    for configuration in pruned:
        cap = caps[configuration.identifier]
        elements.append(f"<path d='{svg_path([xy(point) for point in centrogram(configuration, cap)])}' fill='none' stroke='#277da1' stroke-opacity='.28' stroke-width='1.5'/>")
    for configuration in critical:
        cap = caps[configuration.identifier]
        elements.append(f"<path d='{svg_path([xy(point) for point in centrogram(configuration, cap)])}' fill='none' stroke='#e05d2f' stroke-opacity='.75' stroke-width='2.4'/>")
    # The full-fuel end of each all-heavy line lies beyond the 248k boundary;
    # it makes the limiting behaviour immediately visible.
    elements.extend([
        f"<line class='axis' x1='{left}' y1='{top + chart_height}' x2='{left + chart_width}' y2='{top + chart_height}'/>",
        f"<line class='axis' x1='{left}' y1='{top}' x2='{left}' y2='{top + chart_height}'/>",
        f"<text class='label' x='{left + chart_width / 2:.2f}' y='{top + chart_height + 62}' text-anchor='middle'>Longitudinal CG station (inches aft of datum)</text>",
        f"<text class='label' x='28' y='{top + chart_height / 2:.2f}' transform='rotate(-90 28 {top + chart_height / 2:.2f})' text-anchor='middle'>Gross weight (lb)</text>",
        "<rect x='1030' y='112' width='360' height='505' rx='12' fill='white' stroke='#cad5e3'/>",
        "<text class='label' x='1055' y='148'>Decision summary</text>",
        "<rect x='1055' y='169' width='24' height='14' fill='#d8f3dc' stroke='#167c45'/><text class='small' x='1090' y='182'>Conservative Case I subset</text>",
        "<line x1='1055' y1='207' x2='1079' y2='207' stroke='#94a3b8' stroke-width='2' stroke-dasharray='5 4'/><text class='small' x='1090' y='212'>Full-fuel extension (unsafe)</text>",
        "<line x1='1055' y1='237' x2='1079' y2='237' stroke='#277da1' stroke-width='3'/><text class='small' x='1090' y='242'>240 non-critical safe prefixes</text>",
        "<line x1='1055' y1='267' x2='1079' y2='267' stroke='#e05d2f' stroke-width='4'/><text class='small' x='1090' y='272'>16 all-heavy limiting prefixes</text>",
        f"<text class='small' x='1055' y='307'>Center payload: {CENTER_PAYLOAD.weight:,.0f} lb @ {CENTER_PAYLOAD.cg:,.0f} in</text>",
        f"<text class='small' x='1055' y='334'>Full fuel: {FULL_FUEL:,.0f} lb</text>",
        f"<text class='small' x='1055' y='361'>Robust cap: {robust_cap:,.0f} lb</text>",
        f"<text class='small' x='1055' y='388'>Lost capacity: {FULL_FUEL - robust_cap:,.0f} lb</text>",
        "<text class='small' x='1055' y='426'>Branch-and-bound partition:</text>",
    ])
    text_y = 451
    for group, (safe, violation, checks) in certificates.items():
        count = sum(config.group == group for config in configurations)
        verdict = "CERTIFIED" if safe else "UNRESOLVED"
        elements.append(f"<text class='note' x='1055' y='{text_y}'>{group}: {count} paths — {verdict}</text>")
        text_y += 26
    elements.extend([
        "<text class='note' x='100' y='842'>Blue groups are bounded in weight–moment space and certified against every fuel segment up to the robust cap. Orange is the directly evaluated all-heavy limiting group.</text>",
        "<text class='note' x='100' y='865'>The green region is a public certification envelope, but this SVG is a mathematical demonstration only — never use it for aircraft loading or flight planning.</text>",
        "</svg>",
    ])
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT / "boeing_707_centograms.svg"
    path.write_text("\n".join(elements), encoding="utf-8")
    return path


def write_centrogams_csv(configurations: Sequence[Configuration], caps: dict[str, float], robust_cap: float) -> Path:
    """Export every full centrogram waypoint with its global criticality.

    A configuration is *critical* when it attains the robust (fleet-wide) fuel
    cap. A non-critical configuration may still need its own fuel reduction;
    ``within_configuration_cap`` makes that distinction explicit.
    """
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT / "centrograms.csv"
    fieldnames = [
        "configuration_id", "classification", "branch_group", "configuration_fuel_cap_lb",
        "robust_fuel_cap_lb", "fuel_point", "fuel_lb", "gross_weight_lb", "cg_station_in",
        "moment_lb_in", "within_configuration_cap", "inside_envelope",
    ] + [field for passenger in range(1, PASSENGER_COUNT + 1) for field in (f"passenger_{passenger}_weight_lb", f"passenger_{passenger}_station_in")]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for index, configuration in enumerate(configurations, start=1):
            cap = caps[configuration.identifier]
            classification = "critical" if abs(cap - robust_cap) < EPSILON else "non-critical"
            passenger_fields = {
                key: value
                for passenger_index, choice in enumerate(configuration.choices, start=1)
                for key, value in ((f"passenger_{passenger_index}_weight_lb", choice.weight), (f"passenger_{passenger_index}_station_in", choice.station))
            }
            for fuel_point, fuel in enumerate(FUEL):
                state = configuration.base + fuel
                writer.writerow({
                    "configuration_id": f"C{index:03d}",
                    "classification": classification,
                    "branch_group": configuration.group,
                    "configuration_fuel_cap_lb": f"{cap:.6f}",
                    "robust_fuel_cap_lb": f"{robust_cap:.6f}",
                    "fuel_point": fuel_point,
                    "fuel_lb": f"{fuel.weight:.6f}",
                    "gross_weight_lb": f"{state.weight:.6f}",
                    "cg_station_in": f"{state.cg:.6f}",
                    "moment_lb_in": f"{state.moment:.6f}",
                    "within_configuration_cap": fuel.weight <= cap + EPSILON,
                    "inside_envelope": is_inside_envelope(state),
                    **passenger_fields,
                })
    return path


def main() -> None:
    configurations = make_configurations()
    caps = {config.identifier: maximum_safe_fuel(config.base) for config in configurations}
    robust_cap = min(caps.values())
    groups = {group: [config for config in configurations if config.group == group] for group in sorted({config.group for config in configurations})}
    certificates = {group: group_certificate(members, robust_cap) for group, members in groups.items() if "hull-pruned" in group}
    chart = write_chart(configurations, caps, certificates, robust_cap)
    centrograms_csv = write_centrogams_csv(configurations, caps, robust_cap)
    critical = [config for config in configurations if abs(caps[config.identifier] - robust_cap) < EPSILON]
    summary = {
        "total_configurations": len(configurations),
        "noncritical_pruned_configurations": len(configurations) - len(critical),
        "limiting_configurations": len(critical),
        "full_fuel_lb": FULL_FUEL,
        "mandatory_center_payload_lb": CENTER_PAYLOAD.weight,
        "mandatory_center_payload_station_in": CENTER_PAYLOAD.cg,
        "robust_fuel_cap_lb": robust_cap,
        "lost_fuel_lb": FULL_FUEL - robust_cap,
        "certificate_cap_lb": robust_cap,
        "certificate_groups": {group: {"configurations": len(groups[group]), "certified": result[0], "worst_quadratic_violation": result[1], "fuel_segments_checked": result[2]} for group, result in certificates.items()},
        "sources": {
            "faa_tcds_4a21": "https://www.scribd.com/document/206769306/Tcds-Boeing-707",
            "nasa_707_320b_context": "https://ntrs.nasa.gov/api/citations/19760008970/downloads/19760008970.pdf",
        },
        "safety_notice": "Educational illustration only; not approved aircraft loading or dispatch data.",
    }
    summary_path = OUTPUT / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {chart}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {centrograms_csv}")


if __name__ == "__main__":
    main()
