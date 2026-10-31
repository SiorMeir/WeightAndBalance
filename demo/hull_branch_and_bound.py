"""Compact convex-hull branch-and-bound solver for the Streamlit explorer.

The educational reference solver in :mod:`boeing_707_demo` deliberately
enumerates every passenger combination.  This module demonstrates the other
side of the project: it keeps a Cartesian product factorised, constructs group
hulls from the independent choice hulls, and only evaluates real centrograms
when a branch cannot be certified as a group.

It is still an educational implementation.  In particular, it uses ordinary
binary floating point with a small certificate tolerance and must never be used
for aircraft loading, dispatch, or flight operations.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import prod
from typing import Iterable, Sequence


EPSILON = 1e-7
# This only absorbs binary floating-point noise at a mathematical boundary.
# A real operational implementation needs an outward-rounded precision plan.
CERTIFICATE_TOLERANCE = 1e-4


@dataclass(frozen=True)
class State:
    """A longitudinal contribution in weight--moment coordinates."""

    weight: float
    moment: float

    @property
    def cg(self) -> float:
        return self.moment / self.weight

    def __add__(self, other: "State") -> "State":
        return State(self.weight + other.weight, self.moment + other.moment)


@dataclass(frozen=True)
class Scenario:
    """Validated input required by the compact solver."""

    envelope: tuple[tuple[float, float], ...]
    fixed_base: State
    choice_states: tuple[State, ...]
    choice_labels: tuple[str, ...]
    passenger_count: int
    fuel: tuple[State, ...]
    tank_names: tuple[str, ...]

    @property
    def full_fuel(self) -> float:
        return self.fuel[-1].weight


@dataclass(frozen=True)
class Group:
    """A factorised subset of the passenger Cartesian product."""

    allowed_choices: tuple[tuple[int, ...], ...]

    @property
    def combinations(self) -> int:
        return prod(len(choices) for choices in self.allowed_choices)

    @property
    def is_leaf(self) -> bool:
        return all(len(choices) == 1 for choices in self.allowed_choices)


@dataclass(frozen=True)
class SegmentCertificate:
    """One fuel segment's swept group hull and its largest constraint value."""

    name: str
    fuel_start: float
    fuel_end: float
    swept_hull: tuple[State, ...]
    worst_q: float


@dataclass(frozen=True)
class Certificate:
    """Proof attempt for one factorised group up to the current candidate cap."""

    base_hull: tuple[State, ...]
    segments: tuple[SegmentCertificate, ...]
    worst_q: float

    @property
    def certified(self) -> bool:
        return self.worst_q <= CERTIFICATE_TOLERANCE


@dataclass(frozen=True)
class SeedEvaluation:
    """A deliberately small, real configuration evaluation used to seed U."""

    choices: tuple[int, ...]
    cap: float
    feasible: bool


@dataclass(frozen=True)
class SearchRecord:
    """One visible node in the branch-and-bound trace."""

    node_id: int
    parent_id: int | None
    depth: int
    group: Group
    outcome: str
    cap_before: float
    cap_after: float
    hull_vertices: int
    fuel_segments: int
    worst_q: float | None
    note: str
    certificate: Certificate | None = None


@dataclass(frozen=True)
class SearchResult:
    """Result plus the complete compact-search trace for the visual explorer."""

    scenario: Scenario
    seeds: tuple[SeedEvaluation, ...]
    records: tuple[SearchRecord, ...]
    candidate_cap: float
    complete: bool
    pending_groups: int
    real_evaluations: int
    certified_groups: int
    split_groups: int
    node_budget: int
    witness_choices: tuple[int, ...]
    witness_cap: float


def _signed_area(points: Sequence[tuple[float, float]]) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))


def _normalise_envelope(points: Iterable[Sequence[float]]) -> tuple[tuple[float, float], ...]:
    envelope = tuple((float(point[0]), float(point[1])) for point in points)
    if len(envelope) < 3:
        raise ValueError("The envelope needs at least three CG / weight vertices.")
    if _signed_area(envelope) < 0:
        envelope = tuple(reversed(envelope))
    return envelope


def scenario_from_dict(data: dict) -> Scenario:
    """Build a compact-solver scenario from the editable Streamlit data."""

    inputs = data["illustrative_inputs"]
    basic = inputs["basic_aircraft"]
    pilots = inputs["stationary_pilots"]
    payload = inputs["mandatory_center_payload"]
    passenger = inputs["roaming_passengers"]

    basic_weight = float(basic["weight_lb"])
    pilot_weight = float(pilots["count"]) * float(pilots["weight_each_lb"])
    payload_weight = float(payload["weight_lb"])
    fixed_base = State(
        basic_weight + pilot_weight + payload_weight,
        basic_weight * float(basic["cg_station_in"])
        + pilot_weight * float(pilots["station_in"])
        + payload_weight * float(payload["station_in"]),
    )
    if fixed_base.weight <= EPSILON:
        raise ValueError("The fixed empty-fuel load must have positive weight.")

    weights = tuple(float(value) for value in passenger["weight_endpoints_lb"])
    stations = tuple(float(value) for value in passenger["station_endpoints_in"])
    if not weights or not stations:
        raise ValueError("Each passenger needs at least one weight and station endpoint.")
    if any(weight < 0 for weight in weights):
        raise ValueError("Passenger weight endpoints cannot be negative.")
    choice_states: list[State] = []
    choice_labels: list[str] = []
    for weight in weights:
        for station in stations:
            state = State(weight, weight * station)
            if state not in choice_states:
                choice_states.append(state)
                choice_labels.append(f"{weight:g} lb @ {station:g} in")
    if not choice_states:
        raise ValueError("No distinct passenger endpoint contributions were supplied.")

    tanks = sorted(data["fuel_tanks"], key=lambda tank: float(tank["fuel_sequence_order"]))
    if not tanks:
        raise ValueError("At least one fuel tank / fuel segment is required.")
    fuel: list[State] = [State(0.0, 0.0)]
    names: list[str] = []
    running = fuel[0]
    for tank in tanks:
        tank_weight = float(tank["usable_weight_lb"])
        if tank_weight < 0:
            raise ValueError("Usable fuel cannot be negative.")
        running = running + State(tank_weight, tank_weight * float(tank["moment_arm_in"]))
        fuel.append(running)
        names.append(str(tank["name"]))

    passenger_count = int(passenger["count"])
    if passenger_count < 1:
        raise ValueError("At least one moving passenger is required for this explorer.")
    return Scenario(
        envelope=_normalise_envelope(data["envelope"]["calculation_points_cg_station_in_gross_weight_lb"]),
        fixed_base=fixed_base,
        choice_states=tuple(choice_states),
        choice_labels=tuple(choice_labels),
        passenger_count=passenger_count,
        fuel=tuple(fuel),
        tank_names=tuple(names),
    )


def is_inside_envelope(scenario: Scenario, state: State, tolerance: float = EPSILON) -> bool:
    """Inclusive point-in-convex-polygon predicate in CG / gross-weight space."""

    if state.weight <= EPSILON:
        return False
    cg = state.cg
    for (x0, w0), (x1, w1) in zip(scenario.envelope, scenario.envelope[1:] + scenario.envelope[:1]):
        if (x1 - x0) * (state.weight - w0) - (w1 - w0) * (cg - x0) < -tolerance:
            return False
    return True


def quadratic_roots(a: float, b: float, c: float) -> list[float]:
    """Return the real roots, handling near-linear equations explicitly."""

    if abs(a) < EPSILON:
        return [] if abs(b) < EPSILON else [-c / b]
    discriminant = b * b - 4 * a * c
    if discriminant < -EPSILON:
        return []
    root = max(0.0, discriminant) ** 0.5
    return [(-b - root) / (2 * a), (-b + root) / (2 * a)]


def interpolate(start: State, end: State, fraction: float) -> State:
    return State(
        start.weight + (end.weight - start.weight) * fraction,
        start.moment + (end.moment - start.moment) * fraction,
    )


def segment_breaks(scenario: Scenario, start: State, end: State) -> list[float]:
    """Find every place a real centrogram can cross an envelope edge."""

    dw, dm = end.weight - start.weight, end.moment - start.moment
    breaks = [0.0, 1.0]
    for (x0, w0), (x1, w1) in zip(scenario.envelope, scenario.envelope[1:] + scenario.envelope[:1]):
        dx, dy = x1 - x0, w1 - w0
        alpha = dx * dw * dw
        beta = 2 * dx * start.weight * dw + (-dx * w0 + dy * x0) * dw - dy * dm
        gamma = dx * start.weight * start.weight + (-dx * w0 + dy * x0) * start.weight - dy * start.moment
        breaks.extend(root for root in quadratic_roots(alpha, beta, gamma) if EPSILON < root < 1 - EPSILON)
    return sorted(set(round(value, 12) for value in breaks))


def maximum_safe_fuel(scenario: Scenario, base: State) -> tuple[float, bool]:
    """Return the maximum safe real centrogram prefix and starting feasibility."""

    if not is_inside_envelope(scenario, base):
        return 0.0, False
    for fuel_start, fuel_end in zip(scenario.fuel, scenario.fuel[1:]):
        start, end = base + fuel_start, base + fuel_end
        breaks = segment_breaks(scenario, start, end)
        for left, right in zip(breaks, breaks[1:]):
            midpoint = interpolate(start, end, (left + right) / 2)
            if not is_inside_envelope(scenario, midpoint):
                return fuel_start.weight + left * (fuel_end.weight - fuel_start.weight), True
        if not is_inside_envelope(scenario, end):
            return fuel_start.weight, True
    return scenario.full_fuel, True


def centrogram(scenario: Scenario, base: State, up_to_fuel: float | None = None) -> tuple[State, ...]:
    """Return only the requested prefix of one real centrogram."""

    cap = scenario.full_fuel if up_to_fuel is None else max(0.0, min(up_to_fuel, scenario.full_fuel))
    states = [base]
    for fuel_start, fuel_end in zip(scenario.fuel, scenario.fuel[1:]):
        if fuel_start.weight >= cap - EPSILON:
            break
        if fuel_end.weight <= cap + EPSILON:
            states.append(base + fuel_end)
        else:
            fraction = (cap - fuel_start.weight) / (fuel_end.weight - fuel_start.weight)
            states.append(base + interpolate(fuel_start, fuel_end, fraction))
            break
    return tuple(states)


def cross(origin: State, first: State, second: State) -> float:
    return (first.weight - origin.weight) * (second.moment - origin.moment) - (first.moment - origin.moment) * (second.weight - origin.weight)


def convex_hull(points: Iterable[State]) -> tuple[State, ...]:
    """Monotone-chain hull in additive (weight, moment) coordinates."""

    unique = sorted({(round(point.weight, 9), round(point.moment, 9)) for point in points})
    states = [State(weight, moment) for weight, moment in unique]
    if len(states) <= 1:
        return tuple(states)
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
    return tuple(lower[:-1] + upper[:-1])


def minkowski_sum(left: Sequence[State], right: Sequence[State]) -> tuple[State, ...]:
    """Add two compact hull boundaries without expanding the choice product.

    Each right-hand hull is a passenger's few endpoint states (normally no more
    than four).  The temporary points are therefore hull-boundary pairs, never
    one point for every real passenger combination.
    """

    if not left or not right:
        return ()
    return convex_hull(first + second for first in left for second in right)


def group_hull(scenario: Scenario, group: Group) -> tuple[State, ...]:
    """Build ``fixed + sum(conv(allowed choices))`` directly from factors."""

    hull: tuple[State, ...] = (scenario.fixed_base,)
    for allowed in group.allowed_choices:
        contribution_hull = convex_hull(scenario.choice_states[index] for index in allowed)
        hull = minkowski_sum(hull, contribution_hull)
    return hull


def maximum_constraint_violation(scenario: Scenario, hull: Sequence[State]) -> float:
    """Maximise every transformed envelope constraint over a W--M hull."""

    if not hull:
        return float("inf")
    edges = list(zip(hull, hull[1:] + hull[:1])) if len(hull) > 1 else [(hull[0], hull[0])]
    worst = float("-inf")
    for (x0, w0), (x1, w1) in zip(scenario.envelope, scenario.envelope[1:] + scenario.envelope[:1]):
        dx, dy = x1 - x0, w1 - w0
        for start, end in edges:
            dw, dm = end.weight - start.weight, end.moment - start.moment
            # q = -cross(edge, (M/W, W)) * W.  q <= 0 is inside.
            a = -dx * dw * dw
            b = -(2 * dx * start.weight * dw + (-dx * w0 + dy * x0) * dw - dy * dm)
            c = -(dx * start.weight * start.weight + (-dx * w0 + dy * x0) * start.weight - dy * start.moment)
            candidates = [0.0, 1.0]
            if a < -EPSILON:
                stationary = -b / (2 * a)
                if EPSILON < stationary < 1 - EPSILON:
                    candidates.append(stationary)
            worst = max(worst, *(a * value * value + b * value + c for value in candidates))
    return worst


def certificate_for_group(scenario: Scenario, base_hull: tuple[State, ...], cap: float) -> Certificate:
    """Certify every state in a group through the requested fuel prefix."""

    base_violation = maximum_constraint_violation(scenario, base_hull)
    segments: list[SegmentCertificate] = [
        SegmentCertificate("empty fuel", 0.0, 0.0, base_hull, base_violation)
    ]
    worst = base_violation
    for index, (fuel_start, fuel_end) in enumerate(zip(scenario.fuel, scenario.fuel[1:])):
        if fuel_start.weight >= cap - EPSILON:
            break
        if fuel_end.weight <= cap + EPSILON:
            clipped_end = fuel_end
        else:
            fraction = (cap - fuel_start.weight) / (fuel_end.weight - fuel_start.weight)
            clipped_end = interpolate(fuel_start, fuel_end, fraction)
        swept_hull = convex_hull(
            base + fuel
            for base in base_hull
            for fuel in (fuel_start, clipped_end)
        )
        segment_worst = maximum_constraint_violation(scenario, swept_hull)
        worst = max(worst, segment_worst)
        segments.append(
            SegmentCertificate(
                scenario.tank_names[index],
                fuel_start.weight,
                clipped_end.weight,
                swept_hull,
                segment_worst,
            )
        )
    return Certificate(base_hull=base_hull, segments=tuple(segments), worst_q=worst)


def real_base(scenario: Scenario, choices: Sequence[int]) -> State:
    base = scenario.fixed_base
    for choice in choices:
        base = base + scenario.choice_states[choice]
    return base


def split_group(scenario: Scenario, group: Group) -> tuple[Group, Group, int]:
    """Split the broadest remaining factor, retaining compact child products."""

    candidates = [
        (len(allowed), index)
        for index, allowed in enumerate(group.allowed_choices)
        if len(allowed) > 1
    ]
    if not candidates:
        raise ValueError("Cannot split a group that already represents one real configuration.")
    _, split_index = max(candidates)
    allowed = group.allowed_choices[split_index]
    midpoint = len(allowed) // 2
    left_allowed, right_allowed = allowed[:midpoint], allowed[midpoint:]
    left = list(group.allowed_choices)
    right = list(group.allowed_choices)
    left[split_index] = left_allowed
    right[split_index] = right_allowed
    return Group(tuple(left)), Group(tuple(right)), split_index


def default_seed_choices(scenario: Scenario) -> tuple[tuple[int, ...], ...]:
    """Use a handful of real uniform endpoint loads to establish an upper bound."""

    return tuple(tuple(index for _ in range(scenario.passenger_count)) for index in range(len(scenario.choice_states)))


def group_summary(group: Group) -> str:
    """Readable compact factorisation, e.g. ``28×4 choices; 2×2 choices``."""

    counts = Counter(len(allowed) for allowed in group.allowed_choices)
    return " · ".join(f"{count} passenger{'s' if count != 1 else ''} × {options} choices" for options, count in sorted(counts.items(), reverse=True))


def run_branch_and_bound(scenario: Scenario, node_budget: int = 512) -> SearchResult:
    """Run an exact-if-complete compact branch-and-bound search.

    A hull can certify a group safe through ``U``.  A failed certificate is only
    unresolved, so it is split (or, at a leaf, evaluated as one real
    configuration).  ``U`` is lowered exclusively by a real centrogram.
    """

    if node_budget < 1:
        raise ValueError("The node budget must be at least one.")

    seeds: list[SeedEvaluation] = []
    candidate_cap = scenario.full_fuel
    witness_choices = default_seed_choices(scenario)[0]
    witness_cap = scenario.full_fuel
    for choices in default_seed_choices(scenario):
        cap, feasible = maximum_safe_fuel(scenario, real_base(scenario, choices))
        seeds.append(SeedEvaluation(choices=choices, cap=cap, feasible=feasible))
        if cap < candidate_cap - EPSILON:
            candidate_cap = cap
            witness_choices = choices
            witness_cap = cap
        elif abs(cap - candidate_cap) <= EPSILON and not feasible:
            witness_choices = choices
            witness_cap = cap

    root = Group(tuple(tuple(range(len(scenario.choice_states))) for _ in range(scenario.passenger_count)))
    queue: deque[tuple[int, int | None, int, Group]] = deque([(1, None, 0, root)])
    records: list[SearchRecord] = []
    next_node_id = 2
    real_evaluations = len(seeds)
    certified_groups = 0
    split_groups = 0

    while queue and len(records) < node_budget:
        node_id, parent_id, depth, group = queue.popleft()
        cap_before = candidate_cap
        base_hull = group_hull(scenario, group)
        certificate = certificate_for_group(scenario, base_hull, candidate_cap)
        if certificate.certified:
            certified_groups += 1
            records.append(
                SearchRecord(
                    node_id=node_id,
                    parent_id=parent_id,
                    depth=depth,
                    group=group,
                    outcome="certified",
                    cap_before=cap_before,
                    cap_after=candidate_cap,
                    hull_vertices=len(base_hull),
                    fuel_segments=len(certificate.segments),
                    worst_q=certificate.worst_q,
                    note="The whole factorised group is safe through U; no real centrogram in it is evaluated.",
                    certificate=certificate,
                )
            )
            continue

        if group.is_leaf:
            choices = tuple(allowed[0] for allowed in group.allowed_choices)
            leaf_cap, feasible = maximum_safe_fuel(scenario, real_base(scenario, choices))
            real_evaluations += 1
            if leaf_cap < candidate_cap - EPSILON:
                candidate_cap = leaf_cap
                witness_choices = choices
                witness_cap = leaf_cap
                note = "Real leaf centrogram lowers U. Earlier certificates remain valid for the shorter prefix."
            else:
                note = "Real leaf centrogram does not lower U."
            records.append(
                SearchRecord(
                    node_id=node_id,
                    parent_id=parent_id,
                    depth=depth,
                    group=group,
                    outcome="real evaluation",
                    cap_before=cap_before,
                    cap_after=candidate_cap,
                    hull_vertices=len(base_hull),
                    fuel_segments=len(certificate.segments),
                    worst_q=certificate.worst_q,
                    note=note if feasible else "This real zero-fuel state is infeasible, so U is zero.",
                    certificate=certificate,
                )
            )
            continue

        left, right, split_index = split_group(scenario, group)
        split_groups += 1
        records.append(
            SearchRecord(
                node_id=node_id,
                parent_id=parent_id,
                depth=depth,
                group=group,
                outcome="split",
                cap_before=cap_before,
                cap_after=candidate_cap,
                hull_vertices=len(base_hull),
                fuel_segments=len(certificate.segments),
                worst_q=certificate.worst_q,
                note=(
                    f"Hull is unresolved, so passenger {split_index + 1}'s allowed endpoints are divided. "
                    "No fuel limit is inferred from the hull failure."
                ),
                certificate=certificate,
            )
        )
        queue.append((next_node_id, node_id, depth + 1, left))
        queue.append((next_node_id + 1, node_id, depth + 1, right))
        next_node_id += 2

    return SearchResult(
        scenario=scenario,
        seeds=tuple(seeds),
        records=tuple(records),
        candidate_cap=candidate_cap,
        complete=not queue,
        pending_groups=len(queue),
        real_evaluations=real_evaluations,
        certified_groups=certified_groups,
        split_groups=split_groups,
        node_budget=node_budget,
        witness_choices=witness_choices,
        witness_cap=witness_cap,
    )
