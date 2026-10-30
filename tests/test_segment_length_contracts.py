"""구간 길이·프리셋 기본값 계약.

여기서 막는 회귀:
  1) 자료가 주기 중간에서 시작하면 첫 구간이 잘린 조각인데, 그 길이를 대표로
     삼아 온전한 구간까지 눌러 버리던 문제 (하루 24 → 6 point).
  2) 숨김 목록이 없을 때 내장 도형을 전부 숨겨, 도형 목록이 비고 분류가
     100% Unclassified 로 떨어지던 문제.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.segment_search import representative_segment_length, list_presets
from backend.app_config import load_hidden_presets, cfg_preset_catalog


def _seg(n):
    return {"values": [0.0] * n}


class RepresentativeSegmentLength(unittest.TestCase):
    def test_partial_first_segment_is_ignored(self):
        # 18시에 시작한 시간 간격 자료: 첫날만 6 point, 나머지는 24
        segs = [_seg(6)] + [_seg(24) for _ in range(20)]
        self.assertEqual(representative_segment_length(segs), 24)

    def test_partial_last_segment_is_ignored(self):
        segs = [_seg(24) for _ in range(20)] + [_seg(3)]
        self.assertEqual(representative_segment_length(segs), 24)

    def test_both_ends_partial(self):
        segs = [_seg(6)] + [_seg(24) for _ in range(20)] + [_seg(11)]
        self.assertEqual(representative_segment_length(segs), 24)

    def test_tie_prefers_longer(self):
        segs = [_seg(12), _seg(24)]
        self.assertEqual(representative_segment_length(segs), 24)

    def test_empty(self):
        self.assertEqual(representative_segment_length([]), 0)
        self.assertEqual(representative_segment_length([{"values": []}]), 0)


class PresetDefaults(unittest.TestCase):
    def test_builtin_presets_visible_by_default(self):
        # 숨김 파일이 없는 이름을 쓴다
        hidden = load_hidden_presets("__no_such_dataset__")
        self.assertEqual(hidden, set(), "기본값은 '전부 보임'이어야 한다")

    def test_list_presets_returns_builtins_for_new_dataset(self):
        presets = list_presets("__no_such_dataset__")
        visible = [p for p in presets if not p.get("hidden")]
        self.assertEqual(len(visible), len(cfg_preset_catalog()))
        self.assertTrue(all(p.get("preview") for p in visible),
                        "미리보기 모양이 비어 있으면 화면에 그릴 것이 없다")


if __name__ == "__main__":
    unittest.main(verbosity=2)
