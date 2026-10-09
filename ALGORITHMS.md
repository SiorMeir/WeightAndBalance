# The algorithm behind fewer weight-and-balance calculations

> **One safe fuel decision, without blindly checking every loading combination.**

This project demonstrates a robust longitudinal weight-and-balance algorithm for
aircraft configurations with discrete loading uncertainty. Instead of evaluating
every passenger, payload, and basic-aircraft endpoint combination independently,
it uses **convex hulls** to certify entire families of configurations at once.

The result is exact with respect to the declared discrete input model: a fuel cap
is reduced only when a *real* configuration establishes that limit. A conservative
hull is used to prove safety and prune work; an artificial point inside a hull is
never treated as a real unsafe aircraft.

> **Safety notice**  
> This is an educational algorithm demonstration. It is not approved for aircraft
> loading, dispatch, or flight operations. The Boeing 707 data in this repository
> includes illustrative inputs and must not be used operationally.

## The challenge

For each item, a loading model may allow several discrete choices: an optional
item may be absent or present; a passenger may have a light/heavy weight endpoint
and a forward/aft station endpoint. With 20 optional binary items there are
`2^20 = 1,048,576` combinations before considering basic-aircraft cases, tails,
or fuel flows.

Every possible configuration must be safe for the whole fuel trajectory. The
robust usable-fuel cap is therefore the **intersection** of the safe prefixes for
all modeled cases:

```text
robust cap = min(maximum safe fuel prefix for each real configuration)
```

The central question is: *which of those million calculations can we prove will
not change that minimum?*

## Input contract and assumptions

The algorithm's result is only as meaningful as its input contract. It makes the
following assumptions explicitly.

| Input | Required representation | Assumption used by the algorithm |
| --- | --- | --- |
| **Envelope** | A simple, convex polygon of `(CG station, gross weight)` vertices, in a consistent winding order. | The boundary is inclusive. Longitudinal and lateral envelopes are independent checks; the current demo implements the longitudinal one. A non-convex approved envelope must be split into appropriate convex regions or handled by a different certificate. |
| **Basic aircraft** | One or more discrete `(weight, CG)` endpoint cases for each tail number. | Examples include AFT-HEAVY, AFT-LIGHT, FWD-HEAVY, and FWD-LIGHT. Each is a real case to check, not a continuous uncertainty range. |
| **Items / payload** | A finite set of allowed endpoint contributions per item, each stored as `(weight, moment)`. | Item choices are independent. For an item at station `x`, `moment = weight × x`. Optional items include `(0, 0)`; mandatory items do not. Duplicate contributions may be removed. |
| **Fuel flow** | Ordered fuel waypoints in `(fuel weight, fuel moment)` coordinates. | Waypoints are linearly interpolated **in weight–moment space**. The supplied order is the fuel trajectory that must remain safe; it is not inferred from tank geometry. More than one flow may be checked. |
| **Centrogram** | Every loaded-aircraft state along one configuration's fuel trajectory. | Safety is required at every waypoint *and between waypoints*. The algorithm analytically checks segment interiors, not just sampled points. |
| **Units and datum** | A single positive weight unit and a single longitudinal datum/station convention. | The mathematics is unit-agnostic, but all weights, stations, and moments must share units and datum. Total aircraft weight must remain positive. |
| **Numerics** | A documented precision/tolerance policy. | A configuration is never reported safe because of an uncontrolled round-off error. Near-boundary cases require conservative certification or an unresolved result. |

### What this model does not assume

- It does not claim every value inside a tolerance range is safe. The modeled
  uncertainty is the declared **discrete endpoint set**.
- It does not assume an envelope's allowable CG interval monotonically narrows
  with weight.
- It does not assume safe fuel waypoints imply a safe line between them.
- It does not treat the union of possible states as the answer; robustness is an
  intersection of the safe fuel prefixes.

## Base calculation: one real configuration

Weight and moment add linearly. Let the basic aircraft, chosen items, and fuel at
a particular point on its trajectory be represented as `(W, M)` pairs:

```text
W_total = W_basic + Σ W_item + W_fuel
M_total = W_basic × CG_basic + Σ (W_item × station_item) + M_fuel
CG       = M_total / W_total
```

The base solver checks this centrogram from the required low/zero-fuel state up
to successively higher fuel quantities. On every fuel segment it finds any
envelope-boundary crossings analytically, then returns the end of the longest
safe **prefix**. Prefixes matter: a trajectory that leaves the envelope and later
returns is still not acceptable for the intervening flight.

If the starting state is outside the envelope, the case is infeasible; a cap of
zero must never be misrepresented as a safe result.

## The reduction idea: add in weight–moment space

CG itself is a ratio, but weight and moment are additive. That makes `(W, M)` the
right coordinate system for grouping choices.

For each item `i`, construct the convex hull of its allowed discrete contributions:

```text
P_i = convex_hull(S_i)
P_configuration = P_1 ⊕ P_2 ⊕ ... ⊕ P_n
```

`⊕` is the Minkowski sum. `P_configuration` encloses every real sum of item
choices. Add a tail's basic-aircraft hull, then sweep it across each fuel segment:

```text
P_tail  = P_configuration ⊕ convex_hull(basic_cases_for_tail)
P_swept = P_tail ⊕ segment(fuel_j, fuel_j+1)
```

If `P_swept` is certified inside the envelope up to the current cap, every real
configuration in that group is safe there. The group can be discarded without
enumerating its members.

An enclosing hull can contain mathematically possible but operationally unreal
mixtures (for example, “half of an optional item”). That is why a hull failure is
only **unresolved**: it prompts a split or a real calculation, never an automatic
fuel reduction.

## Certifying the curved constraint

An envelope edge can be written in CG–weight coordinates as a half-plane:

```text
a × CG + b × W ≤ c
```

Substitute `CG = M / W` and multiply by positive `W`:

```text
q(W, M) = a × M + b × W² - c × W ≤ 0
```

An envelope that is straight in CG–weight space is generally curved in
weight–moment space. Checking only hull vertices can therefore be unsound. For
each hull edge parameterized by `t ∈ [0, 1]`, `q(t)` is quadratic:

```text
W(t) = W0 + t × dW
M(t) = M0 + t × dM

q(t) = A × t² + B × t + C
A = b × dW²
B = a × dM + (2 × b × W0 - c) × dW
C = a × M0 + b × W0² - c × W0
```

The maximum occurs at an endpoint or, when `A < 0`, at the stationary point
`-B / (2A)` if it lies on the edge. Checking those candidates for every swept-hull
edge and every envelope edge is a certificate for the whole group.

## Branch-and-bound pseudocode

```text
function robustFuelCap(tails, fuelFlows, envelope, itemChoices):
    # A real evaluation establishes a candidate upper bound. Never lower it
    # from a hull-only violation.
    U = min(realMaximumSafePrefix(seedConfiguration, each tail/flow))
    queue = [Group(all allowed choices for every item and basic-aircraft case)]

    while queue is not empty:
        group = queue.pop()
        certified = true

        for each applicable tail and fuel flow:
            hull = hullOfAllGroupBaseStates(group, tail)
            for each fuel segment clipped to prefix U:
                sweptHull = hull ⊕ segment(fuelSegment)
                if maxTransformedEnvelopeConstraint(sweptHull, envelope) > 0:
                    certified = false
                    break

        if certified:
            continue  # The entire group cannot lower U.

        if group is a single real configuration:
            U = min(U, realMaximumSafePrefix(group, each tail/flow))
            continue

        if a useful real witness is available:
            U = min(U, realMaximumSafePrefix(witness, each tail/flow))

        queue.push(split(group, selectMostUsefulUnresolvedChoice(group)))

    return U
```

When `U` becomes smaller, already certified work remains valid because the required
fuel prefix only shrinks. A production implementation should retain certificates,
cache hulls, choose splits deliberately, and return proven bounds or `unresolved`
if a time budget expires.

## Complexity at a glance

Let:

- `n` be the number of items;
- `k` be the maximum number of discrete contributions for one item;
- `T` be the number of tail numbers;
- `F` be the number of fuel segments across the checked flows;
- `E` be the number of envelope edges; and
- `H` be the number of edges in a group hull.

| Approach | Work | Meaning |
| --- | --- | --- |
| Exhaustive baseline | `O(T × F × E × k^n)` | Every real configuration is evaluated. With binary options, this is exponential in `n`. |
| One hull certificate | `O(T × F × E × H)` | Each constraint is maximized over every swept-hull edge. For constant-size choice hulls, a 2D Minkowski sum has `H = O(n)`. |
| Complete branch-and-bound | Best case close to the hull pass; worst case `O(T × F × E × k^n)` | Refinement preserves exactness, so adversarial inputs can still force exhaustive work. The win is practical pruning, not a promise that an exponential problem becomes polynomial. |

For example, 20 optional binary items have 1,048,576 real combinations, while a
configuration hull assembled from simple two-vertex choices has only linear-scale
boundary complexity. The demo shows the same principle on 256 passenger endpoint
configurations.

## Lean example: proving an item contribution is represented

The following small Lean 4 theorem captures the additive representation used by
the algorithm: an item's moment is its weight times its station, and its CG is
recoverable from positive weight and moment. It is intentionally a compact
building block rather than a full formal proof of the branch-and-bound solver.

```lean
import Mathlib

structure Load where
  weight : ℝ
  moment : ℝ

def contribution (weight station : ℝ) : Load :=
  ⟨weight, weight * station⟩

def cg (load : Load) : ℝ := load.moment / load.weight

theorem contribution_cg
    (weight station : ℝ) (hweight : weight ≠ 0) :
    cg (contribution weight station) = station := by
  dsimp [cg, contribution]
  apply (div_eq_iff hweight).2
  ring
```

A next formalization step would define a convex envelope as half-planes, prove
that the Minkowski sum contains each discrete sum, and prove that a negative
maximum of every transformed quadratic constraint certifies the swept hull.

## Demonstrated scenario

The included Boeing 707-124 demo uses four passengers, each choosing one of four
weight/station endpoints: `4^4 = 256` real configurations. It separates the
all-heavy limiting group from four groups whose centrograms are certified and
pruned. The chart, inputs, source-code comments, and generated summary are all
available from the project home page.

Run it locally with:

```powershell
python demo/boeing_707_demo.py
```

For the full design discussion and open implementation decisions, see
[WEIGHT_AND_BALANCE_HANDOFF.md](WEIGHT_AND_BALANCE_HANDOFF.md).
