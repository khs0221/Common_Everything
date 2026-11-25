"""말하지 않은 글 거르기(R6 수정 1회차, 감사 I2). 모델 없이 가짜 구간으로."""
from types import SimpleNamespace as NS

import numpy as np

from mrec import transcribe
from mrec.transcribe import HallucinationGuard

SR = 16000


def _seg(start, end, text="말", probs=(0.9, 0.9, 0.9, 0.9, 0.9), nsp=0.0, lp=-0.2):
    words = [NS(start=start, end=end, word=w, probability=p) for w, p in zip(text.split() or ["x"], probs)]
    return NS(start=start, end=end, text=text, words=words, no_speech_prob=nsp, avg_logprob=lp)


def _speech(seconds=30.19):
    rng = np.random.default_rng(1)
    return (0.1 * rng.normal(size=int(seconds * SR))).astype(np.float32)


def test_text_in_tail_padding_is_rejected():
    # R6 int8: 30.19초 묶음의 두 번째 창(소리 0.19초 + 무음 채움)에 "감사합니다." 30.00~30.04
    g = HallucinationGuard(_speech())
    assert g.reject(_seg(30.00, 30.04, "감사합니다.", (0.39,)))
    assert g.reject(_seg(30.5, 31.2, "감사합니다.", (0.9,)))  # 묶음 소리가 끝난 뒤
    assert not g.reject(_seg(25.0, 30.1, "정상 발언 입니다 끝까지 잘", (0.9,) * 5))


def test_text_over_silence_is_rejected():
    x = _speech(20.0)
    x[int(10 * SR):int(12 * SR)] *= 0.01  # 2초 무음
    g = HallucinationGuard(x)
    assert g.reject(_seg(10.2, 11.8, "Thank you.", (0.95, 0.99)))
    assert not g.reject(_seg(2.0, 4.0, "Thank you.", (0.95, 0.99)))


def test_low_confidence_short_text_is_rejected_but_confident_short_is_kept():
    g = HallucinationGuard(_speech(30.0))
    assert g.reject(_seg(28.58, 29.98, "다음 영상에서 만나요.", (0.05, 0.68, 0.99)))  # 잡음 창 실측
    assert g.reject(_seg(27.18, 29.98, "So, let's go.", (0.13, 0.56, 0.21)))
    assert not g.reject(_seg(1.0, 1.6, "Yes.", (0.84,)))  # 확신 있는 짧은 대답
    assert not g.reject(_seg(1.0, 3.0, "네 맞습니다", (0.3, 0.95)))  # 첫 단어만 낮고 평균은 높음
    # 5단어 이상은 확률로 거르지 않음(긴 발언의 첫 단어가 낮은 경우 보호)
    assert not g.reject(_seg(1.0, 4.0, "so we will ship it", (0.1, 0.3, 0.3, 0.3, 0.3)))


def test_transcribe_window_applies_guard(monkeypatch):
    tr = transcribe.Transcriber.__new__(transcribe.Transcriber)
    tr.hotwords = None
    tr.hint_langs = ()
    segs = [_seg(1.0, 5.0, "정상 발언 입니다", (0.9, 0.9, 0.9)), _seg(30.0, 30.04, "감사합니다.", (0.39,))]
    monkeypatch.setattr(tr, "_run", lambda audio, lang, words, hints=True: (segs, NS(language="ko")), raising=False)
    out = tr.transcribe_window(_speech(), 100.0, "ko", True)
    assert [s.text for s in out] == ["정상 발언 입니다"] and out[0].start == 101.0
    assert tr.dropped_hallucinations == 1
    monkeypatch.setattr(transcribe, "HALLUCINATION_GUARD", False)
    assert len(tr.transcribe_window(_speech(), 0.0, "ko", True)) == 2
