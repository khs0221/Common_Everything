"""소리가 거의 없는 30초 창의 글 거르기(R6 수정 2회차, 감사 I2·G1·G2). 모델 없이 가짜 구간으로."""
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
