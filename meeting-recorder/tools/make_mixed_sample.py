"""개발용: 혼합 언어 시험 표본(한 트랙 안에서 한국어 → 영어 → 한국어 전환)을 만듭니다.

프로그램에는 결과 파일(samples/sample_mixed_lang.flac, sample_mixed_lang_truth.json)만 들어갑니다.

원본: Google FLEURS (https://huggingface.co/datasets/google/fleurs), 라이선스 CC BY 4.0.
  ko_kr / en_us 의 dev 분할 음성. 받는 법(개발 PC):
    https://huggingface.co/datasets/google/fleurs/resolve/main/data/ko_kr/audio/dev.tar.gz (+ dev.tsv)
    https://huggingface.co/datasets/google/fleurs/resolve/main/data/en_us/audio/dev.tar.gz (+ dev.tsv)
  각각 풀어 <폴더>/dev/*.wav 와 <폴더>/dev.tsv 가 되게 둡니다.
변경: 발언 앞뒤 무음을 잘라 크기를 맞추고(최대 0.5), 0.3초 간격으로 이어 붙였습니다(같은 사람이 쉬지 않고
언어를 바꾸는 흐름을 흉내. 실제로는 서로 다른 화자입니다).

사용: python tools/make_mixed_sample.py <ko 폴더> <en 폴더> <출력 폴더>
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000
# (언어, FLEURS 파일 이름) — 2026-11-24 고름(짧은 문장 위주)
PLAN = [
    ("ko", "3362819189131912553.wav"),
    ("ko", "15198157449680673582.wav"),
    ("ko", "4772966059713591976.wav"),
    ("en", "8452100455463097357.wav"),
    ("en", "6415341913845555034.wav"),
    ("en", "10238777279729691182.wav"),
    ("ko", "9932433357052887169.wav"),
    ("ko", "8861953164890988075.wav"),
    ("en", "5498038997689553356.wav"),
    ("en", "10146705666908229607.wav"),
    ("tts", "sample_mic 0"),  # 저장소의 합성 표본 '나' 첫 문장(영어)
    ("ko", "13980972984626671687.wav"),
]
GAP_S = 0.3


def trim_norm(x: np.ndarray) -> np.ndarray:
    fr = x[: len(x) // 320 * 320].reshape(-1, 320)
    e = np.sqrt((fr ** 2).mean(axis=1))
    idx = np.flatnonzero(e > 0.1 * e.max())
    if len(idx):
        x = x[max(0, idx[0] - 3) * 320:(idx[-1] + 4) * 320]
    return (x / max(1e-6, np.abs(x).max()) * 0.5).astype(np.float32)


def main(ko_dir: str, en_dir: str, out_dir: str) -> None:
    dirs = {"ko": Path(ko_dir), "en": Path(en_dir)}
    texts = {}
    for lang, d in dirs.items():
        for r in csv.reader(open(d / "dev.tsv", encoding="utf-8"), delimiter="\t"):
            if len(r) > 2:
                texts[(lang, r[1])] = r[2]
    root = Path(__file__).resolve().parent.parent / "samples"
    mic, _ = sf.read(root / "sample_mic.flac", dtype="float32")
    mic_truth = [u for u in json.loads((root / "sample_truth.json").read_text(encoding="utf-8")) if u["track"] == "mic"]
    t, clips, truth = 0.5, [], []
    for lang, name in PLAN:
        if lang == "tts":
            u = mic_truth[int(name.split()[-1])]
            x = mic[int(u["start"] * SR):int(u["end"] * SR)]
            lang, text, source = "en", u["text"], "합성음(samples/sample_mic.flac)"
        else:
            x, sr = sf.read(dirs[lang] / "dev" / name, dtype="float32")
            assert sr == SR
            text, source = texts.get((lang, name), ""), f"FLEURS {lang} dev {name}"
        x = trim_norm(x)
        clips.append((t, x))
        truth.append({"lang": lang, "start": round(t, 2), "end": round(t + len(x) / SR, 2),
                      "text": text, "source": source})
        t += len(x) / SR + GAP_S
    y = np.zeros(int((t + 0.5) * SR), np.float32)
    for s, x in clips:
        y[int(s * SR):int(s * SR) + len(x)] += x
    out = Path(out_dir)
    sf.write(out / "sample_mixed_lang.flac", y, SR, subtype="PCM_16")
    (out / "sample_mixed_lang_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"length {len(y) / SR:.1f}s")


if __name__ == "__main__":
    main(*sys.argv[1:4])
