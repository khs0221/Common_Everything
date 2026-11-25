"""짧은 말·작은 목소리 표본 구성(감사 R6 재감사 G1·G4). 저장소 표본에서 잘라 만듭니다.

자르는 시각은 turbo 낱말 시각(R6 수정 2회차 측정)에 앞뒤 여유를 둔 값입니다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

SR = 16000
SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# (파일, 시작, 끝, 말한 글, 언어)
SHORT = [
    ("sample_mic.flac", 7.60, 8.30, "Yes, please.", "en"),
    ("sample_mic.flac", 21.25, 21.80, "Good point.", "en"),
    ("sample_mic.flac", 32.90, 33.35, "Okay.", "en"),
    ("sample_mic.flac", 1.45, 2.20, "Thanks for joining", "en"),
    ("sample_loop.flac", 3.98, 4.58, "Good morning.", "en"),
    ("sample_loop.flac", 25.50, 25.98, "Great.", "en"),
    ("sample_loop.flac", 38.60, 39.12, "Thank you.", "en"),
    ("sample_loop.flac", 39.75, 40.62, "Nothing else from me.", "en"),
    ("sample_mixed_lang.flac", 3.84, 4.74, "모든 사람들에게", "ko"),
    ("sample_mixed_lang.flac", 4.66, 5.56, "영향을 미칩니다.", "ko"),
    ("sample_mixed_lang.flac", 7.60, 8.04, "사람들이", "ko"),
    ("sample_mixed_lang.flac", 36.12, 36.80, "가지고 있습니다.", "ko"),
    ("sample_mixed_lang.flac", 42.16, 43.02, "주로 서식한다.", "ko"),
    ("sample_mixed_lang.flac", 16.92, 17.86, "가능성이 낮다.", "ko"),
    ("sample_mixed_lang.flac", 56.24, 57.08, "사용된 미국", "ko"),
    ("sample_mixed_lang.flac", 35.44, 36.20, "캠핑 공간을", "ko"),
]
# 문장(작은 목소리 시험용)
SENTENCES = [
    ("sample_mic.flac", 7.60, 10.10, "Yes, please. Where are we with the test plan?", "en"),
    ("sample_loop.flac", 18.60, 20.75, "Last month the approval took almost two weeks.", "en"),
    ("sample_loop.flac", 39.75, 41.50, "Nothing else from me. See you next week.", "en"),
    ("sample_mixed_lang.flac", 6.42, 11.98, "스페인 사람들이 3세기 동안 지속된 식민지 시대를 시작했다.", "ko"),
    ("sample_mixed_lang.flac", 31.84, 36.96, "일부 축제들은 어린아이들이 있는 가족들을 위한 특별한 캠핑 공간을 가지고 있습니다.", "ko"),
    ("sample_mixed_lang.flac", 54.82, 61.44, "필리핀 정복에 사용된 미국 제국주의의 전쟁 비용은 필리핀인 자신들에 의해 지급되었다.", "ko"),
]


def _audio(name: str, cache: dict) -> np.ndarray:
    if name not in cache:
        import soundfile as sf

        x, sr = sf.read(SAMPLES / name, dtype="float32")
        assert sr == SR
        cache[name] = x
    return cache[name]


def build(items, levels_db=(0,), gap_s=1.0) -> tuple[np.ndarray, list[dict]]:
    """items 를 수준(dB)마다 차례로 이어 붙인 트랙과 정답 [{lang,start,end,text,db}]."""
    cache: dict = {}
    parts, truth, pos = [np.zeros(int(0.5 * SR), np.float32)], [], 0.5
    gap = np.zeros(int(gap_s * SR), np.float32)
    for db in levels_db:
        g = 10 ** (db / 20)
        for name, a, b, text, lang in items:
            seg = _audio(name, cache)[int(a * SR):int(b * SR)] * g
            truth.append({"lang": lang, "start": pos, "end": pos + len(seg) / SR, "text": text, "db": db})
            parts += [seg.astype(np.float32), gap]
            pos += len(seg) / SR + gap_s
    return np.concatenate(parts), truth


def units(text: str) -> list[str]:
    from mrec.selftest import _units

    return _units(text)


def found(truth: list[dict], utts) -> list[bool]:
    """정답 구절마다 겹치는 출력 글에 그 구절의 비교 단위가 절반 이상 있는지."""
    out = []
    for t in truth:
        txt = " ".join(u.text for u in utts if min(u.end, t["end"]) - max(u.start, t["start"]) > 0.1)
        have = set(units(txt))
        us = units(t["text"])
        out.append(bool(us) and sum(x in have for x in us) / len(us) >= 0.5)
    return out


def noise_track(seconds_each: float = 10.0) -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    """말소리 사이에 잡음 5종(각 seconds_each 초)을 넣은 트랙과 잡음 구간 [(시작, 끝, 종류)]."""
    cache: dict = {}
    rng = np.random.default_rng(0)
    n = int(seconds_each * SR)
    tt = np.arange(n) / SR
    noises = {
        "white-40dB": 0.01 * rng.normal(size=n),
        "white-30dB": 0.03 * rng.normal(size=n),
        "music": sum(0.08 * np.sin(2 * np.pi * f * tt) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * tt))
                     for f in (220, 277, 330, 440, 554)),
        "hum+clicks": 0.05 * np.sin(2 * np.pi * 60 * tt) + 0.3 * (rng.random(n) > 0.9995),
        "near-silence": 0.0005 * rng.normal(size=n),
    }
    speech = [SENTENCES[0], SENTENCES[3], SENTENCES[1], SENTENCES[4], SENTENCES[2], SENTENCES[5]]
    parts, regions, pos = [], [], 0.0
    for i, (kind, z) in enumerate(noises.items()):
        name, a, b, _, _ = speech[i]
        s = _audio(name, cache)[int(a * SR):int(b * SR)]
        parts += [s, z.astype(np.float32)]
        pos += len(s) / SR
        regions.append((pos, pos + seconds_each, kind))
        pos += seconds_each
    name, a, b, _, _ = speech[5]
    parts.append(_audio(name, cache)[int(a * SR):int(b * SR)])
    return np.concatenate(parts).astype(np.float32), regions
