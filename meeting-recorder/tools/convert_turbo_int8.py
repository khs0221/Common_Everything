"""빌드용: whisper large-v3-turbo 원본을 CTranslate2 int8 저장본으로 변환합니다(R6).

원본: openai/whisper-large-v3-turbo(MIT, 커밋 고정, 파일 해시 확인). 변환 도구는 requirements-convert.txt 에
판을 고정했고, 이 조합으로 변환하면 model.bin 이 늘 같은 바이트가 됩니다(리눅스에서 두 번 변환해 같은 해시 확인).
결과 해시는 mrec/models.py 의 FILES 와 맞아야 합니다(다르면 실패).

사용(변환용 가상환경에서): python tools/convert_turbo_int8.py [models 폴더]
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from download_models import fetch, sha256  # noqa: E402
from mrec import models  # noqa: E402


def main(models_root: Path) -> int:
    out = models_root / models.WHISPER_TURBO_DIR
    targets = [f for f in models.FILES if f.convert == "turbo-int8"]
    if all((models_root / f.rel).is_file() and sha256(models_root / f.rel) == f.sha256 for f in targets):
        print("있음: turbo int8(해시 일치)")
        return 0
    src = Path(tempfile.mkdtemp(prefix="turbo_src_"))
    try:
        for name, digest in models.OPENAI_TURBO_FILES.items():
            print(f"받는 중: {name}", flush=True)
            fetch(f"{models.OPENAI_TURBO}/{name}", src / name)
            if digest and sha256(src / name) != digest:
                print(f"원본 해시 불일치: {name}")
                return 1
        from ctranslate2.converters import TransformersConverter

        tmp_out = Path(tempfile.mkdtemp(prefix="turbo_int8_")) / "out"
        TransformersConverter(str(src), copy_files=["tokenizer.json", "preprocessor_config.json"]).convert(
            str(tmp_out), quantization="int8", force=True)
        bad = []
        for f in targets:
            p = tmp_out / Path(f.rel).name
            if not p.is_file() or sha256(p) != f.sha256:
                bad.append(Path(f.rel).name)
        if bad:
            print("변환 결과 해시가 고정값과 다름:", bad)
            return 1
        out.mkdir(parents=True, exist_ok=True)
        for f in targets:
            shutil.copyfile(tmp_out / Path(f.rel).name, models_root / f.rel)
        print(f"완료: {out}")
        return 0
    finally:
        shutil.rmtree(src, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models"))
