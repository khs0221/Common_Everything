"""글로 옮기기(faster-whisper, CPU int8). 말소리 창 단위로 처리해 메모리가 일정합니다."""
from __future__ import annotations

import bisect
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import models
from .audio_io import TARGET_SR, read_segment
from .paths import native_path

AUTO_LANGS = ("en", "ko")  # '자동' 은 영어/한국어 중에서 고릅니다.
PACK_SECONDS = 180.0  # 묶음 하나의 말소리 길이(초). 언어 자동 판별도 묶음마다 합니다.


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


@dataclass
class Segment:
    start: float
    end: float
    text: str
    lang: str
    words: list[Word] = field(default_factory=list)


class Transcriber:
    def __init__(self, model_dir: Path | None = None, threads: int | None = None, beam_size: int = 1):
        _stub_optional_imports()
        from faster_whisper import WhisperModel

        model_dir = model_dir or models.path(models.WHISPER_DIR)
        threads = threads or max(1, min(8, (os.cpu_count() or 4)))
        self.beam_size = beam_size
        self.model = WhisperModel(native_path(model_dir), device="cpu", compute_type="int8",
                                  cpu_threads=threads, num_workers=1, local_files_only=True)

    def _run(self, audio, language: str | None, words: bool):
        segs, info = self.model.transcribe(
            audio, language=language, beam_size=self.beam_size, vad_filter=False,
            condition_on_previous_text=False, word_timestamps=words,
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
        out: list[Segment] = []
        for s in segs:
            text = s.text.strip()
            if not text:
                continue
            # 말이 없는데 지어낸 듯한 구간 거르기(whisper 기본 기준과 같음)
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                continue
            ws = [Word(offset + w.start, offset + w.end, w.word) for w in (s.words or [])]
            out.append(Segment(offset + s.start, offset + s.end, text, lang, ws))
        return out

    def transcribe_file(self, wav16k: Path, speech: list[tuple[float, float]], language: str | None,
                        words: bool, progress: Callable[[float], None] | None = None,
                        cancel: Callable[[], bool] | None = None,
                        pack_seconds: float = PACK_SECONDS) -> list[Segment]:
        """말소리 구간만 이어 붙여(무음 제거) 묶음 단위로 글로 옮긴 뒤, 원래 시각으로 되돌립니다.

        whisper 는 30초 창 단위로 계산하므로, 무음이 많은 트랙(내가 듣고만 있는 마이크 등)을
        구간마다 따로 넣으면 짧은 구간도 30초만큼 계산합니다. 이어 붙이면 그 낭비가 없습니다.
        """
        out: list[Segment] = []
        total = sum(b - a for a, b in speech) or 1.0
        done = 0.0
        for pack in pack_segments(speech, pack_seconds):
            if cancel and cancel():
                raise InterruptedError
            pieces = [read_segment(wav16k, a, b) for a, b in pack]
            tmap = TimeMap([(a, len(p) / TARGET_SR) for (a, _), p in zip(pack, pieces)])
            audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
            del pieces
            if len(audio) >= TARGET_SR * 0.2:
                for s in self.transcribe_window(audio, 0.0, language, words):
                    out.extend(tmap.restore(s))
            done += sum(b - a for a, b in pack)
            if progress:
                progress(done / total)
        return out



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
                ow = Word(self.to_orig(w.start, i), self.to_orig(w.end, i), w.text)
                if groups and groups[-1][0] == i:
                    groups[-1][1].append(ow)
                else:
                    groups.append((i, [ow]))
            out = []
            for _, ws in groups:
                text = "".join(w.text for w in ws).strip()
                if text:
                    out.append(Segment(ws[0].start, ws[-1].end, text, s.lang, ws))
            return out
        i = self.index(s.start)
        j = self.index(max(s.start, s.end - 1e-3))
        return [Segment(self.to_orig(s.start, i), self.to_orig(s.end, j), s.text, s.lang, [])]
