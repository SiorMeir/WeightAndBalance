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

## Safety

This repository is an algorithm demonstration only. Its aircraft inputs include deliberately illustrative values and must not be used for real aircraft loading, dispatch, or flight operations. See [the demo notes](demo/README.md) for source provenance and modelling limits.
