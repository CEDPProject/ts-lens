"""주기 분석 계약.

막는 회귀:
  1) 평균만 빼면 우상향 계열의 스펙트럼이 최저 주파수에 몰려, 하루 주기가
     있는데도 "계열 길이의 절반"이 1위로 잡혔다. 선형 추세를 먼저 뺀다.
  2) 결측·평평·전부 NaN 같은 입력에서 예외 없이 결과를 돌려줘야 한다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.periodicity_service import analyze_periodicity_core

N = 96
HOURLY = 3600.0


def _top(res):
    return res["periods"][0]["description"] if res.get("periods") else None


class DetrendBeforeFFT(unittest.TestCase):
    def test_daily_cycle_found_under_a_trend(self):
        t = np.arange(N, dtype=np.float64)
        sig = 0.05 * t + np.sin(t * 2 * np.pi / 24)
        res = analyze_periodicity_core(sig, HOURLY)
        self.assertTrue(res["has_periodicity"])
        self.assertEqual(_top(res), "1d (24h)",
                         "추세를 빼지 않으면 최저 주파수가 1위로 잡힌다")

    def test_pure_daily_cycle_still_found(self):
        t = np.arange(N, dtype=np.float64)
        res = analyze_periodicity_core(np.sin(t * 2 * np.pi / 24), HOURLY)
        self.assertEqual(_top(res), "1d (24h)")

    def test_noisy_daily_cycle_still_found(self):
        t = np.arange(N, dtype=np.float64)
        rng = np.random.default_rng(0)
        sig = np.sin(t * 2 * np.pi / 24) + rng.normal(0, 0.3, N)
        self.assertEqual(_top(analyze_periodicity_core(sig, HOURLY)), "1d (24h)")


class DegenerateInputs(unittest.TestCase):
    def test_all_nan(self):
        res = analyze_periodicity_core(np.full(N, np.nan), HOURLY)
        self.assertFalse(res["has_periodicity"])

    def test_flat(self):
        res = analyze_periodicity_core(np.zeros(N), HOURLY)
        self.assertFalse(res["has_periodicity"])

    def test_too_short(self):
        res = analyze_periodicity_core(np.arange(10, dtype=np.float64), HOURLY)
        self.assertFalse(res["has_periodicity"])

    def test_partial_nan_is_interpolated(self):
        t = np.arange(N, dtype=np.float64)
        sig = np.sin(t * 2 * np.pi / 24)
        sig[::7] = np.nan
        res = analyze_periodicity_core(sig, HOURLY)
        self.assertTrue(res["has_periodicity"])
        self.assertEqual(_top(res), "1d (24h)")

    def test_reported_periods_repeat_at_least_twice(self):
        t = np.arange(N, dtype=np.float64)
        res = analyze_periodicity_core(np.sin(t * 2 * np.pi / 24), HOURLY)
        for p in res["periods"]:
            self.assertLessEqual(p["period_points"], N / 2 + 1e-6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
