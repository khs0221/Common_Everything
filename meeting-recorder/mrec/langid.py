"""말소리 구간마다 영어/한국어를 판별합니다(R3).

왜 필요한가
- 예전(R2까지)에는 말소리를 최대 180초씩 이어 붙인 묶음마다, 앞 30초로 언어를 한 번 정해 묶음 전체에
  강제했습니다. 같은 사람이 한국어로 말하다 영어로 바꾸면 영어 부분이 한국어로 번역되어 적히거나
  빠졌습니다(R3 재현 기록 참고).

방식
1. whisper 인코더를 '30초로 늘리지 않은' 짧은 창(최대 3초, 1.5초 간격)에 돌려 창마다 언어 확률을 얻습니다.
   CTranslate2 는 30초보다 짧은 입력을 받으며, 2.7초 구간에서 0.62초 → 0.03초로 약 20배 빠르고
   판별 결과는 30초로 늘린 것과 같았습니다(측정: changes.md).
2. 영어·한국어 확률 중 큰 쪽이 CONF(0.6) 이상인 창만 '확실'로 봅니다. 측정에서 확실 판정은 1.5초
   이상 창에서 틀린 적이 없고, 틀린 판정은 모두 확률이 낮았습니다(다른 언어로 흩어짐).
3. 한 구간 안에서 확실한 창의 언어가 바뀌면(쉬지 않고 언어를 바꿔 말함) 바뀌는 곳 근처의 가장 조용한
   지점에서 구간을 나눕니다.
4. 확실하지 않은 것(짧은 "네", "Okay" 등)만 같은 트랙의 시간상 가장 가까운 확실한 구간의 언어를 따릅니다.
   **확실한 판정은 절대 바꾸지 않습니다**(같은 사람이 언어를 바꾼 것을 덮어쓰지 않기 위해).
5. 언어별로 구간을 모아(떨어진 구간도) 언어를 고정해 글로 옮깁니다(번역하지 않음, task=transcribe).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .audio_io import TARGET_SR

LANGS = ("en", "ko")
WIN_S = 3.0          # 판별 창 길이
HOP_S = 1.5          # 창 간격
SINGLE_MAX_S = 4.0   # 이보다 짧은 구간은 창 하나로 판별
CONF = 0.6           # 확실 판정 기준(영어·한국어 중 큰 확률)
MIN_PIECE_S = 0.6    # 나눈 조각의 최소 길이


@dataclass
class LangPiece:
    start: float
    end: float
    lang: str | None      # 확실하면 'en'/'ko', 아니면 None(이웃으로 채움)
    p_en: float = 0.0
    p_ko: float = 0.0
    sure: bool = False


class WhisperLangProbe:
    """faster-whisper 모델로 짧은 음성의 영어/한국어 확률을 구합니다(30초로 늘리지 않음)."""

    def __init__(self, whisper_model):
        import ctranslate2

        self._ct2 = ctranslate2
        self.m = whisper_model  # faster_whisper.WhisperModel

    def __call__(self, audio: np.ndarray) -> tuple[float, float]:
        feats = self.m.feature_extractor(audio)
        if feats.shape[-1] < 10:
            return 0.0, 0.0
        sv = self._ct2.StorageView.from_array(np.ascontiguousarray(feats[None]))
        enc = self.m.model.encode(sv)
        probs = dict(self.m.model.detect_language(enc)[0])
        return float(probs.get("<|en|>", 0.0)), float(probs.get("<|ko|>", 0.0))


def _windows(start: float, end: float) -> list[tuple[float, float]]:
    dur = end - start
    if dur <= SINGLE_MAX_S:
        return [(start, end)]
    out = []
    t = start
    while t + WIN_S < end:
        out.append((t, t + WIN_S))
        t += HOP_S
    out.append((max(start, end - WIN_S), end))
    return out


def _label(p_en: float, p_ko: float) -> tuple[str | None, bool]:
    best = "en" if p_en >= p_ko else "ko"
    return (best, True) if max(p_en, p_ko) >= CONF else (None, False)


def _quietest(audio: np.ndarray, seg_start: float, a: float, b: float) -> float:
    """[a, b] 안에서 20ms 에너지가 가장 낮은 지점(초)."""
    i0 = max(0, int((a - seg_start) * TARGET_SR))
    i1 = min(len(audio), int((b - seg_start) * TARGET_SR))
    frame = int(0.02 * TARGET_SR)
    if i1 - i0 < frame * 2:
        return (a + b) / 2
    x = audio[i0:i1][: (i1 - i0) // frame * frame].reshape(-1, frame)
    k = int(np.argmin((x.astype(np.float64) ** 2).mean(axis=1)))
    return seg_start + (i0 + k * frame + frame / 2) / TARGET_SR


def split_segment(audio: np.ndarray, seg_start: float, seg_end: float,
                  probe: Callable[[np.ndarray], tuple[float, float]]) -> list[LangPiece]:
    """한 말소리 구간을 언어가 같은 조각들로 나눕니다(확실한 언어 전환이 있을 때만 나눔)."""
    wins = _windows(seg_start, seg_end)
    labs = []
    for a, b in wins:
        x = audio[int((a - seg_start) * TARGET_SR):int((b - seg_start) * TARGET_SR)]
        pe, pk = probe(x)
        lab, sure = _label(pe, pk)
        labs.append((a, b, lab, pe, pk))
    sure_labs = [(a, b, lab) for a, b, lab, _, _ in labs if lab]
    if not sure_labs:
        pe = float(np.mean([x[3] for x in labs]))
        pk = float(np.mean([x[4] for x in labs]))
        return [LangPiece(seg_start, seg_end, None, pe, pk, False)]
    # 확실한 창들 사이에서 언어가 바뀌는 곳마다 자릅니다.
    cuts: list[tuple[float, str]] = []  # (자르는 시각, 그 뒤 언어)
    prev = sure_labs[0]
    for cur in sure_labs[1:]:
        if cur[2] != prev[2]:
            # 이전 창 가운데 ~ 지금 창 가운데 사이의 가장 조용한 지점
            a = (prev[0] + prev[1]) / 2
            b = (cur[0] + cur[1]) / 2
            cuts.append((_quietest(audio, seg_start, a, b), cur[2]))
        prev = cur
    pieces = []
    t, lang = seg_start, sure_labs[0][2]
    for c, nxt in cuts:
        if seg_end - c < MIN_PIECE_S:   # 끝 조각이 너무 짧음: 이 전환은 무시
            continue
        if c - t < MIN_PIECE_S:         # 앞 조각이 너무 짧음: 새 언어로 시작
            lang = nxt
            continue
        pieces.append(LangPiece(t, c, lang, sure=True))
        t, lang = c, nxt
    pieces.append(LangPiece(t, seg_end, lang, sure=True))
    # 조각마다 확률 평균(기록용)
    for p in pieces:
        inside = [x for x in labs if x[0] < p.end and x[1] > p.start] or labs
        p.p_en = float(np.mean([x[3] for x in inside]))
        p.p_ko = float(np.mean([x[4] for x in inside]))
    return pieces


def fill_unsure(pieces: list[LangPiece]) -> list[LangPiece]:
    """확실하지 않은 조각만, 시간상 가장 가까운 확실한 조각의 언어로 채웁니다(확실한 것은 그대로)."""
    sure = [p for p in pieces if p.sure]
    out = []
    for p in pieces:
        if p.sure:
            out.append(p)
            continue
        if sure:
            mid = (p.start + p.end) / 2
            near = min(sure, key=lambda q: 0.0 if q.start <= mid <= q.end
                       else min(abs(q.start - mid), abs(q.end - mid)))
            lang = near.lang
        else:  # 트랙 전체가 불확실: 확률 합으로
            lang = "en" if sum(q.p_en for q in pieces) >= sum(q.p_ko for q in pieces) else "ko"
        out.append(LangPiece(p.start, p.end, lang, p.p_en, p.p_ko, False))
    return out


def group_by_language(pieces: list[LangPiece]) -> list[tuple[str, list[tuple[float, float]]]]:
    """언어별로 트랙 전체의 조각을 모읍니다: [(언어, [(시작, 끝), ...]), ...] (시간순).

    떨어진 조각도 같은 언어끼리 한 묶음에 이어 붙입니다(묶음 안 시각은 TimeMap 이 조각마다 되돌림).
    언어가 자주 바뀌는 트랙에서도 whisper 30초 창 낭비가 늘지 않아, 처리 시간이 한 언어 트랙과 비슷합니다.
    """
    out = []
    for lang in LANGS:
        segs = sorted((p.start, p.end) for p in pieces if p.lang == lang)
        if segs:
            out.append((lang, segs))
    return out


def language_runs(pieces: list[LangPiece]) -> list[tuple[str, list[tuple[float, float]]]]:
    """같은 언어가 이어지는 조각끼리 묶습니다: [(언어, [(시작, 끝), ...]), ...]."""
    runs: list[tuple[str, list[tuple[float, float]]]] = []
    for p in sorted(pieces, key=lambda p: p.start):
        if runs and runs[-1][0] == p.lang:
            runs[-1][1].append((p.start, p.end))
        else:
            runs.append((p.lang, [(p.start, p.end)]))
    return runs
