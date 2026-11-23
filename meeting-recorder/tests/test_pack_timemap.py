from mrec.transcribe import Segment, TimeMap, Word, pack_segments
from mrec.vad import pad_segments


def test_pad_segments_splits_overlap_in_middle():
    out = pad_segments([(1.0, 2.0), (2.2, 3.0), (10.0, 11.0)], 0.2, 10.5)
    assert out[0] == (0.8, 2.1)  # 2.0 과 2.2 의 가운데
    assert out[1][0] == 2.1 and abs(out[1][1] - 3.2) < 1e-9
    assert out[2] == (9.8, 10.5)  # 끝은 길이를 넘지 않음


def test_pack_segments_limits_speech_per_pack():
    segs = [(i * 10.0, i * 10.0 + 5.0) for i in range(10)]  # 5초 × 10
    packs = pack_segments(segs, pack_seconds=12.0)
    assert [len(p) for p in packs] == [2, 2, 2, 2, 2]
    assert sum(len(p) for p in packs) == 10
    assert pack_segments([], 10) == []


def test_timemap_restores_and_splits_words_from_different_chunks():
    # 원래 시각 10~12초(2초), 50~53초(3초)를 이어 붙임 → 이어 붙인 시각 0~2, 2~5
    tm = TimeMap([(10.0, 2.0), (50.0, 3.0)])
    assert tm.to_orig(0.5) == 10.5
    assert tm.to_orig(2.5) == 50.5
    seg = Segment(0.2, 4.0, "a b c", "en", [Word(0.2, 0.6, " a"), Word(1.2, 1.8, " b"), Word(2.3, 2.9, " c")])
    out = tm.restore(seg)
    assert len(out) == 2
    assert (out[0].start, out[0].end, out[0].text) == (10.2, 11.8, "a b")
    assert (round(out[1].start, 3), round(out[1].end, 3), out[1].text) == (50.3, 50.9, "c")


def test_timemap_word_straddling_boundary_uses_one_chunk():
    tm = TimeMap([(10.0, 2.0), (50.0, 3.0)])
    seg = Segment(1.9, 2.3, "x", "en", [Word(1.9, 2.3, " x")])  # 가운데 2.1 → 두 번째 구간
    out = tm.restore(seg)
    assert len(out) == 1 and out[0].start == 50.0 and abs(out[0].end - 50.3) < 1e-9


def test_timemap_without_words():
    tm = TimeMap([(10.0, 2.0), (50.0, 3.0)])
    out = tm.restore(Segment(0.5, 1.5, "hi", "en", []))
    assert len(out) == 1 and (out[0].start, out[0].end) == (10.5, 11.5)
