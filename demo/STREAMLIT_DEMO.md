# Interactive compact centrogram explorer

The Streamlit app visualises the **factorised convex-hull branch-and-bound**
algorithm. It does not calculate or draw every passenger centrogram first.

From the repository root, create an optional virtual environment and install
the UI dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r demo\requirements-streamlit.txt
```

Launch the explorer:

```powershell
py -m streamlit run demo\streamlit_app.py
```

The browser session begins with the inputs from
`data/boeing_707_124_demo.json`. Changes made in the form are session-local and
take effect only after selecting **Run compact search**.

## What it shows

For each moving passenger, the app retains the small set of allowed
weight--moment endpoint contributions. Rather than construct their Cartesian
product, it builds a root hull with Minkowski sums:

```text
H_root = H_fixed ⊕ conv(S_1) ⊕ ... ⊕ conv(S_n)
```

It evaluates a small set of real seed centrograms to establish candidate `U`.
Then it tests each compact group hull through every fuel segment up to `U`.

- A certified group is removed without calculating any centrogram in it.
- An unresolved group is split into smaller factorised groups; a hull failure is
  never treated as a real loading violation.
- A single real leaf centrogram is evaluated only when refinement reaches one.
  Only that real result may lower `U`.

The explorer contains three views:

- **Search overview** distinguishes real seed/leaf calculations from compact
  group decisions in the branch-and-bound trace.
- **Certificate on envelope** plots one selected group base hull, its most
  restrictive fuel sweep, and one real witness path.
- **Algorithm explanation** provides a visual flow from endpoint sets to
  certificates, splits, real witnesses, and the final decision.

The group-work budget is intentionally not a cap on the number of possible
configurations. If it is exhausted, the app reports the search as **unresolved**
and labels `U` only as a real-configuration upper bound.

`boeing_707_demo.py` remains the small exhaustive reference implementation for
cross-checking; it is not the calculation mode used by this app.

This is an educational demonstration, not an approved weight-and-balance,
dispatch, or fuel-planning tool.
