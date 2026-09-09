import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from decision_engine import CORE_WEIGHTS, add_asset_trend_columns, confirmed_zone_entries


class DecisionEngineTests(unittest.TestCase):
    def test_core_weights_are_fixed_and_sum_to_one(self):
        self.assertAlmostEqual(sum(CORE_WEIGHTS.values()), 1.0)
        self.assertNotIn("credit_score", CORE_WEIGHTS)
        self.assertNotIn("put_call_score", CORE_WEIGHTS)
        self.assertNotIn("aaii_score", CORE_WEIGHTS)

    def test_five_day_confirmation_uses_fifth_day(self):
        zones = pd.Series([1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2])
        self.assertEqual(confirmed_zone_entries(zones), [4, 9])

    def test_short_zone_run_is_not_accepted(self):
        zones = pd.Series([1, 1, 1, 1, 2, 2, 2, 2, 2])
        self.assertEqual(confirmed_zone_entries(zones), [8])

    def test_trend_score_is_bounded(self):
        index = pd.date_range("2020-01-01", periods=320, freq="B")
        frame = pd.DataFrame(
            {
                "qqq": np.linspace(100, 200, len(index)),
                "spy": np.linspace(100, 150, len(index)),
            },
            index=index,
        )
        add_asset_trend_columns(frame, "qqq")
        score = float(frame["qqq_trend_score"].dropna().iloc[-1])
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)
        self.assertGreaterEqual(score, 90.0)


if __name__ == "__main__":
    unittest.main()
