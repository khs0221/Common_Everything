"""동봉 모델 목록과 확인.

모델은 빌드 때 `tools/download_models.py` 가 내려받아 exe 옆 `models/` 폴더에 넣습니다.
실행 중에는 내려받지 않습니다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .paths import models_dir

WHISPER_SMALL_DIR = "faster-whisper-small"                  # 발언 단위 언어 판별 전용(R6 수정: base → small 로 되돌림)
WHISPER_TURBO_DIR = "faster-whisper-large-v3-turbo-int8"    # 글로 옮기기(large-v3-turbo, int8 저장, R6)
WHISPER_DIR = WHISPER_SMALL_DIR  # 토크나이저(용어 힌트 토큰 수 세기)
WHISPER_DIRS = {"small": WHISPER_SMALL_DIR, "turbo": WHISPER_TURBO_DIR}
SEGMENTATION = "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"
EMBEDDING = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
VAD = "silero_vad.onnx"

GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
HF_SMALL = "https://huggingface.co/Systran/faster-whisper-small/resolve/536b0662742c02347bc0e980a01041f333bce120"
# turbo int8: 원본 openai/whisper-large-v3-turbo(MIT, 아래 커밋)를 빌드 때 CTranslate2 변환기로 직접 int8 저장
# (tools/convert_turbo_int8.py, 고정한 판의 변환 도구로 같은 바이트가 나옴을 확인). 받는 주소가 없고 'convert' 로 표시.
OPENAI_TURBO = "https://huggingface.co/openai/whisper-large-v3-turbo/resolve/41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
OPENAI_TURBO_FILES = {  # 변환에 쓰는 원본 파일과 해시
    "model.safetensors": "542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1",
    "config.json": "", "generation_config.json": "", "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
    "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    "tokenizer_config.json": "", "vocab.json": "", "merges.txt": "", "added_tokens.json": "",
    "special_tokens_map.json": "", "normalizer.json": "",
}


@dataclass(frozen=True)
class ModelFile:
    rel: str            # models/ 아래 경로
    url: str            # 빌드 때 받을 주소
    sha256: str
    archive_member: str | None = None  # 압축 파일 안의 경로(있으면)
    convert: str | None = None         # 받지 않고 빌드 때 변환해 만드는 파일(변환 이름)


# 해시는 받거나 변환해 확인한 값입니다(2026-11-23~25).
FILES: tuple[ModelFile, ...] = (
    # small: 언어 판별 전용. Systran/faster-whisper-small, license: mit(R6 수정 1회차: base 는 짧은 발언에서
    # 영어를 한국어로 판별하는 경우가 있어 되돌림)
    ModelFile(f"{WHISPER_SMALL_DIR}/model.bin", f"{HF_SMALL}/model.bin",
              "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    ModelFile(f"{WHISPER_SMALL_DIR}/config.json", f"{HF_SMALL}/config.json",
              "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    ModelFile(f"{WHISPER_SMALL_DIR}/tokenizer.json", f"{HF_SMALL}/tokenizer.json",
              "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    ModelFile(f"{WHISPER_SMALL_DIR}/vocabulary.txt", f"{HF_SMALL}/vocabulary.txt",
              "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    # large-v3-turbo int8 저장본(R6): 빌드 때 변환. preprocessor_config.json(멜 128개 설정) 포함
    ModelFile(f"{WHISPER_TURBO_DIR}/model.bin", "", "7482f978f9493b93b3d0bf68a148a1a62ca02547e8ee981358ff8ef2de8a9418",
              convert="turbo-int8"),
    ModelFile(f"{WHISPER_TURBO_DIR}/config.json", "", "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
              convert="turbo-int8"),
    ModelFile(f"{WHISPER_TURBO_DIR}/preprocessor_config.json", "",
              "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711", convert="turbo-int8"),
    ModelFile(f"{WHISPER_TURBO_DIR}/tokenizer.json", "",
              "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd", convert="turbo-int8"),
    ModelFile(f"{WHISPER_TURBO_DIR}/vocabulary.json", "",
              "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1", convert="turbo-int8"),
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
