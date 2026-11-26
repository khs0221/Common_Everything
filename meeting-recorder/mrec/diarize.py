"""발언자 구분(스피커 트랙): sherpa-onnx 오프라인 화자 분리.

- 구간 나누기: pyannote segmentation-3.0 (sherpa-onnx 가 ONNX 로 바꾼 int8 판)
- 목소리 특징: 3D-Speaker CAM++ (VoxCeleb 영어 학습판)
- 묶기: 거리 기준 군집(참석자 수를 알면 그 수로 고정)

창 이동 비율 0.25: 0.1(기본)보다 약 2.5배 빠르고, 시험 음성에서 경계 오차가 거의 같았습니다.
측정값은 docs/라운드/R1/changes.md 참고.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import models, sysinfo
from .audio_io import TARGET_SR, read_all, read_segment, wav_info
from .paths import native_path

DEFAULT_THRESHOLD = 0.7
WINDOW_SHIFT = 0.25
PACK_GAP_S = 0.6          # 떨어진 말소리 구간 사이에 넣는 짧은 무음(화자 경계 구분용)
PACK_SKIP_RATIO = 0.85    # 말소리가 전체 길이의 85% 이상이면 패킹 없이 통째로 처리


@dataclass
class Turn:
    start: float
    end: float
    speaker: int


def make_diarizer(num_speakers: int | None = None, threshold: float = DEFAULT_THRESHOLD,
                  threads: int | None = None):
    import sherpa_onnx

    threads = threads or sysinfo.compute_threads()
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=native_path(models.path(models.SEGMENTATION)), window_shift_ratio=WINDOW_SHIFT),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=native_path(models.path(models.EMBEDDING)), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=int(num_speakers) if num_speakers else -1, threshold=threshold),
        min_duration_on=0.3, min_duration_off=0.5)
    if not cfg.validate():
        raise RuntimeError("화자 분리 모델 설정이 올바르지 않습니다(모델 파일 확인 필요)")
    return sherpa_onnx.OfflineSpeakerDiarization(cfg)


def _pack_speech(wav16k: Path, speech: list[tuple[float, float]]):
    """말소리 구간만 이어 붙인 오디오와 [(packed_start, packed_end, orig_start)] 매핑을 반환합니다."""
    import numpy as np

    parts: list[np.ndarray] = []
    mapping: list[tuple[float, float, float]] = []
    gap = np.zeros(int(round(PACK_GAP_S * TARGET_SR)), dtype=np.float32)
    pos = 0.0
    prev_end: float | None = None
    for a, b in sorted(speech):
        if b <= a:
            continue
        seg = read_segment(wav16k, a, b)
        if seg.size == 0:
            continue
        if prev_end is not None and a - prev_end > 0.5:
            parts.append(gap)
            pos += len(gap) / TARGET_SR
        dur = len(seg) / TARGET_SR
        parts.append(seg)
        mapping.append((pos, pos + dur, a))
        pos += dur
        prev_end = b
    audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    return audio, mapping


def _restore_turns(packed_turns: list[Turn], mapping: list[tuple[float, float, float]]) -> list[Turn]:
    """패킹된 오디오의 화자 구간을 원래 녹음 시각으로 되돌립니다(삽입된 무음 구간 제외)."""
    out: list[Turn] = []
    for t in packed_turns:
        for ps, pe, orig_s in mapping:
            if pe <= t.start:
                continue
            if ps >= t.end:
                break
            ov_s = max(t.start, ps)
            ov_e = min(t.end, pe)
            if ov_e > ov_s + 1e-3:
                out.append(Turn(orig_s + (ov_s - ps), orig_s + (ov_e - ps), t.speaker))
    out.sort(key=lambda x: (x.start, x.end))
    return out


def diarize_file(wav16k: Path, num_speakers: int | None = None, threshold: float = DEFAULT_THRESHOLD,
                 progress: Callable[[float], None] | None = None,
                 cancel: Callable[[], bool] | None = None,
                 speech: list[tuple[float, float]] | None = None) -> list[Turn]:
    sd = make_diarizer(num_speakers, threshold)
    assert sd.sample_rate == TARGET_SR
    mapping = None
    if speech:
        _, _, total_frames = wav_info(wav16k)
        total_dur = total_frames / TARGET_SR if total_frames else 0.0
        speech_dur = sum(max(0.0, b - a) for a, b in speech)
        if total_dur > 0 and speech_dur < total_dur * PACK_SKIP_RATIO:
            audio, mapping = _pack_speech(wav16k, speech)
        else:
            audio = read_all(wav16k)
    else:
        audio = read_all(wav16k)
    if len(audio) < TARGET_SR:  # 1초 미만
        return []

    def cb(done, total):
        if progress and total:
            progress(done / total)
        return 1 if (cancel and cancel()) else 0

    result = sd.process(audio, callback=cb).sort_by_start_time()
    del audio
    if cancel and cancel():
        raise InterruptedError
    turns = [Turn(float(r.start), float(r.end), int(r.speaker)) for r in result]
    return _restore_turns(turns, mapping) if mapping is not None else turns
