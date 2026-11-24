# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 설정(onedir). 빌드: pyinstaller --noconfirm MeetingRecorder.spec
# 모델(models/)은 빌드 뒤 dist/MeetingRecorder/models 로 복사합니다(워크플로 참고).
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("sherpa_onnx") + collect_dynamic_libs("ctranslate2")
datas = [
    ("samples/sample_mic.flac", "samples"),
    ("samples/sample_loop.flac", "samples"),
    ("samples/sample_truth.json", "samples"),
]
hiddenimports = ["sherpa_onnx", "ctranslate2", "tokenizers"]
if sys.platform == "win32":
    hiddenimports += ["soundcard", "soundcard.mediafoundation"]

# 넣지 않는 것: PyAV(GPL 구성요소 포함 FFmpeg, 용량), huggingface_hub(내려받기 코드),
# onnxruntime 파이썬 패키지(sherpa-onnx 가 자체 onnxruntime 을 씀 → 같은 이름 DLL 충돌 방지)
excludes = ["av", "huggingface_hub", "hf_xet", "httpx", "httpcore", "onnxruntime",
            "torch", "transformers", "tensorflow", "matplotlib", "IPython", "pytest",
            "scipy", "pandas", "PIL"]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingRecorder",
    console=False,
    upx=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MeetingRecorder")
