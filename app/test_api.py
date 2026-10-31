from __future__ import annotations

import copy
import json
import logging
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent
REPOSITORY = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import api
import wb_core as wb


def light_twin_payload() -> dict:
    return json.loads((REPOSITORY / "examples" / "light_twin.json").read_text(encoding="utf-8"))


def toy_payload() -> dict:
    return {
        "envelope": [
            {"cg": 10, "weight": 80},
            {"cg": 14, "weight": 80},
            {"cg": 20, "weight": 200},
            {"cg": 10, "weight": 200},
        ],
        "tails": [{"id": "N-TOY", "weight": 100, "cg": 14.9}],
        "items": [{"name": "Optional cargo", "weight": 80, "cg": 24, "optional": True}],
        "fuel_flow": [{"weight": 0, "moment": 0}, {"weight": 15, "moment": 150}],
    }


def centrogram_identity(centrogram: dict) -> tuple:
    return (
        centrogram["tail"],
        tuple((item["name"], item["present"], item["weight"], item["cg"]) for item in centrogram["items"]),
    )


def test_light_twin_matches_solver_and_reports_solver_derived_counts():
    payload = light_twin_payload()
    response = TestClient(api.app).post("/standard-calc", json=payload)

    assert response.status_code == 200
    data = response.json()
    expected = wb.solve(api.build_problem(api.CalcRequest.model_validate(payload)))
    assert data["status"] == "ok"
    assert data["fuel_cap"] == expected.cap == 320
    assert data["full_fuel"] == expected.full == 550
    assert data["stats"]["loadings"] == expected.total == 64
    assert data["stats"]["exact_evaluations"] == expected.exact_evals
    assert len(data["critical_centrograms"]) == expected.exact_evals
    assert data["limiting"]["binding"] is True
    assert set(data["limiting"]["limited_by_edge"]) == {"from", "to"}
    assert response.headers["X-Request-ID"] == data["request_id"]


def test_toy_preset_and_multiple_tails_use_a_single_fleet_cap():
    client = TestClient(api.app)
    assert client.post("/standard-calc", json=toy_payload()).json()["fuel_cap"] == 15

    payload = toy_payload()
    payload["tails"].append({"id": "N-HEAVY", "weight": 110, "cg": 14.9})
    combined = client.post("/standard-calc", json=payload).json()
    individual = []
    for tail in payload["tails"]:
        single_tail = copy.deepcopy(payload)
        single_tail["tails"] = [tail]
        individual.append((tail["id"], client.post("/standard-calc", json=single_tail).json()["fuel_cap"]))

    expected_tail, expected_cap = min(individual, key=lambda item: item[1])
    assert combined["fuel_cap"] == expected_cap
    assert combined["limiting"]["tail"] == expected_tail


def test_all_centrograms_contains_every_loading_and_the_critical_ones():
    payload = light_twin_payload()
    payload["options"]["all_centrograms"] = True
    data = TestClient(api.app).post("/standard-calc", json=payload).json()

    assert len(data["all_centrograms"]) == 64
    assert min(centrogram["cap"] for centrogram in data["all_centrograms"]) == data["fuel_cap"]
    all_ids = {centrogram_identity(centrogram) for centrogram in data["all_centrograms"]}
    assert {centrogram_identity(centrogram) for centrogram in data["critical_centrograms"]} <= all_ids


def test_validation_and_domain_errors_share_the_error_envelope():
    client = TestClient(api.app)
    cases = []

    bad_first_fuel = light_twin_payload()
    bad_first_fuel["fuel_flow"][0]["weight"] = 1
    cases.append((bad_first_fuel, "invalid_request"))

    repeated_fuel = light_twin_payload()
    repeated_fuel["fuel_flow"][1]["weight"] = 0
    cases.append((repeated_fuel, "invalid_request"))

    invalid_delta = light_twin_payload()
    invalid_delta["items"][0]["weight_delta"] = 361
    cases.append((invalid_delta, "invalid_request"))

    duplicate_item = light_twin_payload()
    duplicate_item["items"][1]["name"] = duplicate_item["items"][0]["name"]
    cases.append((duplicate_item, "invalid_request"))

    missing_tails = light_twin_payload()
    del missing_tails["tails"]
    cases.append((missing_tails, "invalid_request"))

    non_convex = light_twin_payload()
    non_convex["envelope"] = [
        {"cg": 10, "weight": 80}, {"cg": 20, "weight": 80}, {"cg": 15, "weight": 100},
        {"cg": 20, "weight": 200}, {"cg": 10, "weight": 200},
    ]
    cases.append((non_convex, "invalid_input"))

    infeasible = light_twin_payload()
    infeasible["tails"][0]["cg"] = 1_000
    cases.append((infeasible, "infeasible_at_zero_fuel"))

    for payload, expected_code in cases:
        response = client.post("/standard-calc", json=payload)
        data = response.json()
        assert response.status_code == 422
        assert data["status"] == "error"
        assert data["error"]["code"] == expected_code
        assert data["request_id"]
        assert data["timing"]["total_ms"] is not None


def test_all_centrograms_limit_and_internal_errors_are_safe(monkeypatch):
    client = TestClient(api.app, raise_server_exceptions=False)
    payload = toy_payload()
    payload["items"] = [
        {"name": f"optional {index}", "weight": 1, "cg": 14, "optional": True}
        for index in range(16)
    ]
    payload["options"] = {"all_centrograms": True}
    response = client.post("/standard-calc", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_many_centrograms"

    def explode(*_args, **_kwargs):
        raise RuntimeError("secret trace text")

    monkeypatch.setattr(api.wb, "solve", explode)
    response = client.post("/standard-calc", json=light_twin_payload())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret trace text" not in response.text


def test_incomplete_result_and_timing():
    payload = light_twin_payload()
    payload["options"]["max_steps"] = 1
    data = TestClient(api.app).post("/standard-calc", json=payload).json()

    assert data["status"] == "incomplete"
    assert data["error"] is None
    assert data["fuel_cap"] is None
    assert data["fuel_cap_upper_bound"] >= 320
    assert 0 <= data["timing"]["solve_ms"] <= data["timing"]["total_ms"]
    assert isinstance(data["timing"]["centrograms_ms"], float)


def test_debug_logs_include_request_id_for_solver_steps(caplog):
    caplog.set_level(logging.DEBUG)
    response = TestClient(api.app).post("/standard-calc", json=light_twin_payload())
    data = response.json()
    solver_records = [record for record in caplog.records if record.name == "wb_core"]
    step_records = [record for record in solver_records if record.getMessage().startswith("step=")]

    assert len(step_records) == data["stats"]["steps"]
    assert any(record.getMessage().startswith("seed ") for record in solver_records)
    assert all(record.request_id == data["request_id"] for record in solver_records)


def test_solver_self_check_still_passes():
    completed = subprocess.run(
        [sys.executable, str(APP_DIR / "wb_core.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.rstrip().endswith("ok")


def test_web_examples_preserve_existing_preset_results():
    client = TestClient(api.app)
    examples = client.get("/ui/examples").json()
    assert [example["id"] for example in examples] == ["light-twin", "boeing-707", "toy"]
    presets = [wb.preset_light_twin(), wb.preset_707(), wb.preset_toy()]
    for example, preset in zip(examples, presets):
        problem = api.build_problem(api.CalcRequest.model_validate(example["request"]))
        reference = wb.problem_from_rows(**preset)
        assert problem.total == reference.total
        assert problem.full == reference.full
        assert problem.envelope == reference.envelope
        assert abs(wb.solve(problem).cap - wb.solve(reference).cap) < 1e-6
        # Compare every loading's zero-fuel state, including fixed loads moved into items.
        def bases(p):
            return sorted(tuple(round(v, 6) for v in p.base_of(cfg)) for cfg in api.itertools.product(*(range(len(s)) for s in p.slot_states)))
        assert bases(problem) == bases(reference)


def test_web_assets_and_examples_work_under_proxy_prefix():
    for prefix in ("", "/bakat/api"):
        client = TestClient(api.app, root_path=prefix)
        page = client.get(prefix + "/ui/")
        assert page.status_code == 200
        assert 'href="styles.css"' in page.text
        assert 'src="app.mjs"' in page.text
        assert client.get(prefix + "/ui/examples").status_code == 200
        for asset, expected_type in [("styles.css", "text/css"), ("app.mjs", "javascript"), ("model.mjs", "javascript"), ("chart.mjs", "javascript"), ("plotly.min.js", "javascript")]:
            response = client.get(prefix + "/ui/" + asset)
            assert response.status_code == 200
            assert expected_type in response.headers["content-type"]
        assert client.post(prefix + "/standard-calc", json=light_twin_payload()).json()["fuel_cap"] == 320
        assert client.get(prefix + "/docs").status_code == 200
