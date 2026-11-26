"""프로그램 폴더·모델 폴더·저장 폴더 위치."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "MeetingRecorder"


def app_dir() -> Path:
    """exe 가 있는 폴더(개발 중에는 meeting-recorder 폴더)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """PyInstaller 가 넣은 데이터 파일 위치(개발 중에는 app_dir 과 같음)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()


def models_dir() -> Path:
    env = os.environ.get("MREC_MODELS_DIR")
    return Path(env) if env else app_dir() / "models"


def samples_dir() -> Path:
    return bundle_dir() / "samples"


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "회의기록"


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def user_glossary_dir() -> Path:
    return user_data_dir() / "glossary"


def load_settings() -> dict:
    p = settings_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    p = settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def native_path(p: Path | str) -> str:
    """C++ 라이브러리(ctranslate2, sherpa-onnx)에 넘길 경로.

    윈도우에서 경로에 한글 등 ASCII 가 아닌 글자가 있으면 C++ 쪽에서 파일을 못 여는
    경우가 있어, 짧은 경로(8.3 형식)로 바꿔 봅니다. 바꿀 수 없으면 원래 경로를 돌려주고
    호출한 쪽에서 오류 메시지를 보여 줍니다.
    """
    s = str(p)
    if s.isascii() or os.name != "nt":
        return s
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.GetShortPathNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(32768)
        n = fn(s, buf, len(buf))
        if n and buf.value.isascii():
            return buf.value
    except Exception:
        pass
    return s


def path_is_safe_for_native(p: Path | str) -> bool:
    return native_path(p).isascii()
