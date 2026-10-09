# WeightAndBalance — algorithm discussion and Codex handoff

Date: 2026-10-08  
Purpose: Preserve the brainstorming session so development can continue in Codex, starting with a demo and progressing toward a high-level design (HLD).

## 1. Goal and current status

Reduce computations for aircraft weight-and-balance validation across optional payloads, uncertainty endpoint choices, aircraft tail numbers, and fuel trajectories, without accepting an unsafe configuration.

The objective is to maximize usable fuel while guaranteeing validity for every modeled combination throughout the flight. Conservative intermediate bounds are acceptable for pruning, but the user does not want an arbitrary allowance for lost fuel capacity.

The selected direction is **branch-and-bound using convex hulls in weight–moment coordinates**. A whole family of combinations is eliminated when its hull proves that no member can reduce the current fuel cap. A hull failure triggers refinement or evaluation of a real combination; it does not itself justify reducing the final cap.

This is a proposed algorithm, not an implemented or benchmarked system. The conversation established its structure and mathematical checks; input conventions, numerical guarantees, and practical pruning performance still need development and validation.

## 2. Domain terminology

| Term | Meaning |
| --- | --- |
| Basic aircraft | Aircraft without fuel, configuration, or add-on items; described by weight, longitudinal CG, and lateral CG. Each tail number has its own basic data. |
| Item | Payload contribution with a weight and location along an aircraft axis; either or both may have tolerances. |
| Configuration | Selected collection of items for a flight. Some items are optional. |
| Fuel flow | Ordered weight–moment datapoints describing the fuel contribution as fuel quantity changes. |
| Loaded aircraft | Basic aircraft + configuration + fuel contribution. |
| Centrogram | All loaded-aircraft states along the fuel trajectory, from empty fuel to full fuel. |
| Envelope | Polygon of allowed aircraft weight and CG. |

## 3. Agreed scope and assumptions

- Envelopes are convex simple polygons. Bounds can change with weight; no assumption that the permitted CG interval always narrows as weight increases.
- Longitudinal and lateral envelopes are separate and checked independently. Initial development focuses on the longitudinal envelope.
- Item presence and parameter choices are independent. No dependencies or mutually exclusive item groups are currently required.
- Uncertainty is a **discrete endpoint model**. The user requires checking the bounds exclusively, not all values inside a tolerance interval.
- Basic-aircraft uncertainty was described using four cases: AFT-HEAVY, AFT-LIGHT, FWD-HEAVY, FWD-LIGHT, arising from relative weight and CG safety factors.
- Fuel datapoints are linearly interpolated. A supplied fuel flow represents the available tanks and is the trajectory to check in that run.
- Validity is required throughout the supplied fuel trajectory, including interpolation between datapoints.
- Scale: up to 20 items per mission, 10 tail numbers with shared limitations but different basic data, and up to 2 fuel flows to check.
- Fuel datapoint counts, envelope vertex counts, current runtime, and target latency have not been supplied.

Important distinction: the proposed continuous fuel check assumes interpolation in **fuel weight–moment coordinates**. The conversation identified this assumption, but the exact input/interpolation contract still needs to be made explicit in implementation.

The discrete endpoint requirement is the model to implement. The algorithm must not silently reinterpret it as a guarantee for all continuous weight/position uncertainties.

## 4. Why exhaustive evaluation grows rapidly

For item i, let S_i be its allowed contribution choices. A fixed optional item has two choices: absent or present. If weight and position each have two bounds, a present item can have four endpoint combinations, with absence adding a fifth choice when optional. Duplicate contributions can be removed.

With B basic-aircraft cases, the number of combinations per tail and fuel flow is approximately:

```text
B × product_i |S_i|
```

Twenty fixed optional items alone produce 2^20 = 1,048,576 combinations. Each combination is inexpensive individually; the product of independent choices is the bottleneck.

## 5. Additive state representation

Use weight W and longitudinal moment M, then compute CG x = M / W:

```text
W = W_basic + sum_i W_i + W_fuel(f)
M = W_basic × CG_basic + sum_i M_i + M_fuel(f)
M_i = weight_i × position_i
x = M / W
```

Total aircraft weight must remain positive. Units and datum conventions must be consistent.

For each item, enumerate its allowed discrete contributions:

```text
S_i = {(w, w × position) for allowed weight/position endpoint pairs}
```

Include (0, 0) if the item is optional. Mandatory items omit absence. Construct the basic-aircraft set from its specified endpoint cases in the same way.

## 6. Hull construction and reuse

Let P_i = convex_hull(S_i). Form the configuration polygon with a Minkowski sum:

```text
P_config = P_1 ⊕ P_2 ⊕ ... ⊕ P_n
P_tail = P_config ⊕ convex_hull(basic-aircraft cases for that tail)
```

Because contributions add independently, P_config is the convex hull of all discrete configuration sums. It contains every real configuration, while its interior can also contain unrealizable mixtures, such as partially present items.

In two dimensions, convex Minkowski sums have vertex counts bounded by the sum of input polygon vertex counts. For 20 fixed optional items, the configuration hull has at most 40 vertices, even though there are over a million combinations. This count is for the configuration hull only; basic-aircraft uncertainty and fuel sweeping add geometry.

For a fuel segment with endpoints F_j and F_(j+1), build:

```text
P_swept = P_tail ⊕ segment(F_j, F_(j+1))
```

This contains every modeled configuration throughout that fuel segment, under weight–moment interpolation. A cap inside a segment uses the appropriately truncated fuel segment.

Reuse the configuration hull across tail numbers and fuel flows. During branching, fixing or restricting item choices gives smaller hulls for the remaining combinations.

## 7. Certifying a hull against the envelope

Express each convex envelope edge as a half-plane in CG–weight coordinates:

```text
a × x + b × W <= c
```

Substitute x = M / W and multiply by positive W:

```text
q(W, M) = a × M + b × W² - c × W <= 0
```

The envelope is polygonal in CG–weight space, but generally becomes curved in weight–moment space. Therefore a generic point-in-polygon check on hull vertices is insufficient.

For each envelope half-plane, maximize q over the swept hull. For this quadratic, vertices and stationary maxima along hull edges suffice, with explicit handling for degenerate point/segment hulls. When a is nonzero, linear dependence on M puts a maximum on the boundary; when a is zero, the objective depends only on W, whose attainable range is also represented on the boundary.

For an edge parameterized by:

```text
W(t) = W0 + t × dW
M(t) = M0 + t × dM,    0 <= t <= 1
q(t) = A × t² + B × t + C
A = b × dW²
B = a × dM + (2 × b × W0 - c) × dW
C = a × M0 + b × W0² - c × W0
```

Check t = 0 and t = 1. If A < 0, also check t* = -B / (2A) when it lies inside the edge. Linear and constant cases require endpoints only.

- If every constraint's certified maximum is <= 0, the whole group is safe and can be pruned.
- If any maximum is positive, the group is unresolved. The maximizing hull state may be artificial; it is not automatically a real violation.
- For b >= 0, q is convex, so a maximum occurs at a hull vertex. Such vertices are realizable endpoint sums; this gives an exact shortcut for those constraints.
- For b < 0, an edge-interior maximum may require refinement. Interior fuel states can be real even when mixed configuration states are not.

Do not assume valid fuel datapoints imply a valid interpolated trajectory. Check the interior of each segment analytically.

## 8. Proposed branch-and-bound flow

1. Evaluate a few real combinations to establish a candidate fuel cap U. Each evaluated combination's own maximum safe prefix is an upper bound on the cap safe for all combinations; take their minimum.
2. Represent all unprocessed combinations as groups, each bounded by its weight–moment hull.
3. For a group, certify every required fuel segment up to U against every envelope edge.
4. If certification succeeds, discard the group. None of its combinations can lower U.
5. If certification fails, either evaluate a real candidate combination or split an unresolved item/basic-aircraft choice into smaller groups.
6. Lower U only using a real combination's analytically established fuel limit. Artificial hull violations trigger refinement.
7. Continue until every combination is covered by a certified group or resolved directly.

Previously certified groups remain certified when U decreases because the required fuel prefix shrinks.

For a real combination, use the same segment quadratic checks and roots to find the first violation boundary. Search over safe **prefixes** of the supplied trajectory, not merely safe isolated fuel quantities. Prefix feasibility is monotonic even if the trajectory exits and later re-enters the envelope.

If the required starting state is already invalid, report infeasibility; a cap of zero must not imply that the aircraft is safe.

Completion is intended to match exhaustive evaluation of the defined discrete choices and continuous fuel segments, within a stated, conservatively certified numerical precision. Worst-case branching remains exponential.

Branch selection, witness selection, caching, and queue ordering were not settled in the conversation. They are implementation decisions to explore in the demo.

## 9. Intersection, not union, for robust fuel limits

The original description used “union of worst-case calculations.” Clarification established that all modeled cases must be safe.

- Union describes the collection of possible aircraft states.
- Intersection describes fuel prefixes valid for every case.
- The robust cap is the minimum of the per-case maximum safe prefixes.

Whether the product should return separate caps per tail/flow, a single cap across all tails/flows, or both remains an output-contract decision. Do not silently impose a fleet-wide minimum when separate aircraft results are intended.

## 10. Complexity and numerical requirements

For T tails, S total fuel segments across the checked flows, E envelope edges, and H hull edges, the first-pass certificate costs approximately O(T × S × E × H), excluding hull construction. With constant-size item choice sets, H = O(n).

This is the cost of the initial hull pass, not a polynomial worst-case bound for the complete exact algorithm. Refinement may still enumerate every combination in difficult instances.

Numerical certification must prevent rounding from producing a false-safe result. Precision, boundary inclusion, edge normalization, root handling, and near-zero comparisons need explicit design. If refinement is interrupted by a time budget, return proven lower/upper bounds or an unresolved status rather than claiming an exact answer.

## 11. Suggested next work in Codex

These are proposed follow-up tasks, not features already implemented:

1. Define input/output schemas for envelopes, item endpoint choices, basic-aircraft cases, fuel flows, and cap results; settle units and interpolation.
2. Build an exhaustive reference solver for small instances, including analytic checks inside fuel segments.
3. Implement 2D convex hulls, convex Minkowski sums, and the quadratic hull certificate, preserving provenance where useful for real witnesses.
4. Add branch-and-bound with explicit exact/unresolved results and numerical bounds.
5. Build a demo showing the envelope, fuel trajectories, hulls, branch/pruning counts, and the real combination that limits fuel.
6. Compare against the reference solver, then benchmark the requested 20-item / 10-tail / 2-flow scale.

Validation should include fixed optional items, endpoint tolerances, arbitrary convex envelope slopes, artificial hull violations, valid segment endpoints with an invalid interior, degenerate hulls, boundary contact, invalid starting states, and shared-versus-separate output caps.

Useful metrics: total discrete combinations, resolved combinations, pruned groups, nodes explored, hull vertices, certificate evaluations, runtime, final cap, and disagreement or unresolved precision interval versus the reference solver.

## 12. Remaining decisions

- Exact fuel interpolation coordinates and fuel-quantity parameterization.
- Whether zero fuel is the required terminal state or the flight ends at a reserve/minimum fuel state.
- Separate or common results across tails and alternative fuel flows.
- Envelope boundary inclusion and the numerical precision contract.
- Representative real envelope vertices and fuel datasets for the demo.
- Fuel datapoint/envelope sizes and target runtime.
- Demo interface and implementation language; none was selected.

## 13. Reference pointers from the conversation

These were cited during brainstorming and are retained as background pointers; this handoff does not independently validate them or claim that they establish the proposed algorithm's correctness.

- [FAA Pilot's Handbook of Aeronautical Knowledge, Weight and Balance](https://www.faa.gov/sites/faa.gov/files/12_phak_ch10.pdf): weight, moment, and CG background.
- [CGAL 2D Minkowski Sums documentation](https://doc.cgal.org/latest/Minkowski_sum_2/index.html): geometric construction background.
- [Robinson R44 II operating limitations](https://robinsonstrapistorprod.blob.core.windows.net/uploads/assets/r44ii_poh_2_4c17ab4577.pdf#page=6): example weight–CG envelope referenced in the discussion.
- NASA public aeronautical documentation was suggested as a source of examples, but no specific dataset was selected.

## 14. Instruction for continuing development

Start from the agreed discrete endpoint model and arbitrary convex longitudinal envelope. Treat hulls as safe pruning bounds, refine artificial failures, and reduce the cap only with real modeled cases. Validate against exhaustive small cases before interpreting performance gains. Continue toward a demo and HLD; no production implementation or performance result exists yet.
