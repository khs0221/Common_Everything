"""말소리 구간 찾기(silero VAD, sherpa-onnx 로 실행). 파일을 조각으로 읽어 메모리가 일정합니다."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from . import models
from .audio_io import TARGET_SR, iter_blocks, wav_info
from .paths import native_path

WINDOW = 512  # silero VAD 16kHz 창 크기
# 음량 맞추기(R4): 말소리 찾기에 넣는 소리만 키웁니다(글로 옮기는 소리·녹음 파일은 그대로).
# 트랙 전체에서 큰 쪽 5% 구간의 크기(32ms 창 RMS 의 95번째 백분위)가 TARGET 보다 작을 때만,
# 트랙 전체에 같은 배율을 곱합니다(최대 MAX_GAIN). 보통 크기의 트랙은 배율 1 이라 결과가 같습니다.
GAIN_TARGET_RMS = 0.05   # 약 -26 dBFS
GAIN_MAX = 31.6          # 최대 +30 dB
GAIN_FRAME = 512


def track_gain(wav16k: Path) -> float:
    """말소리 찾기용 배율(1.0 이상). 파일을 한 번 훑어 계산합니다(메모리 일정)."""
    rms = []
    for block in iter_blocks(wav16k, TARGET_SR * 30):
        n = len(block) // GAIN_FRAME * GAIN_FRAME
        if n:
            fr = block[:n].reshape(-1, GAIN_FRAME).astype(np.float64)
            rms.append(np.sqrt((fr ** 2).mean(axis=1)))
    if not rms:
        return 1.0
    p95 = float(np.percentile(np.concatenate(rms), 95))
    if p95 <= 1e-6:
        return 1.0  # 완전한 무음
    return float(min(GAIN_MAX, max(1.0, GAIN_TARGET_RMS / p95)))


def speech_segments(wav16k: Path, progress: Callable[[float], None] | None = None,
                    threshold: float = 0.5, min_silence_s: float = 0.5,
                    min_speech_s: float = 0.25, max_speech_s: float = 20.0,
                    pad_s: float = 0.2, cancel: Callable[[], bool] | None = None,
                    auto_gain: bool = True) -> list[tuple[float, float]]:
    """[(시작초, 끝초)] 말소리 구간. 앞뒤로 pad_s 만큼 넓히고 겹치면 합칩니다.

    auto_gain: 아주 작게 녹음된 트랙(마이크 입력이 작은 PC 등)은 말소리 찾기용으로만 키웁니다.
    """
    import sherpa_onnx

    gain = track_gain(wav16k) if auto_gain else 1.0
    speech_segments.last_gain = gain

    cfg = sherpa_onnx.VadModelConfig(
        silero_vad=sherpa_onnx.SileroVadModelConfig(
            model=native_path(models.path(models.VAD)), threshold=threshold,
            min_silence_duration=min_silence_s, min_speech_duration=min_speech_s,
            window_size=WINDOW, max_speech_duration=max_speech_s),
        sample_rate=TARGET_SR, num_threads=1)
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=max_speech_s * 3)
    sr, _, total = wav_info(wav16k)
    assert sr == TARGET_SR
    out: list[tuple[float, float]] = []

    def drain():
        while not vad.empty():
            seg = vad.front
            start = seg.start / TARGET_SR
            out.append((start, start + len(seg.samples) / TARGET_SR))
            vad.pop()

    done = 0
    rest = np.zeros(0, dtype=np.float32)
    for block in iter_blocks(wav16k, TARGET_SR * 30):
        if cancel and cancel():
            raise InterruptedError
        if gain != 1.0:
            block = np.clip(block * gain, -1.0, 1.0)
        buf = np.concatenate([rest, block])
        n = len(buf) // WINDOW * WINDOW
        for i in range(0, n, WINDOW):
            vad.accept_waveform(buf[i:i + WINDOW])
        rest = buf[n:]
        drain()
        done += len(block)
        if progress and total:
            progress(done / total)
    if len(rest):
        vad.accept_waveform(np.pad(rest, (0, WINDOW - len(rest))))
    vad.flush()
    drain()
    dur = total / TARGET_SR
    return pad_segments(out, pad_s, dur)


def pad_segments(segs: list[tuple[float, float]], pad_s: float, dur: float) -> list[tuple[float, float]]:
    """앞뒤로 pad_s 넓히되, 이웃과 겹치면 가운데에서 나눕니다(구간 길이가 크게 늘지 않게)."""
    segs = sorted(segs)
    out: list[list[float]] = []
    for a, b in segs:
        a, b = max(0.0, a - pad_s), min(dur, b + pad_s)
        if out and a < out[-1][1]:
            mid = (a + pad_s + out[-1][1] - pad_s) / 2
            mid = min(max(mid, out[-1][0]), b)
            out[-1][1] = mid
            a = mid
        out.append([a, b])
    return [(a, b) for a, b in out if b > a]

