"""글로 옮기기(faster-whisper, CPU int8). 말소리 창 단위로 처리해 메모리가 일정합니다."""
from __future__ import annotations

import bisect
import os
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import models, sysinfo
from .audio_io import TARGET_SR, read_segment
from .paths import native_path

AUTO_LANGS = ("en", "ko")  # '자동' 은 영어/한국어 중에서 고릅니다.
PACK_SECONDS = 180.0  # 묶음 하나의 말소리 길이(초). 묶음 안은 한 언어로만 채웁니다(R3).
LID_SHARE = 0.1       # 진행률에서 언어 판별이 차지하는 비중


def _stub_optional_imports() -> None:
    """exe 에는 PyAV(av)·huggingface_hub 를 넣지 않습니다(용량·라이선스·외부 접속 코드 제외).

    faster-whisper 는 이 둘을 파일 맨 위에서 불러오지만, 우리는 음성을 배열로 넘기고
    모델도 폴더 경로로 주므로 실제로는 쓰지 않습니다. 없으면 빈 모듈로 대신합니다.
    """
    for name in ("av", "huggingface_hub"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0  # whisper 의 단어 확률(확신이 낮은 단어 표시용, R4)


@dataclass
class Segment:
    start: float
    end: float
    text: str
    lang: str
    words: list[Word] = field(default_factory=list)


HALLUCINATION_GUARD = True


class HallucinationGuard:
    """말하지 않은 글(환각) 거르기(R6 수정 1회차).

    turbo 의 무음 확률(no_speech_prob)은 잡음·무음에서도 0 으로 나와 기본 기준이 듣지 않습니다(측정).
    대신 두 가지를 봅니다.
    1. 소리 없는 곳의 글: 구간이 묶음 소리의 끝을 넘거나(30초 창 꼬리의 무음 채움), 구간 소리 크기가
       묶음 말소리 크기(32ms 창 RMS 의 75번째 백분위)의 10% 미만이면 버립니다.
    2. 확신 낮은 짧은 글: 4단어 이하이고 첫 단어 확률 < 0.35, 평균 단어 확률 < 0.6 이면 버립니다
       ("Thank you.", "감사합니다.", "다음 영상에서 만나요." 같은 전형적인 환각이 여기에 듭니다).
    """

    FRAME = 512

    def __init__(self, audio):
        self.audio = np.asarray(audio, dtype=np.float32)
        self.dur = len(self.audio) / TARGET_SR
        n = len(self.audio) // self.FRAME * self.FRAME
        if n:
            fr = self.audio[:n].reshape(-1, self.FRAME).astype(np.float64)
            self.ref = float(np.percentile(np.sqrt((fr ** 2).mean(axis=1)), 75))
        else:
            self.ref = 0.0

    def reject(self, seg) -> bool:
        a, b = float(seg.start), min(float(seg.end), self.dur)
        if b - a < 0.1:  # 소리가 끝난 뒤(또는 거의 없는 곳)의 글
            return True
        x = self.audio[int(a * TARGET_SR):int(b * TARGET_SR)]
        if self.ref > 0 and x.size and float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) < 0.1 * self.ref:
            return True
        probs = [float(w.probability) for w in (seg.words or [])]
        if probs and len(probs) <= 4 and probs[0] < 0.35 and sum(probs) / len(probs) < 0.6:
            return True
        return False


class Transcriber:
    def __init__(self, model_dir: Path | None = None, threads: int | None = None, beam_size: int = 1,
                 model: str = "turbo", hotwords: str | None = None, hint_langs: tuple[str, ...] = ("en",),
                 hint_vocab_text: str | None = None):
        """model: 'turbo'(글로 옮기기) 또는 'small'(발언 단위 언어 판별 전용). hotwords: 인식 힌트 문자열(hint_langs 언어 묶음에만)."""
        _stub_optional_imports()
        from faster_whisper import WhisperModel

        self.model_name = model
        self.prev_text = False  # condition_on_previous_text(측정 뒤 결정, R4 changes.md)
        self.hotwords = hotwords or None
        self.hint_vocab_text = hint_vocab_text  # 끼어듦 검사용 용어(문맥 문장 제외). 없으면 hotwords 전체
        self.hint_langs = tuple(hint_langs)
        model_dir = model_dir or models.path(models.WHISPER_DIRS[model])
        threads = threads or sysinfo.compute_threads()
        self.beam_size = beam_size
        self.model = WhisperModel(native_path(model_dir), device="cpu", compute_type="int8",
                                  cpu_threads=threads, num_workers=1, local_files_only=True)

    def _hints_active(self, language: str | None) -> bool:
        return bool(self.hotwords) and language in self.hint_langs

    def hint_echo(self, text: str) -> bool:
        """힌트 목록을 그대로 읊은 듯한 출력인지(무음·잡음에서 생기는 부작용, R4 측정).

        단어가 3개 이상이고 절반 이상이 힌트 용어의 단어이면서 쉼표로 나열하거나(2개 이상) 반복되면
        (서로 다른 단어 비율 0.5 이하), 또는 같은 단어가 세 번 이상 연속되거나 6단어 이상에서 서로 다른
        단어 비율이 0.3 이하이면, 또는 단어의 70% 이상이 힌트 용어의 단어이면(쉼표 없는 나열, R6) 그렇다고 봅니다. 그렇다고 보면 그 묶음만 힌트 없이 다시 옮기므로(R3 와 같은
        결과), 잘못 걸려도 결과가 나빠지지 않고 시간만 더 듭니다.
        """
        ws = re.findall(r"[a-z0-9][a-z0-9&.\-]*", text.lower())
        ws = [w.strip(".-") for w in ws if w.strip(".-")]
        if len(ws) < 3:
            return False
        uniq = len(set(ws)) / len(ws)
        vocab = self._hint_vocab()
        # R6(감사 R4 N1): 쉼표 없이 용어만 늘어놓은 출력도 잡음(단어 70% 이상이 힌트 용어의 단어)
        if sum(w in vocab for w in ws) / len(ws) >= 0.7:
            return True
        if len(ws) >= 6 and uniq <= 0.3:
            return True
        run = best = 1
        for a, b in zip(ws, ws[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        if best >= 3:  # 같은 단어 세 번 연속
            return True
        inv = sum(w in vocab for w in ws) / len(ws)
        return inv >= 0.5 and (text.count(",") >= 2 or uniq <= 0.5)

    def short_hint_only(self, seg) -> bool:
        """1~2단어 끼어듦(R6, 감사 R4 N1): 구간 글 전체가 힌트 용어 한두 개뿐이고, whisper 가 말소리일
        가능성을 낮게 본 구간(no_speech_prob > 0.3 또는 avg_logprob < -0.8). 진짜로 용어만 짧게 말한 것은
        보통 이 확률이 좋아 걸리지 않고, 걸려도 힌트 없이 다시 옮길 뿐입니다."""
        ws = [w.strip(".-,?!") for w in re.findall(r"[a-z0-9][a-z0-9&.\-]*", seg.text.lower())]
        ws = [w for w in ws if w]
        if not ws or len(ws) > 3:
            return False
        vocab = self._hint_vocab()
        if not all(w in vocab for w in ws):
            return False
        return getattr(seg, "no_speech_prob", 0.0) > 0.3 or getattr(seg, "avg_logprob", 0.0) < -0.8

    def _hint_vocab(self) -> set:
        src = getattr(self, "hint_vocab_text", None) or self.hotwords or ""
        if getattr(self, "_vocab_src", None) != src:
            self._vocab_src = src
            self._vocab = {w.strip(".-") for w in re.findall(r"[a-z0-9][a-z0-9&.\-]*", src.lower())}
        return self._vocab

    def _run(self, audio, language: str | None, words: bool, hints: bool = True):
        # hotwords: faster-whisper 1.2.1 은 30초 창마다 프롬프트 앞에 붙입니다(get_prompt).
        # initial_prompt 는 condition_on_previous_text=False 이면 묶음의 첫 창에만 쓰여 쓰지 않습니다(R4).
        hot = self.hotwords if (hints and self._hints_active(language)) else None
        segs, info = self.model.transcribe(
            audio, language=language, beam_size=self.beam_size, vad_filter=False,
            condition_on_previous_text=self.prev_text, word_timestamps=words, hotwords=hot,
            without_timestamps=False, temperature=(0.0, 0.2, 0.4, 0.6),
            log_prob_threshold=-1.0, no_speech_threshold=0.6, compression_ratio_threshold=2.4)
        return list(segs), info

    def transcribe_window(self, audio, offset: float, language: str | None, words: bool) -> list[Segment]:
        """language: None=자동(영어/한국어), 'en', 'ko'."""
        segs, info = self._run(audio, language, words)
        lang = language or info.language
        if language is None and info.language not in AUTO_LANGS:
            probs = dict(info.all_language_probs or [])
            lang = max(AUTO_LANGS, key=lambda k: probs.get(k, 0.0))
            segs, info = self._run(audio, lang, words)
        if self._hints_active(lang) and any(self.hint_echo(s.text) or self.short_hint_only(s) for s in segs):
            # 힌트 때문에 말하지 않은 용어가 끼어든 것으로 보이면 이 묶음은 힌트 없이 다시 옮깁니다.
            self.hint_retries = getattr(self, "hint_retries", 0) + 1
            segs, info = self._run(audio, lang, words, hints=False)
        out: list[Segment] = []
        guard = HallucinationGuard(audio) if HALLUCINATION_GUARD else None
        for s in segs:
            text = s.text.strip()
            if not text:
                continue
            # 말이 없는데 지어낸 듯한 구간 거르기(whisper 기본 기준과 같음)
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                continue
            if guard is not None and guard.reject(s):
                self.dropped_hallucinations = getattr(self, "dropped_hallucinations", 0) + 1
                continue
            ws = [Word(offset + w.start, offset + w.end, w.word, float(getattr(w, 'probability', 1.0)))
                  for w in (s.words or [])]
            out.append(Segment(offset + s.start, offset + s.end, text, lang, ws))
        return out

    def transcribe_file(self, wav16k: Path, speech: list[tuple[float, float]], language: str | None,
                        words: bool, progress: Callable[[float], None] | None = None,
                        cancel: Callable[[], bool] | None = None,
                        pack_seconds: float = PACK_SECONDS,
                        runs: list | None = None) -> list[Segment]:
        """말소리 구간만 이어 붙여(무음 제거) 묶음 단위로 글로 옮긴 뒤, 원래 시각으로 되돌립니다.

        whisper 는 30초 창 단위로 계산하므로, 무음이 많은 트랙(내가 듣고만 있는 마이크 등)을
        구간마다 따로 넣으면 짧은 구간도 30초만큼 계산합니다. 이어 붙이면 그 낭비가 없습니다.

        language=None(자동)이면 먼저 말소리 구간마다 영어/한국어를 판별하고(langid), 같은 언어가
        같은 언어 구간끼리만 묶어 그 언어로 고정해 옮깁니다. 묶음에 두 언어가 섞이지 않으므로
        한쪽 언어가 다른 언어로 번역되어 적히지 않습니다(R3).
        """
        out: list[Segment] = []
        total = sum(b - a for a, b in speech) or 1.0
        if runs is not None:  # 언어 판별을 다른(가벼운) 모델로 미리 한 경우(R4)
            base = 0.0
        elif language is None:
            self.last_lang_stats = {}
            runs = self.detect_language_runs(wav16k, speech, cancel=cancel,
                                             progress=(lambda f: progress(f * LID_SHARE)) if progress else None)
            base = LID_SHARE
        else:
            self.last_lang_stats = {}
            runs = [(language, list(speech))]
            base = 0.0
        done = 0.0
        for lang, segs in runs:
            for pack in pack_segments(segs, pack_seconds):
                if cancel and cancel():
                    raise InterruptedError
                pieces = [read_segment(wav16k, a, b) for a, b in pack]
                tmap = TimeMap([(a, len(p) / TARGET_SR) for (a, _), p in zip(pack, pieces)])
                audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
                del pieces
                if len(audio) >= TARGET_SR * 0.2:
                    for s in self.transcribe_window(audio, 0.0, lang, words):
                        out.extend(tmap.restore(s))
                done += sum(b - a for a, b in pack)
                if progress:
                    progress(base + (1 - base) * done / total)
        out.sort(key=lambda s: s.start)
        return out

    def detect_language_runs(self, wav16k: Path, speech: list[tuple[float, float]],
                             cancel: Callable[[], bool] | None = None,
                             progress: Callable[[float], None] | None = None):
        """말소리 구간마다 언어를 판별해 언어별로 모은 [(언어, [(시작, 끝), ...]), ...] 를 돌려줍니다."""
        from . import langid

        probe = langid.WhisperLangProbe(self.model)
        pieces: list = []
        total = sum(b - a for a, b in speech) or 1.0
        done = 0.0
        for a, b in speech:
            if cancel and cancel():
                raise InterruptedError
            audio = read_segment(wav16k, a, b)
            pieces.extend(langid.split_segment(audio, a, b, probe, langid.CONF_BY_MODEL.get(self.model_name)))
            done += b - a
            if progress:
                progress(done / total)
        filled = langid.fill_unsure(pieces)
        runs = langid.group_by_language(filled)
        self.last_lang_stats = {
            "pieces": len(filled), "unsure": sum(1 for p in pieces if not p.sure),
            "split_segments": len(pieces) - len(speech),
            "seconds": {lang: round(sum(p.end - p.start for p in filled if p.lang == lang), 1)
                        for lang in langid.LANGS},
        }
        return runs


def pack_segments(speech: list[tuple[float, float]], pack_seconds: float = PACK_SECONDS) -> list[list[tuple[float, float]]]:
    """말소리 구간들을 말소리 합계 pack_seconds 이하의 묶음으로 나눕니다(구간은 자르지 않음)."""
    packs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    cur_len = 0.0
    for a, b in sorted(speech):
        if b <= a:
            continue
        if cur and cur_len + (b - a) > pack_seconds:
            packs.append(cur)
            cur, cur_len = [], 0.0
        cur.append((a, b))
        cur_len += b - a
    if cur:
        packs.append(cur)
    return packs


class TimeMap:
    """이어 붙인 음성의 시각 ↔ 원래 녹음의 시각."""

    def __init__(self, chunks: list[tuple[float, float]]):
        # chunks: [(원래 시작초, 길이초)]
        self.orig = [a for a, _ in chunks]
        self.lens = [n for _, n in chunks]
        self.packed_starts = []
        acc = 0.0
        for n in self.lens:
            self.packed_starts.append(acc)
            acc += n
        self.total = acc

    def index(self, t: float) -> int:
        i = bisect.bisect_right(self.packed_starts, t) - 1
        return min(max(i, 0), len(self.orig) - 1)

    def to_orig(self, t: float, i: int | None = None) -> float:
        if not self.orig:
            return t
        i = self.index(t) if i is None else i
        local = min(max(t - self.packed_starts[i], 0.0), self.lens[i])
        return self.orig[i] + local

    def restore(self, s: Segment) -> list[Segment]:
        """원래 시각으로 되돌립니다. 한 구간의 단어가 서로 다른 말소리 구간에서 왔으면 나눕니다
        (이어 붙인 음성에서는 몇 초~몇 분 떨어진 말이 한 문장처럼 이어질 수 있기 때문)."""
        if s.words:
            groups: list[tuple[int, list[Word]]] = []
            for w in s.words:
                i = self.index((w.start + w.end) / 2)  # 단어의 시작·끝을 같은 구간 기준으로
                if groups and groups[-1][0] == i:
                    groups[-1][1].append(w)
                else:
                    groups.append((i, [w]))
            # 구간 경계 근처 단어의 시각은 흔들리므로, 문장 부호를 보고 경계를 바로잡습니다.
            for k in range(len(groups) - 1):
                a, b = rebalance(groups[k][1], groups[k + 1][1])
                groups[k] = (groups[k][0], a)
                groups[k + 1] = (groups[k + 1][0], b)
            out = []
            for i, ws in groups:
                if not ws:
                    continue
                ows = [Word(self.to_orig(w.start, i), self.to_orig(w.end, i), w.text, w.prob) for w in ws]
                text = "".join(w.text for w in ows).strip()
                if text:
                    out.append(Segment(ows[0].start, ows[-1].end, text, s.lang, ows))
            return out
        i = self.index(s.start)
        j = self.index(max(s.start, s.end - 1e-3))
        return [Segment(self.to_orig(s.start, i), self.to_orig(s.end, j), s.text, s.lang, [])]


SENTENCE_END = (".", "?", "!", "。", "？", "！")
MAX_MOVE_WORDS = 2


def ends_sentence(text: str) -> bool:
    return text.strip().rstrip("\"')]»”’").endswith(SENTENCE_END)


def rebalance(a: list, b: list) -> tuple[list, list]:
    """이웃한 두 단어 묶음(a 다음 b)의 경계를 문장 부호에 맞춥니다.

    예) a = "…in March. I", b = "can prepare…" → a = "…in March.", b = "I can prepare…"
        a = "…the customer", b = "workshop. Next…" → a = "…the customer workshop.", b = "Next…"
    옮기는 단어는 최대 MAX_MOVE_WORDS 개이고, 어느 쪽도 비지 않게 합니다.
    """
    if not a or not b or ends_sentence(a[-1].text):
        return a, b
    for k in range(1, MAX_MOVE_WORDS + 1):
        if len(a) > k and ends_sentence(a[-k - 1].text):
            return a[:-k], a[-k:] + b
    for k in range(1, MAX_MOVE_WORDS + 1):
        if len(b) > k and ends_sentence(b[k - 1].text):
            return a + b[:k], b[k:]
    return a, b
