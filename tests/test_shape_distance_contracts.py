"""shape 거리 계약.

막는 회귀: 특징을 center_mass = |peak_pos-0.5| + |dip_pos-0.5| 하나로 합치면
위아래로 뒤집은 모양이 같은 값을 갖는다. 종 모양과 V 자가 구별되지 않아
쿼리를 다시 그려도 같은 결과가 나왔다.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.helpers import _extract_shape_features, _shape_feature_distance, compute_distance

N = 24


def _z(v):
    a = np.asarray(v, dtype=float)
    s = a.std() or 1.0
    return (a - a.mean()) / s


BELL = _z([math.sin(math.pi * i / (N - 1)) for i in range(N)])
VSHAPE = _z([-math.sin(math.pi * i / (N - 1)) for i in range(N)])
RISE = _z([i / (N - 1) for i in range(N)])
FALL = _z([1 - i / (N - 1) for i in range(N)])


class ShapeFeaturesDistinguishMirrors(unittest.TestCase):
    def test_bell_and_v_have_different_features(self):
        fb = _extract_shape_features(BELL)
        fv = _extract_shape_features(VSHAPE)
        self.assertFalse(np.allclose(fb, fv),
                         "종 모양과 V 자의 특징이 같으면 검색이 둘을 구별하지 못한다")

    def test_bell_and_v_are_far_apart(self):
        self.assertGreater(_shape_feature_distance(BELL, VSHAPE), 0.1)
        self.assertGreater(compute_distance(BELL, VSHAPE, "shape"), 0.1)

    def test_rise_and_fall_are_far_apart(self):
        self.assertGreater(compute_distance(RISE, FALL, "shape"), 0.1)

    def test_same_shape_is_zero(self):
        self.assertAlmostEqual(compute_distance(BELL, BELL.copy(), "shape"), 0.0, places=6)

    def test_bell_closer_to_itself_than_to_v(self):
        noisy = BELL + np.random.default_rng(0).normal(0, 0.05, N)
        self.assertLess(compute_distance(BELL, _z(noisy), "shape"),
                        compute_distance(BELL, VSHAPE, "shape"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DegeneratePresetGuard(unittest.TestCase):
    """변화 없는 도형은 저장 단계에서 막는다.

    막는 회귀: 값이 전부 0 인 사용자 프리셋이 저장돼 있었다. 평평한 구간마다
    내장 flat 과 거리 0 으로 동점이 되고, 그 밖에는 아무것도 맞히지 못해
    분류에서 0 건을 기록했다.
    """

    def test_flat_shape_ties_with_flat_preset(self):
        flat = np.zeros(N)
        self.assertAlmostEqual(compute_distance(flat, np.zeros(N), "euclidean"), 0.0)

    def test_flat_shape_is_far_from_every_nonflat_shape(self):
        flat = np.zeros(N)
        for other in (BELL, VSHAPE, RISE, FALL):
            self.assertGreater(compute_distance(flat, other, "euclidean"), 0.5,
                               "평평한 도형은 모양 있는 구간과 구별돼야 한다")
