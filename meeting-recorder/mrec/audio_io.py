"""녹음 파일 쓰기·읽기, 표본 주파수 바꾸기.

- `WavTrackWriter`: 16비트 모노 WAV 를 디스크에 조금씩 흘려 씁니다. 매번 머리말(길이)을
  고쳐 쓰므로 프로그램이 갑자기 꺼져도 그때까지 쓴 부분은 정상 WAV 로 남습니다.
- `Resampler`: 48kHz 등 → 16kHz 변환(조각 단위, 메모리 일정).
- `prepare_16k_mono`: 처리용 16kHz 모노 WAV 를 준비합니다.
"""
from __future__ import annotations

import math
import os
import time
import wave
from fractions import Fraction
from pathlib import Path
from typing import Iterator

import numpy as np

TARGET_SR = 16000


def float_to_int16(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = np.clip(x, -1.0, 1.0)
    return (x * 32767.0).astype("<i2")


class WavTrackWriter:
    """16비트 모노 WAV 를 흘려 씁니다(메모리에 쌓지 않음)."""

    def __init__(self, path: Path | str, samplerate: int, fsync_interval_s: float = 5.0):
        self.path = Path(path)
        self.samplerate = int(samplerate)
        self.frames_written = 0
        self._fsync_interval = fsync_interval_s
        self._last_sync = time.monotonic()
        self._fh = open(self.path, "wb")
        self._wav = wave.open(self._fh, "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.samplerate)
        # 길이 0 머리말을 먼저 써 둡니다.
        self._wav.writeframes(b"")
        self._fh.flush()
        self.closed = False

    def write(self, samples: np.ndarray) -> None:
        if self.closed:
            raise ValueError("이미 닫힌 파일입니다")
        data = float_to_int16(samples)
        if data.size == 0:
            return
        # wave.writeframes 는 쓸 때마다 머리말의 길이를 고쳐 씁니다(_patchheader).
        self._wav.writeframes(data.tobytes())
        self.frames_written += int(data.size)
        self._fh.flush()
        now = time.monotonic()
        if now - self._last_sync >= self._fsync_interval:
            try:
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            self._last_sync = now

    def write_silence(self, frames: int) -> None:
        frames = int(frames)
        chunk = 16000
        while frames > 0:
            n = min(chunk, frames)
            self.write(np.zeros(n, dtype=np.float32))
            frames -= n

    @property
    def seconds(self) -> float:
        return self.frames_written / self.samplerate

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._wav.close()
        finally:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except (OSError, ValueError):
                pass
            self._fh.close()


class Resampler:
    """조각 단위 표본 주파수 변환기(창 함수를 씌운 sinc 다상 필터).

    같은 신호를 한 번에 넣든 여러 조각으로 나눠 넣든 결과가 같습니다.
    """

    def __init__(self, sr_in: int, sr_out: int = TARGET_SR, zero_crossings: int = 16, beta: float = 8.0):
        self.sr_in = int(sr_in)
        self.sr_out = int(sr_out)
        frac = Fraction(self.sr_out, self.sr_in)
        self.up, self.down = frac.numerator, frac.denominator
        self.passthrough = self.up == self.down
        if self.passthrough:
            return
        # 입력 표본 단위로 본 차단 주파수(나이퀴스트=0.5)
        cutoff = 0.5 * min(1.0, self.sr_out / self.sr_in) * 0.94
        self.half = int(math.ceil(zero_crossings / (2 * cutoff)))
        taps = np.arange(-self.half + 1, self.half + 1)  # 2*half 개
        # 위상 p(0..up-1) 별 필터: 출력 n 의 입력 위치 t = n*down/up, 소수부 = p/up
        phases = np.arange(self.up)[:, None] / self.up
        d = taps[None, :] - phases  # 입력 표본 k = floor(t)+tap, 거리 = k - t
        win = np.kaiser(2 * self.half + 1, beta)
        # 거리 d 에서의 창 값(선형 보간)
        win_v = np.interp(d + self.half, np.arange(2 * self.half + 1), win)
        h = 2 * cutoff * np.sinc(2 * cutoff * d) * win_v
        h /= h.sum(axis=1, keepdims=True)
        self.table = h.astype(np.float32)  # (up, 2*half)
        self._buf = np.zeros(self.half, dtype=np.float32)  # 앞쪽 여유(0 으로 시작)
        self._buf_start = -self.half  # _buf[0] 의 입력 위치
        self._n_out = 0  # 지금까지 만든 출력 수
        self._n_in = 0

    def process(self, x: np.ndarray, final: bool = False) -> np.ndarray:
        if self.passthrough:
            return np.asarray(x, dtype=np.float32)
        x = np.asarray(x, dtype=np.float32)
        self._buf = np.concatenate([self._buf, x])
        self._n_in += len(x)
        if final:
            self._buf = np.concatenate([self._buf, np.zeros(self.half, dtype=np.float32)])
            total_out = (self._n_in * self.up + self.down - 1) // self.down
        else:
            total_out = None
        buf_end = self._buf_start + len(self._buf)  # 버퍼 끝(배타) 입력 위치
        # 출력 n 은 입력 floor(t)+half 까지 필요
        outs = []
        n = self._n_out
        # 만들 수 있는 마지막 n: floor(n*down/up) + half < buf_end
        max_n = ((buf_end - self.half) * self.up - 1) // self.down
        if total_out is not None:
            max_n = min(max_n, total_out - 1)
        if max_n >= n:
            ns = np.arange(n, max_n + 1)
            num = ns * self.down
            base = num // self.up
            phase = num % self.up
            idx = (base - self._buf_start)[:, None] + np.arange(-self.half + 1, self.half + 1)[None, :]
            y = np.einsum("ij,ij->i", self._buf[idx], self.table[phase])
            outs.append(y.astype(np.float32))
            self._n_out = max_n + 1
        # 앞으로 필요한 가장 이른 입력: floor(_n_out*down/up) - half + 1
        need_from = (self._n_out * self.down) // self.up - self.half + 1
        drop = max(0, need_from - self._buf_start)
        if drop:
            self._buf = self._buf[drop:]
            self._buf_start += drop
        return outs[0] if outs else np.zeros(0, dtype=np.float32)


def wav_info(path: Path | str) -> tuple[int, int, int]:
    """(표본 주파수, 채널 수, 길이 표본 수)."""
    import soundfile as sf

    info = sf.info(str(path))
    return int(info.samplerate), int(info.channels), int(info.frames)


def is_16k_mono_wav(path: Path | str) -> bool:
    import soundfile as sf

    try:
        info = sf.info(str(path))
    except Exception:
        return False
    return info.format == "WAV" and info.samplerate == TARGET_SR and info.channels == 1


def iter_blocks(path: Path | str, block_frames: int = TARGET_SR * 10) -> Iterator[np.ndarray]:
    """모노 float32 조각을 차례로 돌려줍니다(여러 채널이면 평균)."""
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        while True:
            data = f.read(block_frames, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            yield data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]


def read_segment(path: Path | str, start_s: float, end_s: float) -> np.ndarray:
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        a = max(0, int(start_s * sr))
        b = min(f.frames, int(math.ceil(end_s * sr)))
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        f.seek(a)
        data = f.read(b - a, dtype="float32", always_2d=True)
    return data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]


def read_all(path: Path | str) -> np.ndarray:
    import soundfile as sf

    data, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]


def prepare_16k_mono(src: Path | str, dst: Path | str) -> Path:
    """처리용 16kHz 모노 WAV 를 만듭니다. 이미 그 형식이면 원본 경로를 그대로 돌려줍니다.

    읽을 수 있는 형식: WAV, FLAC, OGG, MP3(libsndfile 이 지원하는 형식).
    """
    import soundfile as sf

    src = Path(src)
    if is_16k_mono_wav(src):
        return src
    info = sf.info(str(src))
    rs = Resampler(int(info.samplerate), TARGET_SR)
    w = WavTrackWriter(dst, TARGET_SR, fsync_interval_s=60)
    try:
        for block in iter_blocks(src, int(info.samplerate) * 10):
            w.write(rs.process(block))
        w.write(rs.process(np.zeros(0, dtype=np.float32), final=True))
    finally:
        w.close()
    return Path(dst)


def slice_to_16k_mono(src: Path | str, dst: Path | str, start_s: float, end_s: float) -> Path:
    """원본 오디오의 [start_s, end_s] 구간만 16kHz 모노 WAV 로 추출합니다."""
    import soundfile as sf

    sr, _, frames = wav_info(src)
    a = max(0, int(round(start_s * sr)))
    b = min(frames, int(math.ceil(end_s * sr)))
    rs = None if sr == TARGET_SR else Resampler(sr, TARGET_SR)
    w = WavTrackWriter(dst, TARGET_SR, fsync_interval_s=60)
    try:
        if b > a:
            with sf.SoundFile(str(src)) as f:
                f.seek(a)
                remain = b - a
                step = sr * 10
                while remain > 0:
                    data = f.read(min(step, remain), dtype="float32", always_2d=True)
                    if len(data) == 0:
                        break
                    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
                    w.write(rs.process(mono) if rs is not None else mono)
                    remain -= len(data)
            if rs is not None:
                w.write(rs.process(np.zeros(0, dtype=np.float32), final=True))
    finally:
        w.close()
    return Path(dst)

