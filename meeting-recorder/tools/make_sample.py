"""개발용: 시험용 합성 대화 음성(마이크 트랙 + 스피커 트랙)을 만듭니다.

이 스크립트는 프로그램(exe)에 들어가지 않습니다. 저장소의 samples/ 파일을
다시 만들 때만 씁니다.

필요한 것(개발 PC):
  pip install "sherpa-onnx==1.12.40" numpy soundfile
  (sherpa-onnx 2.x 는 espeak 발음 변환기가 빠져 이 음성 모델을 읽지 못합니다)
  음성 모델: https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-libritts_r-medium.tar.bz2
  (LibriTTS-R 기반 piper 음성, 모델 카드 표기 CC BY 4.0)

사용:
  python tools/make_sample.py <음성모델 폴더> <출력 폴더>
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

OUT_SR = 16000

# (발언자, 트랙, 문장) — 회의처럼 꾸민 영어 대화. 실제 회의 내용이 아닙니다.
VOICES = {"me": 296, "guest_a": 148, "guest_b": 259}
TRACK = {"me": "mic", "guest_a": "loop", "guest_b": "loop"}
SCRIPT = [
    ("me", "Good morning everyone. Thanks for joining the weekly project call."),
    ("guest_a", "Good morning. I can hear you clearly. Shall we start with the schedule?"),
    ("me", "Yes, please. Where are we with the test plan?"),
    ("guest_a", "The test plan is almost finished. I will send the final draft by Thursday."),
    ("guest_b", "I have a question about the budget. Do we still need approval from the finance team?"),
    ("guest_b", "Last month the approval took almost two weeks."),
    ("me", "Good point. I will ask the finance team today and share the answer tomorrow."),
    ("guest_a", "Great. Then the next step is the customer workshop in March."),
    ("guest_b", "I can prepare the slides for the workshop, but I need the latest numbers first."),
    ("me", "Okay. Let's agree that the numbers are shared by Friday. Any other issues?"),
    ("guest_a", "No other issues from my side. Thank you."),
    ("guest_b", "Nothing else from me. See you next week."),
]
GAP_S = {"same": 0.35, "change": 0.6}


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """FFT 기반 대역 제한 리샘플(짧은 표본용)."""
    n_out = int(round(len(x) * sr_out / sr_in))
    spec = np.fft.rfft(x)
    n_keep = n_out // 2 + 1
    spec = spec[:n_keep] if len(spec) >= n_keep else np.pad(spec, (0, n_keep - len(spec)))
    y = np.fft.irfft(spec, n=n_out) * (n_out / len(x))
    return y.astype(np.float32)


def main(model_dir: str, out_dir: str) -> None:
    import sherpa_onnx as so

    d = Path(model_dir)
    cfg = so.OfflineTtsConfig(
        model=so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(
                model=str(d / "en_US-libritts_r-medium.onnx"),
                tokens=str(d / "tokens.txt"),
                data_dir=str(d / "espeak-ng-data"),
                noise_scale=0.667,
                noise_scale_w=0.8,
            ),
            num_threads=4,
        )
    )
    tts = so.OfflineTts(cfg)
    clips = []
    t = 0.5
    prev = None
    for who, text in SCRIPT:
        if prev is not None:
            t += GAP_S["same" if prev == who else "change"]
        a = tts.generate(text, sid=VOICES[who], speed=1.0)
        x = resample(np.asarray(a.samples, dtype=np.float32), a.sample_rate, OUT_SR)
        x = x / max(1e-6, np.abs(x).max()) * 0.6
        clips.append((who, text, t, x))
        t += len(x) / OUT_SR
        prev = who
    total = int((t + 1.0) * OUT_SR)
    tracks = {"mic": np.zeros(total, np.float32), "loop": np.zeros(total, np.float32)}
    truth = []
    for who, text, start, x in clips:
        i = int(start * OUT_SR)
        tracks[TRACK[who]][i:i + len(x)] += x
        truth.append({"speaker": who, "track": TRACK[who], "start": round(start, 3),
                      "end": round(start + len(x) / OUT_SR, 3), "text": text})
    rng = np.random.default_rng(0)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, y in tracks.items():
        y = y + rng.normal(0, 0.0005, len(y)).astype(np.float32)  # 아주 작은 잡음
        sf.write(out / f"sample_{name}.flac", y, OUT_SR, subtype="PCM_16")
    (out / "sample_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"length {total / OUT_SR:.1f}s, utterances {len(truth)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
