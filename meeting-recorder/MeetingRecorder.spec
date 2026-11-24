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
    ("samples/sample_mixed_lang.flac", "samples"),
    ("samples/sample_mixed_lang_truth.json", "samples"),
]
hiddenimports = ["sherpa_onnx", "ctranslate2", "tokenizers"]
if sys.platform == "win32":
    hiddenimports += ["soundcard", "soundcard.mediafoundation"]

# 넣지 않는 것: PyAV(GPL 구성요소 포함 FFmpeg, 용량), huggingface_hub(내려받기 코드),
# onnxruntime 파이썬 패키지(sherpa-onnx 가 자체 onnxruntime 을 씀 → 같은 이름 DLL 충돌 방지)
# 쓰지 않는 표준 모듈 중 접속 기능이 있는 것(ssl·메일·FTP 등)과 빌드 도구도 뺍니다(감사 F6·F7).
excludes = ["av", "huggingface_hub", "hf_xet", "httpx", "httpcore", "onnxruntime",
            "torch", "transformers", "tensorflow", "matplotlib", "IPython", "pytest",
            "scipy", "pandas", "PIL",
            "ssl", "_ssl", "smtplib", "ftplib", "xmlrpc", "http.server", "webbrowser",
            "setuptools", "_distutils_hack", "pkg_resources", "yaml"]

# 묶음에서 뺄 DLL: CTranslate2 휠에 든 cudnn64_9.dll(GPU 용, CPU 전용이라 쓰지 않음).
# ctranslate2.dll 의 가져오기 목록에 cudnn64_9.dll 이 없음을 확인했습니다(pefile, 2026-11-24).
DROP_BINARIES = ("cudnn",)

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if not any(k in b[0].lower() for k in DROP_BINARIES)]
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
