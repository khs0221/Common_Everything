"""동봉 모델 목록과 확인.

모델은 빌드 때 `tools/download_models.py` 가 내려받아 exe 옆 `models/` 폴더에 넣습니다.
실행 중에는 내려받지 않습니다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .paths import models_dir

WHISPER_DIR = "faster-whisper-small"                  # 빠름(small)
WHISPER_TURBO_DIR = "faster-whisper-large-v3-turbo"   # 정확(large-v3-turbo, 기본, R4 측정으로 medium 대신 채택)
WHISPER_DIRS = {"small": WHISPER_DIR, "turbo": WHISPER_TURBO_DIR}
SEGMENTATION = "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"
EMBEDDING = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
VAD = "silero_vad.onnx"

GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
HF = "https://huggingface.co/Systran/faster-whisper-small/resolve/536b0662742c02347bc0e980a01041f333bce120"
# faster-whisper 1.2.1 이 'large-v3-turbo' 이름으로 쓰는 CTranslate2 변환본(openai/whisper-large-v3-turbo, MIT)
HF_TURBO = "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"


@dataclass(frozen=True)
class ModelFile:
    rel: str            # models/ 아래 경로
    url: str            # 빌드 때 받을 주소
    sha256: str
    archive_member: str | None = None  # 압축 파일 안의 경로(있으면)


# 2026-11-23 에 내려받아 확인한 해시값입니다.
FILES: tuple[ModelFile, ...] = (
    ModelFile(f"{WHISPER_DIR}/model.bin", f"{HF}/model.bin",
              "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    ModelFile(f"{WHISPER_DIR}/config.json", f"{HF}/config.json",
              "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    ModelFile(f"{WHISPER_DIR}/tokenizer.json", f"{HF}/tokenizer.json",
              "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    ModelFile(f"{WHISPER_DIR}/vocabulary.txt", f"{HF}/vocabulary.txt",
              "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    # large-v3-turbo: 2026-11-24 받음, 모델 카드 license: mit. preprocessor_config.json(멜 128개 설정)도 필요
    ModelFile(f"{WHISPER_TURBO_DIR}/model.bin", f"{HF_TURBO}/model.bin",
              "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
    ModelFile(f"{WHISPER_TURBO_DIR}/config.json", f"{HF_TURBO}/config.json",
              "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
    ModelFile(f"{WHISPER_TURBO_DIR}/preprocessor_config.json", f"{HF_TURBO}/preprocessor_config.json",
              "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
    ModelFile(f"{WHISPER_TURBO_DIR}/tokenizer.json", f"{HF_TURBO}/tokenizer.json",
              "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
    ModelFile(f"{WHISPER_TURBO_DIR}/vocabulary.json", f"{HF_TURBO}/vocabulary.json",
              "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    ModelFile(SEGMENTATION, f"{GH}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
              "d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d",
              archive_member="sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"),
    ModelFile("sherpa-onnx-pyannote-segmentation-3-0/LICENSE",
              f"{GH}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
              "", archive_member="sherpa-onnx-pyannote-segmentation-3-0/LICENSE"),
    ModelFile(EMBEDDING, f"{GH}/speaker-recongition-models/{EMBEDDING}",
              "98daf9bcf1c8abf3db374fec4c27a5eda7c58705bb3f180d057b085a72a88bb5"),
    ModelFile(VAD, f"{GH}/asr-models/silero_vad.onnx",
              "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def missing_models(root: Path | None = None) -> list[str]:
    """없는 모델 파일 목록(빠른 확인: 존재·크기만)."""
    root = root or models_dir()
    return [f.rel for f in FILES if not (root / f.rel).is_file() or (root / f.rel).stat().st_size == 0]


def missing_for(model: str, auto_language: bool = True, root: Path | None = None) -> list[str]:
    """이 처리에 필요한 파일 중 없는 것(감사 R4 E2: 한 모델만 없어도 다른 품질은 쓸 수 있게).

    필요한 것: whisper 가 아닌 모델(화자 분리·VAD) + 고른 whisper 모델 + 언어 자동이면 판별용 small.
    """
    root = root or models_dir()
    need_dirs = {WHISPER_DIRS[model]}
    if auto_language:
        need_dirs.add(WHISPER_DIRS["small"])
    all_dirs = set(WHISPER_DIRS.values())
    out = []
    for f in FILES:
        top = f.rel.split("/")[0]
        if top in all_dirs and top not in need_dirs:
            continue
        p = root / f.rel
        if not p.is_file() or p.stat().st_size == 0:
            out.append(f.rel)
    return out


def verify_models(root: Path | None = None) -> list[str]:
    """해시가 다른 파일 목록(느림: 전체를 읽음)."""
    root = root or models_dir()
    bad = []
    for f in FILES:
        p = root / f.rel
        if not p.is_file():
            bad.append(f.rel)
        elif f.sha256 and sha256_of(p) != f.sha256:
            bad.append(f.rel)
    return bad


def path(rel: str, root: Path | None = None) -> Path:
    return (root or models_dir()) / rel
