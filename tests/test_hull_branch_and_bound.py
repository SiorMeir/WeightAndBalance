"""Regression checks for the compact search against the small exhaustive oracle."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

import boeing_707_demo as exhaustive  # noqa: E402
import hull_branch_and_bound as compact  # noqa: E402


class CompactSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads((ROOT / "data" / "boeing_707_124_demo.json").read_text(encoding="utf-8"))

    def test_default_compact_result_matches_the_exhaustive_reference(self) -> None:
        result = compact.run_branch_and_bound(compact.scenario_from_dict(self.data), node_budget=512)
        reference_cap = min(exhaustive.maximum_safe_fuel(config.base) for config in exhaustive.make_configurations())

        self.assertTrue(result.complete)
        self.assertAlmostEqual(result.candidate_cap, reference_cap, places=5)
        self.assertLess(result.real_evaluations, len(exhaustive.make_configurations()))
        self.assertGreater(result.certified_groups, 0)

    def test_many_passengers_stay_factorised(self) -> None:
        scenario_data = copy.deepcopy(self.data)
        scenario_data["illustrative_inputs"]["roaming_passengers"]["count"] = 30
        result = compact.run_branch_and_bound(compact.scenario_from_dict(scenario_data), node_budget=64)

        self.assertEqual(len(result.scenario.choice_states) ** result.scenario.passenger_count, 4**30)
        self.assertTrue(result.complete)
        self.assertEqual(result.real_evaluations, 4)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].outcome, "certified")

    def test_exhausted_budget_is_honestly_unresolved(self) -> None:
        result = compact.run_branch_and_bound(compact.scenario_from_dict(self.data), node_budget=1)

        self.assertFalse(result.complete)
        self.assertGreater(result.pending_groups, 0)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].outcome, "split")


if __name__ == "__main__":
    unittest.main()
