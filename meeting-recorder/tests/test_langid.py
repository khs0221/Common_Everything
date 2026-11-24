"""발언 단위 언어 판별(langid) 논리 시험: 가짜 확률 함수로 모델 없이."""
import numpy as np

from mrec import langid
from mrec.langid import LangPiece

SR = 16000


def make_probe(regions, seg_start):
    state = {"pos": seg_start}

    def probe(audio):
        # split_segment 는 창 순서대로 부르므로, 창 시작을 따라가며 확률을 돌려줌
        mid = state["pos"] + len(audio) / SR / 2
        state["pos"] += langid.HOP_S
        for a, b, pe, pk in regions:
            if a <= mid < b:
                return pe, pk
        return 0.1, 0.1
    return probe


def test_windows_cover_segment():
    assert langid._windows(0.0, 3.5) == [(0.0, 3.5)]
    w = langid._windows(10.0, 17.0)
    assert w[0][0] == 10.0 and w[-1][1] == 17.0 and all(b - a <= langid.WIN_S + 1e-9 for a, b in w)


def test_switch_inside_one_segment_is_split_at_quiet_point():
    # 0~6초 한국어, 6.3~12초 영어, 6.0~6.3 은 조용함(쉬지 않고 바꿔 말함)
    audio = np.full(12 * SR, 0.3, np.float32)
    audio[int(6.0 * SR):int(6.3 * SR)] = 0.0
    probe = make_probe([(0, 6.15, 0.02, 0.95), (6.15, 99, 0.97, 0.01)], 0.0)
    pieces = langid.split_segment(audio, 0.0, 12.0, probe)
    assert [p.lang for p in pieces] == ["ko", "en"]
    assert 5.9 <= pieces[0].end <= 6.4 and pieces[1].start == pieces[0].end


def test_unsure_segment_returns_single_unsure_piece():
    audio = np.full(int(1.2 * SR), 0.3, np.float32)
    pieces = langid.split_segment(audio, 5.0, 6.2, lambda x: (0.3, 0.05))
    assert len(pieces) == 1 and pieces[0].lang is None and not pieces[0].sure


def test_fill_unsure_uses_nearest_sure_and_never_changes_sure():
    pieces = [LangPiece(0, 3, "ko", sure=True), LangPiece(3.1, 3.6, None),           # 짧은 "네" → 가까운 한국어
              LangPiece(4.5, 8, "en", sure=True), LangPiece(8.1, 8.6, None),          # 짧은 'Okay' → 앞 영어
              LangPiece(20, 23, "ko", sure=True), LangPiece(23.2, 23.6, "en", sure=True)]  # 확실한 짧은 전환은 그대로
    out = langid.fill_unsure(pieces)
    assert [p.lang for p in out] == ["ko", "ko", "en", "en", "ko", "en"]
    assert [p.sure for p in out] == [True, False, True, False, True, True]


def test_all_unsure_track_uses_probability_sum():
    pieces = [LangPiece(0, 1, None, 0.3, 0.1), LangPiece(2, 3, None, 0.2, 0.25)]
    assert {p.lang for p in langid.fill_unsure(pieces)} == {"en"}


def test_group_by_language_collects_far_apart_pieces():
    pieces = [LangPiece(0, 3, "ko", sure=True), LangPiece(4, 6, "en", sure=True), LangPiece(7, 9, "ko", sure=True)]
    assert langid.group_by_language(pieces) == [("en", [(4, 6)]), ("ko", [(0, 3), (7, 9)])]
