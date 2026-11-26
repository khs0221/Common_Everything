"""소리가 거의 없는 30초 창의 글 거르기(R6 수정 2회차, 감사 I2·G1·G2). 모델 없이 가짜 구간으로."""
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

from mrec import transcribe
from mrec.transcribe import HallucinationGuard

SR = 16000


def _seg(start, end, text="말", probs=(0.9,), seek=0, nsp=0.0, lp=-0.2):
    words = [NS(start=start, end=end, word=w, probability=p) for w, p in zip(text.split() or ["x"], probs)]
    return NS(start=start, end=end, text=text, words=words, no_speech_prob=nsp, avg_logprob=lp, seek=seek)


def _speech(seconds=30.19, level=0.1):
    rng = np.random.default_rng(1)
    return (level * rng.normal(size=int(seconds * SR))).astype(np.float32)


def test_text_from_window_with_only_tail_sound_is_rejected():
    # 실측 두 건: 30.19초 묶음의 둘째 창(seek 3000)의 "감사합니다." 30.00~30.04,
    # 16.41초 묶음의 둘째 창(seek 1600)의 "감사합니다." 16.00~16.06
    assert HallucinationGuard(_speech()).reject(_seg(30.00, 30.04, "감사합니다.", (0.39,), seek=3000))
    assert HallucinationGuard(_speech(16.41)).reject(_seg(16.00, 16.06, "감사합니다.", (0.5,), seek=1600))
    # 첫 창의 글은 길이와 무관하게 버리지 않음
    assert not HallucinationGuard(_speech()).reject(_seg(29.95, 29.99, "감사합니다.", (0.2,), seek=0))
    # 둘째 창이라도 말할 수 있는 속도면 버리지 않음(남은 소리 속 짧은 대답)
    g = HallucinationGuard(_speech(16.8))
    assert not g.reject(_seg(16.05, 16.45, "네.", (0.3,), seek=1600))
    assert not g.reject(_seg(16.05, 16.6, "Yes, please.", (0.3, 0.4), seek=1600))
    # 남은 소리가 1초 이상인 창도 검사하지 않음
    assert not HallucinationGuard(_speech(31.5)).reject(_seg(30.0, 30.04, "감사합니다.", (0.3,), seek=3000))


def test_short_pack_and_quiet_or_unsure_text_are_kept():
    """수정 1회차의 소리 크기·낱말 확률 규칙은 실제 발언을 버려(감사 G1) 없앴습니다."""
    g = HallucinationGuard(_speech(0.22))  # 0.22초짜리 묶음(짧은 대답 하나)
    assert not g.reject(_seg(0.0, 0.2, "네.", (0.2,)))
    x = _speech(20.0)
    x[int(10 * SR):int(12 * SR)] *= 0.03  # 약 -30dB 작은 목소리
    g = HallucinationGuard(x)
    assert not g.reject(_seg(10.2, 11.8, "Yes, please.", (0.1, 0.5)))
    assert not g.reject(_seg(1.0, 2.0, "모든 사람들에게", (0.05, 0.9)))  # 감사자 실측: 첫 낱말 확률 0.05


def _tr():
    tr = transcribe.Transcriber.__new__(transcribe.Transcriber)
    tr.hotwords = None
    tr.hint_langs = ()
    return tr


def test_transcribe_window_applies_guard_and_records_time_only(monkeypatch):
    tr = _tr()
    segs = [_seg(1.0, 5.0, "정상 발언 입니다", (0.9, 0.9, 0.9)), _seg(30.0, 30.04, "감사합니다.", (0.39,), seek=3000)]
    monkeypatch.setattr(tr, "_run", lambda audio, lang, words, hints=True: (segs, NS(language="ko")), raising=False)
    out = tr.transcribe_window(_speech(), 100.0, "ko", True)
    assert [s.text for s in out] == ["정상 발언 입니다"] and out[0].start == 101.0
    assert [t for t, _ in tr._dropped_now] == [130.0]
    tr.guard = False  # 설정으로 끔
    assert len(tr.transcribe_window(_speech(), 0.0, "ko", True)) == 2
    tr.guard = True
    monkeypatch.setattr(transcribe, "HALLUCINATION_GUARD", False)
    assert len(tr.transcribe_window(_speech(), 0.0, "ko", True)) == 2


def test_done_summary_shows_dropped_count():
    from mrec.pipeline import JobResult, done_summary

    r = JobResult({}, 8, ["참석자1"], {}, 60.0)
    assert done_summary(r) == "끝났습니다. 발언 8개, 상대방 1명으로 구분했습니다."
    r.dropped_text = 2
    assert done_summary(r).endswith("말소리 없는 구간의 글 2건 제외.")


def test_recheck_unsure_is_off_by_default():
    """R6 되돌림: 불확실 조각 다시 보기는 기본 끔(설정 "recheck_unsure": true 로만 켬)."""
    from mrec.pipeline import Job

    assert transcribe.RECHECK_UNSURE is False
    assert Job(Path("."), None, None).recheck_unsure is False
    assert getattr(_tr(), "recheck", transcribe.RECHECK_UNSURE) is False


def test_no_speech_threshold_is_045(monkeypatch):
    assert transcribe.NO_SPEECH_THRESHOLD == 0.45
    tr = _tr()
    segs = [_seg(0.0, 1.0, "정상", (0.9,), nsp=0.1, lp=-0.2),
            _seg(1.0, 2.0, "잡음환각", (0.5,), nsp=0.5, lp=-1.2)]
    monkeypatch.setattr(tr, "_run", lambda audio, lang, words, hints=True: (segs, NS(language="ko")), raising=False)
    out = tr.transcribe_window(_speech(5.0), 0.0, "ko", True)
    assert [s.text for s in out] == ["정상"]


def test_repeated_and_low_volume_hallucination_phrases_rejected():
    # 1) 한 구간 내 반복: 정상 음량이어도 차단
    g = HallucinationGuard(_speech(10.0, level=0.1))
    assert g.reject(_seg(0.0, 2.0, "Thank you. Thank you.", (0.9, 0.9, 0.9, 0.9)))
    assert g.reject(_seg(2.0, 4.0, "감사합니다. 감사합니다.", (0.9, 0.9)))

    # 2) 연속 구간 반복: 첫 번째 정상 음량은 통과, 두 번째 연속 반복은 차단
    g2 = HallucinationGuard(_speech(10.0, level=0.1))
    assert not g2.reject(_seg(0.0, 1.0, "Thank you.", (0.9, 0.9)))
    assert g2.reject(_seg(1.0, 2.0, "Thank you.", (0.9, 0.9)))
    # 정상 발언 뒤 다시 한 번은 허용
    assert not g2.reject(_seg(2.0, 3.0, "Let's continue.", (0.9, 0.9)))
    assert not g2.reject(_seg(3.0, 4.0, "감사합니다.", (0.9,)))
    assert g2.reject(_seg(4.0, 5.0, "감사합니다.", (0.9,)))

    # 3) 저음량(묵음/배경소음 -50 dB)에서는 단일 'Thank you.' / '감사합니다.' 도 차단
    quiet = _speech(10.0, level=0.003)  # 약 -50 dBFS
    g3 = HallucinationGuard(quiet)
    assert g3.reject(_seg(0.0, 1.0, "Thank you.", (0.9, 0.9)))
    assert g3.reject(_seg(2.0, 3.0, "감사합니다.", (0.9,)))

    # 4) 작은 목소리(-38 dB)로 실제 말한 단일 'Thank you.' 는 통과
    soft = _speech(5.0, level=0.0125)  # 약 -38 dBFS
    assert not HallucinationGuard(soft).reject(_seg(0.0, 1.0, "Thank you.", (0.9, 0.9)))


def test_recheck_unsure_enforces_count_and_time_caps(tmp_path):
    import soundfile as sf

    wav = tmp_path / "t.wav"
    sf.write(wav, np.zeros(SR * 30, dtype=np.float32), SR)
    calls = {"n": 0}

    class FakeModel:
        def detect_language(self, audio):
            calls["n"] += 1
            return "en", 0.9, [("en", 0.9), ("ko", 0.1)]

    tr = _tr()
    tr.model = FakeModel()
    unsure = [(i * 0.5, i * 0.5 + 0.4, "en") for i in range(40)]

    tr.recheck_unsure(wav, unsure, [], True, max_count=5, max_seconds=60.0)
    assert calls["n"] == 5

    calls["n"] = 0
    tr.recheck_unsure(wav, unsure, [], True, max_count=30, max_seconds=0.0)
    assert calls["n"] == 0


def test_vad_min_db_and_background_noise_gain_guard(tmp_path):
    import soundfile as sf

    from mrec import vad

    rng = np.random.default_rng(42)
    # 1) 일정한 배경 소음(-50 dB 및 -35 dB): 마이크 보호 시 증폭 금지(배율 1.0), -50 dB는 min_db=-40 컷으로 제외
    steady = (0.003 * rng.normal(size=SR * 3)).astype(np.float32)
    w_steady = tmp_path / "steady.wav"
    sf.write(w_steady, steady, SR)
    assert vad.track_gain(w_steady, min_db=-40.0) == 1.0
    assert vad.filter_quiet_segments(w_steady, [(0.2, 2.0)], -40.0) == []
    assert vad.filter_quiet_segments(w_steady, [(0.2, 2.0)], None) == [(0.2, 2.0)]

    fan = (0.018 * rng.normal(size=SR * 3)).astype(np.float32)  # 약 -35 dB 일정한 팬 소음
    w_fan = tmp_path / "fan.wav"
    sf.write(w_fan, fan, SR)
    assert vad.track_gain(w_fan, min_db=-40.0) == 1.0

    # 2) 조용한 바닥(-66 dB) + 작은 말소리(-30 dB): 증폭 허용, -40 dB 컷 통과
    mixed = (0.0005 * rng.normal(size=SR * 4)).astype(np.float32)
    mixed[SR:SR * 2] = (0.03 * rng.normal(size=SR)).astype(np.float32)
    w_mixed = tmp_path / "mixed.wav"
    sf.write(w_mixed, mixed, SR)
    assert vad.track_gain(w_mixed, min_db=-40.0) > 1.0
    assert vad.filter_quiet_segments(w_mixed, [(0.0, 0.8), (1.0, 2.0)], -40.0) == [(1.0, 2.0)]


def test_pipeline_skips_diarization_for_one_on_one(tmp_path, monkeypatch):
    """1:1 회의(num_speakers=1)는 화자 분리를 생략하고 상대방 발언을 spk1 로 처리합니다."""
    import soundfile as sf

    from mrec import diarize, pipeline, vad

    folder = tmp_path / "meet"
    folder.mkdir()
    wav = folder / "loop.wav"
    sf.write(wav, (0.05 * np.sin(2 * np.pi * 440 * np.arange(SR * 2) / SR)).astype(np.float32), SR)

    monkeypatch.setenv("MREC_GLOSSARY_DIR", str(tmp_path / "g"))
    (tmp_path / "g").mkdir()
    monkeypatch.setattr(vad, "speech_segments", lambda *a, **k: [(0.0, 1.5)])

    def fail_diarize(*a, **k):
        raise AssertionError("1:1 회의에서는 화자 분리를 호출하지 않아야 합니다")

    monkeypatch.setattr(diarize, "diarize_file", fail_diarize)

    class FakeTr:
        def __init__(self, *a, **k):
            self.dropped = []

        def transcribe_file(self, *a, **k):
            return [transcribe.Segment(0.0, 1.5, "Hello", "en", [transcribe.Word(0.0, 1.5, " Hello", 0.9)])]

    monkeypatch.setattr(transcribe, "Transcriber", FakeTr)
    res = pipeline.run(pipeline.Job(folder, None, wav, language="en", num_speakers=1, extra_terms=["ProjAlpha"]))
    assert res.speakers == ["spk1"]
    assert "ProjAlpha" in (folder / "Copilot용.txt").read_text(encoding="utf-8-sig")

