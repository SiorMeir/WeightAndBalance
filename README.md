# Weight and Balance

An educational Python demonstration of longitudinal aircraft weight-and-balance analysis using convex-hull pruning in weight–moment coordinates.

The current Boeing 707 example enumerates four passenger endpoint choices, draws every centrogram, and distinguishes the configurations that determine the robust fuel cap from those safely pruned by a hull certificate.

## Run

```powershell
python demo/boeing_707_demo.py
```

The script regenerates the SVG visualization, JSON summary, and full centrogram CSV export in `demo/output/`.

## Safety

This repository is an algorithm demonstration only. Its aircraft inputs include deliberately illustrative values and must not be used for real aircraft loading, dispatch, or flight operations. See [the demo notes](demo/README.md) for source provenance and modelling limits.
