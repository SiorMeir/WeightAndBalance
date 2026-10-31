# Weight and Balance

An educational Python demonstration of longitudinal aircraft weight-and-balance analysis using convex-hull pruning in weight–moment coordinates.

The repository has two complementary Boeing 707 demonstrations:

- `demo/boeing_707_demo.py` is a small exhaustive reference solver. It draws every centrogram and is useful for validating the compact algorithm on manageable cases.
- `demo/streamlit_app.py` is the interactive convex-hull branch-and-bound explorer. It keeps passenger choices factorised, certifies whole groups through Minkowski-sum hulls, and draws only selected hulls and real witnesses.

Read [the algorithm guide](ALGORITHMS.md) for the input model, certification math,
pseudocode, complexity analysis, and a Lean 4 building-block example. The same
guide is presented as a readable visual overview on the project's GitHub Pages
home page.

## Run

```powershell
python demo/boeing_707_demo.py
```

The script reads its complete 707 input model from [`data/boeing_707_124_demo.json`](data/boeing_707_124_demo.json), then regenerates the SVG visualization, JSON summary, and full centrogram CSV export in `demo/output/`.

## Run the API

The stateless API accepts a complete calculation in each request. It is an
educational demonstration only, not approved for aircraft loading, dispatch, or
flight operations.

```bash
pip install -r app/requirements.txt
LOG_LEVEL=DEBUG uvicorn api:app --app-dir app --port 8000
```

Open `http://localhost:8000/docs` for the interactive schema. The following
examples assume the server is running on port 8000.

Calculate the Light Twin example:

```bash
curl -sS http://localhost:8000/standard-calc \
  -H 'content-type: application/json' \
  --data-binary @examples/light_twin.json
```

Inspect the response headers, including `X-Request-ID`:

```bash
curl -sS -i http://localhost:8000/standard-calc \
  -H 'content-type: application/json' \
  --data-binary @examples/light_twin.json
```

See the structured validation-error envelope with an invalid request:

```bash
curl -sS -i http://localhost:8000/standard-calc \
  -H 'content-type: application/json' \
  --data '{"envelope": []}'
```

Run the API test suite with:

```bash
pytest app/test_api.py
```

## Centrogram webapp

The dedicated frontend is served by the same API process. After installing
`app/requirements.txt`, start it with:

```powershell
python -m uvicorn api:app --app-dir app --port 8000
```

Open [the centrogram explorer](http://localhost:8000/ui/). It starts with the
Light Twin inputs without running a calculation. Boeing 707 and toy presets are
also available from `GET /ui/examples`, using the existing demonstration data.

Expand an input section to edit every REST request field. Import and export use
the `POST /standard-calc` JSON request format; edits stay in browser memory until
exported. Units are shown for known examples and remain unspecified for imported
requests. Use one consistent weight/CG/datum system and true weight × arm moments.

Run simulations explicitly. The chart shows critical centrograms by default;
request all centrograms in Simulation options to enable the all-loading view
(API limit: 10,000 loadings). Hover for point and component values, or click a
curve / use the loading selector to pin its details. Solid paths end at each
loading's own cap; dashed paths continue beyond it. Zooming resamples the curves
in weight–moment space. An incomplete solve displays an **unproven upper bound**.
Editing inputs marks the previous result stale; its chart retains the submitted
input snapshot.

The timing strip separates solver, centrogram construction, API total, and
browser request duration. Browser duration includes response parsing and excludes
chart drawing; API total is the API's existing measurement, before JSON response
serialization and transfer. There is no run cache or automatic rerun.

The frontend uses plain JavaScript and the installed Plotly package's bundled
JavaScript, without a Node build step or CDN. When deployed behind an ingress,
the webapp is available at `<api-prefix>/ui/`; the Streamlit demos remain available separately.
The Docker image includes the web assets and example requests.

Run the API and frontend calculation checks with:

```powershell
python -m pytest app/test_api.py
node --test app/web/model.test.mjs
```

For the compact interactive explorer, install `demo/requirements-streamlit.txt` and run:

```powershell
py -m streamlit run demo\streamlit_app.py
```

## Deploy to k3s

The included manifests deploy the stateless API behind the k3s-bundled Traefik
ingress controller in the `weight-and-balance` namespace. Configure
`deploy/k8s/api.yaml` with your container image and ingress hostname before deploying.

Build and publish the image from the repository root, replacing the example
registry and tag with your own:

```powershell
$image = "registry.example.com/weight-and-balance:v1"
docker build --platform linux/amd64 --tag $image .
docker push $image
```

For subsequent releases, choose a new image tag and update `deploy/k8s/api.yaml`
to match before applying.

The ingress exposes the API schema at `<api-prefix>/docs`, the webapp at
`<api-prefix>/ui/`, and calculations at `POST <api-prefix>/standard-calc`.
If changing the prefix, keep the ingress path, strip-prefix middleware, and
Uvicorn `--root-path` setting in sync. Point your hostname's DNS record at the
Traefik ingress endpoint. Then
apply and wait for the rollout:

```powershell
kubectl apply -k deploy/k8s
kubectl -n weight-and-balance rollout status deployment/weight-and-balance-api
```

The ingress intentionally starts as HTTP on Traefik's `web` entrypoint. Add
your cluster's TLS/certificate configuration before exposing it to the public
internet.

## Safety

This repository is an algorithm demonstration only. Its aircraft inputs include deliberately illustrative values and must not be used for real aircraft loading, dispatch, or flight operations. See [the demo notes](demo/README.md) for source provenance and modelling limits.
