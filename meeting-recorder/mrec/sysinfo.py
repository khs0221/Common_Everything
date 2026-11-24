"""메모리 최고치 등 기술 정보(회의 내용과 무관)."""
from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def peak_memory_mb() -> float | None:
    """이 프로세스의 메모리 최고치(MB). 윈도우는 PeakWorkingSetSize, 리눅스는 ru_maxrss."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return pmc.PeakWorkingSetSize / (1024 * 1024)
            return None
        import resource

        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / 1024 if sys.platform != "darwin" else r / (1024 * 1024)
    except Exception:
        return None


MAX_THREADS = 8


def compute_threads(cpu_count: int | None = None) -> int:
    """계산(글 옮기기·화자 분리)에 쓸 스레드 수: 논리 코어 수 - 1(화면 응답용 1개 남김), 1~MAX_THREADS.

    예: 4코어 → 3, 8코어(가상 PC 등) → 7, 16코어 → 8.
    """
    n = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    env = os.environ.get("MREC_THREADS")
    if env and env.isdigit() and int(env) > 0:
        return min(int(env), 64)
    return max(1, min(MAX_THREADS, n - 1 if n >= 2 else 1))


def total_memory_mb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys / (1024 * 1024)
            return None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024)
    except Exception:
        return None


def session_kind() -> str:
    """원격 세션 여부(간단 판단). 윈도우 SESSIONNAME 이 'RDP-' 로 시작하면 원격 데스크톱."""
    name = os.environ.get("SESSIONNAME", "")
    if name.upper().startswith("RDP-"):
        return f"원격 데스크톱({name})"
    if os.name == "nt":
        try:
            import ctypes

            if ctypes.windll.user32.GetSystemMetrics(0x1000):  # SM_REMOTESESSION
                return "원격 세션"
        except Exception:
            pass
        return f"로컬({name or '알 수 없음'})"
    return "해당 없음"


def system_summary() -> dict:
    """진단용 기술 정보(회의 내용 없음)."""
    mem = total_memory_mb()
    return {"cpu_logical": os.cpu_count(), "threads": compute_threads(),
            "memory_total_mb": round(mem) if mem else None, "session": session_kind()}


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "MeetingRecorder" / "logs"


_USER_PATH_RE = re.compile(r"(?i)\b([a-z]:[\\/]+(?:users|documents and settings)[\\/]+)([^\\/\r\n\"'<>|:]+)")
_HOME_RE = re.compile(r"(/home/|/Users/)([^/\s\"']+)")


def mask_user_paths(text: str) -> str:
    """사용자 폴더 경로의 이름 부분을 가립니다: C:\\Users\\홍길동\\… → C:\\Users\\<사용자>\\… (감사 E1)."""
    if not text:
        return text
    out = _USER_PATH_RE.sub(lambda m: m.group(1) + "<사용자>", text)
    out = _HOME_RE.sub(lambda m: m.group(1) + "<사용자>", out)
    try:
        home = str(Path.home())
        name = Path(home).name
        if name and len(name) >= 2:
            out = out.replace(home, str(Path(home).parent / "<사용자>"))
    except Exception:
        pass
    return out


class MaskingFormatter(logging.Formatter):
    """로그 줄(오류 traceback 포함)에서 사용자 폴더 경로를 가립니다."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_user_paths(super().format(record))


def setup_logging() -> Path | None:
    """기술 로그만 남깁니다(장치 이름·오류·처리 시간). 회의 내용(글)은 남기지 않습니다."""
    logger = logging.getLogger("mrec")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return None
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "app.log"
        h = RotatingFileHandler(p, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
        return p
    except OSError:
        return None


def keep_awake(on: bool) -> None:
    """윈도우: 녹음·처리 중 절전 모드로 들어가지 않게 합니다."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass
