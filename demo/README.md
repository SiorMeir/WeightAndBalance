# Boeing 707 centrogram demo

Run the dependency-free demonstration from the repository root:

```powershell
python demo/boeing_707_demo.py
```

It produces:

- `demo/output/boeing_707_centograms.svg` — the Case I envelope and all 256 passenger centrograms.
- `demo/output/summary.json` — cap, group certification, and pruning counts.
- `demo/output/centrograms.csv` — every full centrogram waypoint, its passenger choices, and its `critical`/`non-critical` classification. `within_configuration_cap` distinguishes a globally non-critical path from a waypoint beyond that configuration's own safe cap.

## Model

Four passengers independently choose one of the discrete endpoint contributions:

| Choice | Value |
| --- | --- |
| Weight | 185 or 225 lb |
| Longitudinal station | 650 or 1,050 in aft of datum |

Two pilots are stationary and an illustrative mandatory 8,000 lb bulk payload is placed at station 850 in (the model's nominal CG). There are therefore `4^4 = 256` real passenger combinations. This extra payload makes every full-fuel path exceed the maximum-weight boundary, while preserving the purpose of the passenger-position comparison.

The demo constructs five disjoint branch-and-bound groups: the first passenger who is light (P1 through P4), plus the all-heavy group. Each light group contains 16–128 real configurations and is bounded with a convex hull in weight–moment space. The certificate maximizes the transformed quadratic envelope constraint on each swept hull edge, including an interior stationary maximum when one exists.

The educational implementation permits `1e-4` in its transformed quadratic certificate solely to absorb binary floating-point roundoff on a mathematical boundary. It is not a production numerical-precision contract.

## Data provenance and safety

The Case I longitudinal envelope, 707 datum, and the 707-124 tank capacities and arms are transcribed from the public FAA Type Certificate Data Sheet **4A21 Revision 8** (1 May 1973): [public copy](https://www.scribd.com/document/206769306/Tcds-Boeing-707). The table has a small non-convex forward-limit notch, so the code uses a conservative convex inner subset, matching the algorithm's convex-envelope assumption. The NASA 707-320B study provides independent public context for the 170,000–247,000 lb operating-weight range: [NASA NTRS PDF](https://ntrs.nasa.gov/api/citations/19760008970/downloads/19760008970.pdf).

The basic aircraft state, crew weights/stations, center payload, passenger loading area, and fuel burn ordering are intentionally illustrative. The latter is a linearly interpolated trajectory built from public tank aggregate data, **not** an approved 707 fuel-use schedule. This is an algorithm demonstration only; it must never be used to load, dispatch, or operate an aircraft.
