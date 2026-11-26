"""화면(tkinter 한 창)."""
from __future__ import annotations

import logging
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import merge, models, output, paths, pipeline, sysinfo
from .devices import DeviceInfo, check_device, check_text, default_backend
from .recorder import LOOP_FILE, MIC_FILE, Recorder, new_meeting_folder

log = logging.getLogger("mrec")

LANG_CHOICES = [("자동(영어/한국어)", None), ("영어", "en"), ("한국어", "ko")]
SPK_CHOICES = [("자동", None)] + [(f"{n}명", n) for n in range(1, 13)]
MIC_CUT_CHOICES = [
    ("끔 (모든 소리)", None),
    ("약하게 (-50 dB)", -50.0),
    ("보통 (-40 dB, 기본)", -40.0),
    ("강하게 (-30 dB)", -30.0),
]
# 처리 품질: (화면 이름, 설정 값, whisper 모델)
# R6: '빠름'(small)을 없애고 large-v3-turbo 하나만 씁니다(묶음 크기 축소). 언어 판별은 small.
TRANSCRIBE_MODEL = "turbo"
# 예상 처리 시간(녹음 길이 대비). 측정 근거: docs/라운드/R4/changes.md. 사용자 PC 는 더 느릴 수 있습니다.
SPEED_NOTE = "처리 시간: 녹음 길이의 약 {m}배(4코어 기준 측정. 코어가 많으면 더 빠름)"
SILENT_WARN_S = 60  # 이 시간 동안 소리가 전혀 없으면 안내
AUTO_DEVICE = "자동(윈도우에서 쓰는 장치 그대로)"
SPEED_FACTOR = "0.4"  # 4코어 컨테이너 측정(R4·R6). 코어가 많으면 더 빠를 수 있음
LOG_TAIL_LINES = 30


def _exc_text(e: BaseException, lines: int = 10) -> str:
    import traceback

    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip().splitlines()
    return f"{type(e).__name__}: {e}\n" + "\n".join(tb[-lines:])


def error_report(where: str, err_text: str, devices: dict | None = None) -> str:
    """[오류 내용 복사] 에 담는 글: 버전·윈도우 버전·PC 사양·장치·오류·최근 기술 로그(회의 내용 없음)."""
    from . import __version__

    si = sysinfo.system_summary()
    lines = [f"회의 녹음·기록 {__version__}", f"윈도우/OS: {platform.platform()}",
             f"파이썬: {sys.version.split()[0]}, exe: {bool(getattr(sys, 'frozen', False))}",
             f"CPU 논리 코어: {si['cpu_logical']}, 계산 스레드: {si['threads']}, "
             f"총 메모리: {si['memory_total_mb']}MB, 세션: {si['session']}"]
    if devices:
        lines.append("마지막으로 연 장치: " + ", ".join(f"{('마이크' if k == 'mic' else '스피커')}={v}"
                                                   for k, v in devices.items()))
    lines += [f"문제: {where}", "", err_text.rstrip(), "", f"최근 로그(최대 {LOG_TAIL_LINES}줄):"]
    try:
        log_file = sysinfo.log_dir() / "app.log"
        tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
        lines += tail or ["(로그 없음)"]
    except OSError:
        lines.append("(로그 없음)")
    # 사용자 폴더 경로(C:\\Users\\<이름>)는 가립니다(감사 E1).
    return sysinfo.mask_user_paths("\n".join(lines))


def fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def open_folder(p: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as e:
        messagebox.showerror("폴더 열기", f"폴더를 열 수 없습니다.\n{p}\n{e}")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = paths.load_settings()
        self.out_dir = Path(self.settings.get("output_dir") or paths.default_output_dir())
        self.q: queue.Queue = queue.Queue()
        self.recorder: Recorder | None = None
        self.backend = None
        self.backend_error = None
        self.mics: list[DeviceInfo] = []
        self.spks: list[DeviceInfo] = []
        self.last_folder: Path | None = None
        self.processing = False
        self.cancel_flag = threading.Event()
        self.silent_since = {"mic": None, "loop": None}
        self.last_devices: dict = {}

        root.title("회의 녹음·기록")
        root.minsize(560, 0)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._init_backend()
        self.root.after(200, self._tick)
        self._check_models()

    # ------------------------------------------------------------ 화면 구성
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        f = ttk.LabelFrame(self.root, text="1. 장치")
        f.pack(fill="x", **pad)
        ttk.Label(f, text="내 마이크").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.mic_cb = ttk.Combobox(f, state="readonly", width=48, values=[AUTO_DEVICE])
        self.mic_cb.grid(row=0, column=1, sticky="we", padx=6)
        self.mic_cb.current(0)
        ttk.Label(f, text="상대방 소리(스피커)").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.spk_cb = ttk.Combobox(f, state="readonly", width=48, values=[AUTO_DEVICE])
        self.spk_cb.grid(row=1, column=1, sticky="we", padx=6)
        self.spk_cb.current(0)
        ttk.Button(f, text="장치 새로고침", command=self.refresh_devices).grid(row=0, column=2, rowspan=2, padx=6)
        ttk.Label(f, text="보통은 '자동' 그대로 두십시오. 특정 장치로 고정하고 싶을 때만 고릅니다.",
                  foreground="#555").grid(row=2, column=0, columnspan=2, sticky="w", padx=6)
        self.check_btn = ttk.Button(f, text="장치 점검", command=self.check_devices)
        self.check_btn.grid(row=2, column=2, padx=6, pady=2)
        # 장치 계층·목록 문제를 숨기지 않고 보여 주는 칸(문제 있을 때만 보임)
        self.dev_err = tk.StringVar(value="")
        self.dev_err_label = tk.Label(f, textvariable=self.dev_err, fg="#b71c1c", wraplength=430,
                                      justify="left", anchor="w")
        self.copy_btn = ttk.Button(f, text="오류 내용 복사", command=self.copy_error)
        self._err_report = ""
        f.columnconfigure(1, weight=1)

        f = ttk.LabelFrame(self.root, text="2. 녹음")
        f.pack(fill="x", **pad)
        self.start_btn = ttk.Button(f, text="● 녹음 시작", command=self.start_recording)
        self.start_btn.grid(row=0, column=0, padx=6, pady=6)
        self.pause_btn = ttk.Button(f, text="❚❚ 일시정지", command=self.toggle_pause, state="disabled")
        self.pause_btn.grid(row=0, column=1, padx=6)
        self.stop_btn = ttk.Button(f, text="■ 중지", command=self.stop_recording, state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=6)
        self.elapsed_var = tk.StringVar(value="00:00:00")
        ttk.Label(f, textvariable=self.elapsed_var, font=("Segoe UI", 16)).grid(row=0, column=3, padx=12)
        ttk.Label(f, text="나(마이크)").grid(row=1, column=0, sticky="w", padx=6)
        self.mic_bar = ttk.Progressbar(f, maximum=100, length=300)
        self.mic_bar.grid(row=1, column=1, columnspan=3, sticky="we", padx=6, pady=2)
        ttk.Label(f, text="상대방(스피커)").grid(row=2, column=0, sticky="w", padx=6)
        self.loop_bar = ttk.Progressbar(f, maximum=100, length=300)
        self.loop_bar.grid(row=2, column=1, columnspan=3, sticky="we", padx=6, pady=2)
        self.rec_warn = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.rec_warn, foreground="#b35c00", wraplength=520).grid(
            row=3, column=0, columnspan=4, sticky="w", padx=6)
        # 장치 끊김 등 지금 녹음에 문제가 있을 때 띄우는 빨간 경고
        self.rec_alarm = tk.StringVar(value="")
        self.alarm_label = tk.Label(f, textvariable=self.rec_alarm, fg="white", bg="#c62828",
                                    font=("Segoe UI", 11, "bold"), wraplength=520, justify="left")
        self._alarm_shown = False
        f.columnconfigure(3, weight=1)

        f = ttk.LabelFrame(self.root, text="3. 글로 옮기기")
        f.pack(fill="x", **pad)
        ttk.Label(f, text="회의 언어").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.lang_cb = ttk.Combobox(f, state="readonly", width=20, values=[n for n, _ in LANG_CHOICES])
        self.lang_cb.grid(row=0, column=1, sticky="w", padx=6)
        lang = self.settings.get("language")
        self.lang_cb.current(next((i for i, (_, v) in enumerate(LANG_CHOICES) if v == lang), 0))
        ttk.Label(f, text="상대방 인원(나 제외)").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.spk_n_cb = ttk.Combobox(f, state="readonly", width=20, values=[n for n, _ in SPK_CHOICES])
        self.spk_n_cb.grid(row=1, column=1, sticky="w", padx=6)
        self.spk_n_cb.current(0)
        ttk.Label(f, text="마이크 소음 차단").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.mic_cut_cb = ttk.Combobox(f, state="readonly", width=20, values=[n for n, _ in MIC_CUT_CHOICES])
        self.mic_cut_cb.grid(row=2, column=1, sticky="w", padx=6)
        saved_cut = self.settings.get("mic_min_db", -40.0) if "mic_min_db" in self.settings else -40.0
        self.mic_cut_cb.current(next((i for i, (_, v) in enumerate(MIC_CUT_CHOICES) if v == saved_cut), 2))
        ttk.Label(f, text="이번 회의 용어").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        self.meeting_terms_var = tk.StringVar(value="")
        self.meeting_terms_entry = ttk.Entry(f, textvariable=self.meeting_terms_var, width=42)
        self.meeting_terms_entry.grid(row=3, column=1, columnspan=2, sticky="we", padx=6, pady=3)
        ttk.Label(f, text="쉼표로 구분해 넣으면 이번 회의의 인식 힌트와 교정 용어 최상단에 우선 반영됩니다.",
                  foreground="#555").grid(row=4, column=0, columnspan=3, sticky="w", padx=6)
        self.speed_var = tk.StringVar(value=SPEED_NOTE.format(m=SPEED_FACTOR))
        ttk.Label(f, textvariable=self.speed_var, foreground="#555").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=6)
        self.glossary_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.glossary_var, foreground="#555", wraplength=420).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Button(f, text="용어 파일 열기", command=self.open_glossary).grid(row=6, column=2, padx=6, pady=2)
        self._update_glossary_note()
        self.auto_var = tk.BooleanVar(value=self.settings.get("auto_process", True))
        ttk.Checkbutton(f, text="녹음을 멈추면 바로 글로 옮기기", variable=self.auto_var).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        self.prog = ttk.Progressbar(f, maximum=100, length=300)
        self.prog.grid(row=8, column=0, columnspan=2, sticky="we", padx=6, pady=4)
        self.cancel_btn = ttk.Button(f, text="처리 취소", command=self.cancel_processing, state="disabled")
        self.cancel_btn.grid(row=8, column=2, padx=6)
        self.prog_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.prog_var).grid(row=9, column=0, columnspan=3, sticky="w", padx=6)
        f.columnconfigure(1, weight=1)

        f = ttk.LabelFrame(self.root, text="4. 결과")
        f.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=f"저장 폴더: {self.out_dir}")
        ttk.Label(f, textvariable=self.out_var, wraplength=420).grid(row=0, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Button(f, text="저장 폴더 바꾸기", command=self.change_out_dir).grid(row=0, column=2, padx=6, pady=3)
        self.open_btn = ttk.Button(f, text="결과 폴더 열기", command=self.open_result, state="disabled")
        self.open_btn.grid(row=1, column=0, padx=6, pady=4, sticky="w")
        self.names_btn = ttk.Button(f, text="참석자 이름 정하기", command=self.edit_names, state="disabled")
        self.names_btn.grid(row=1, column=1, padx=6, sticky="w")
        self.reproc_btn = ttk.Button(f, text="회의 폴더 다시 처리…", command=self.reprocess_folder)
        self.reproc_btn.grid(row=2, column=0, padx=6, pady=4, sticky="w")
        self.file_btn = ttk.Button(f, text="음성 파일 하나 처리…", command=self.process_single_file)
        self.file_btn.grid(row=2, column=1, padx=6, sticky="w")

        self.status = tk.StringVar(value="준비되었습니다.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    # ------------------------------------------------------------ 장치
    def _init_backend(self) -> bool:
        """장치 계층을 엽니다. 실패하면 원인을 화면에 보여 주고 False."""
        try:
            self.backend = default_backend()
        except Exception as e:
            self.backend = None
            self.backend_error = e
            log.exception("장치 계층을 열 수 없음")
            self._show_device_error(
                "소리 장치 기능을 시작하지 못했습니다. [장치 새로고침]을 눌러 다시 시도해 보시고, "
                "계속되면 [오류 내용 복사]를 눌러 담당자에게 보내 주십시오.",
                "장치 계층 열기", _exc_text(e))
            self.start_btn.configure(state="disabled")
            self.status.set("소리 장치를 쓸 수 없습니다(녹음 불가). 기존 녹음 처리는 됩니다.")
            return False
        self.backend_error = None
        self._hide_device_error()
        self.start_btn.configure(state="normal")
        self._load_device_lists()
        return True

    def refresh_devices(self):
        """[장치 새로고침]: 장치 계층이 없으면 만들기부터 다시 시도합니다."""
        if self.recorder and self.recorder.running:
            return
        if self.backend is None:
            if self._init_backend():
                self.status.set("소리 장치 기능을 다시 시작했습니다.")
            return
        self._load_device_lists()

    def _load_device_lists(self):
        try:
            self.mics = self.backend.list_microphones()
            self.spks = self.backend.list_speakers()
        except Exception as e:
            self.mics, self.spks = [], []
            log.exception("장치 목록 오류")
            self._fill(self.mic_cb, [], None)
            self._fill(self.spk_cb, [], None)
            self._show_device_error(
                "장치 목록을 읽지 못했습니다. '자동'으로는 그대로 녹음할 수 있습니다.",
                "장치 목록 읽기", _exc_text(e))
            return
        self._hide_device_error()
        self._fill(self.mic_cb, self.mics, self.settings.get("mic_id"))
        self._fill(self.spk_cb, self.spks, self.settings.get("speaker_id"))
        if not self.mics or not self.spks:
            self.status.set("장치 목록이 비어 있습니다. '자동'으로 녹음을 시도할 수 있습니다.")

    @staticmethod
    def _fill(cb: ttk.Combobox, devs: list[DeviceInfo], saved: str | None):
        """맨 위는 '자동', 그 아래 장치 목록. 저장된 장치(id)가 목록에 있으면 그것을, 없으면 '자동'.

        예전 설정 호환: mic_id/speaker_id 가 없거나 None 이면 '자동'(예전에도 '기본 장치 따라가기').
        """
        names = [AUTO_DEVICE] + [("(현재 기본) " if d.is_default else "") + d.name for d in devs]
        cb.configure(values=names)
        idx = next((i + 1 for i, d in enumerate(devs) if saved and d.id == saved), 0)
        cb.current(idx)

    def _selected(self, cb: ttk.Combobox, devs: list[DeviceInfo]) -> str | None:
        """고른 장치 id. '자동'이면 None(녹음 스레드가 윈도우 기본 장치를 직접 열고, 바뀌면 따라감).
        목록의 장치를 고르면 그 장치로 고정합니다(현재 기본 장치를 골라도 고정, 감사 N3)."""
        i = cb.current()
        if i <= 0 or i - 1 >= len(devs):
            return None
        return devs[i - 1].id

    def check_devices(self):
        """[장치 점검]: 녹음하지 않고 고른 마이크·스피커를 1초씩 열어 소리 크기와 연 방법을 보여 줍니다."""
        if self.recorder and self.recorder.running:
            messagebox.showinfo("장치 점검", "녹음 중에는 점검할 수 없습니다. 녹음 막대를 보십시오.")
            return
        if self.backend is None and not self._init_backend():
            return
        mic_id = self._selected(self.mic_cb, self.mics)
        spk_id = self._selected(self.spk_cb, self.spks)
        self.check_btn.configure(state="disabled")
        self.status.set("장치 점검 중… (2~3초, 말하거나 소리를 틀어 두면 크기가 보입니다)")
        backend = self.backend

        def work():
            res = [check_device(backend, "mic", mic_id, 1.0), check_device(backend, "loop", spk_id, 1.0)]
            self.q.put(("check", res))

        threading.Thread(target=work, name="device-check", daemon=True).start()

    def _check_done(self, res: list[dict]):
        self.check_btn.configure(state="normal")
        text = "\n\n".join(check_text(r) for r in res)
        log.info("장치 점검: %s", [{k: r.get(k) for k in ("kind", "ok", "device", "combo", "samplerate", "level_db")}
                                for r in res])
        if all(r["ok"] for r in res):
            self.status.set("장치 점검: 두 장치 모두 열렸습니다.")
            self.last_devices = {("mic" if r["kind"] == "mic" else "loop"): r["device"] for r in res}
            messagebox.showinfo("장치 점검", text)
        else:
            self.status.set("장치 점검: 열지 못한 장치가 있습니다. [오류 내용 복사]로 내용을 보낼 수 있습니다.")
            self._show_device_error("장치 점검: 열지 못한 장치가 있습니다. '자동'으로 바꾸거나 다른 장치를 골라 보시고, "
                                    "계속되면 [오류 내용 복사]를 눌러 담당자에게 보내 주십시오.", "장치 점검", text)
            messagebox.showwarning("장치 점검", text[:1500])

    def _show_device_error(self, msg: str, where: str, err_text: str):
        self.dev_err.set(msg)
        self._err_report = error_report(where, err_text, self.last_devices)
        self.dev_err_label.grid(row=3, column=0, columnspan=2, sticky="we", padx=6, pady=(4, 2))
        self.copy_btn.grid(row=3, column=2, padx=6, pady=(4, 2))

    def _hide_device_error(self):
        self.dev_err.set("")
        self._err_report = ""
        self.dev_err_label.grid_remove()
        self.copy_btn.grid_remove()

    def copy_error(self):
        """오류 내용(기술 정보)을 클립보드에 넣습니다."""
        text = self._err_report or error_report("(표시된 오류 없음)", "", self.last_devices)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set("오류 내용을 복사했습니다. 메일·메신저에 붙여 넣어 보내 주십시오.")

    def _check_models(self):
        missing = models.missing_models()
        if missing:
            if not models.missing_for(TRANSCRIBE_MODEL, auto_language=False):
                self.status.set("언어 판별 모델 파일이 없습니다. 회의 언어를 '영어' 나 '한국어' 로 정하면 처리할 수 있습니다.")
            else:
                self.status.set("모델 파일이 없습니다. 압축을 다시 풀어 주십시오(models 폴더 필요).")
            log.error("모델 없음: %s", missing)
        elif not paths.path_is_safe_for_native(paths.models_dir()):
            messagebox.showwarning(
                "폴더 위치", "프로그램 폴더 경로에 한글이 있어 모델을 못 읽을 수 있습니다.\n"
                "압축을 C:\\MeetingRecorder 처럼 영어로만 된 폴더에 풀어 주십시오.")

    # ------------------------------------------------------------ 녹음
    def start_recording(self):
        if self.processing:
            messagebox.showinfo("녹음", "글로 옮기는 중입니다. 끝난 뒤(또는 취소 후) 녹음해 주십시오.")
            return
        if self.backend is None and not self._init_backend():
            messagebox.showerror("녹음 시작", "소리 장치 기능을 시작하지 못했습니다.\n"
                                 "창의 [오류 내용 복사]로 내용을 복사해 담당자에게 보내 주십시오.")
            return
        mic_id = self._selected(self.mic_cb, self.mics)
        spk_id = self._selected(self.spk_cb, self.spks)
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            free_gb = shutil.disk_usage(self.out_dir).free / 1e9
            if free_gb < 1.0 and not messagebox.askyesno(
                    "저장 공간", f"남은 공간이 {free_gb:.1f}GB 입니다(1시간에 약 0.25GB). 그래도 녹음할까요?"):
                return
            self.recorder = Recorder(self.backend)
            folder = self.recorder.start(self.out_dir, mic_id, spk_id)
        except Exception as e:
            log.exception("녹음 시작 실패")
            self.recorder = None
            self._show_device_error("녹음을 시작하지 못했습니다. 장치 선택을 '자동'으로 바꾸거나 [장치 새로고침] 후 "
                                    "다시 시도해 주십시오.", "녹음 시작", _exc_text(e))
            messagebox.showerror("녹음 시작", f"녹음을 시작하지 못했습니다.\n장치 선택을 확인해 주십시오.\n\n{e}")
            return
        self.settings.update({"mic_id": mic_id, "speaker_id": spk_id})  # None = 자동
        paths.save_settings(self.settings)
        if self.backend_error is None:
            self._hide_device_error()
        sysinfo.keep_awake(True)
        self.last_folder = folder
        self.silent_since = {"mic": time.monotonic(), "loop": time.monotonic()}
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(text="❚❚ 일시정지", state="normal")
        self.stop_btn.configure(state="normal")
        for w in (self.mic_cb, self.spk_cb, self.reproc_btn, self.file_btn):
            w.configure(state="disabled")
        sr = {k: t.sr for k, t in self.recorder.tracks.items()}
        self.last_devices = self.recorder.device_names()
        log.info("녹음 시작: 표본 주파수 %s, 장치 %s, 연 방법 %s, %s", sr, self.last_devices,
                 self.recorder.device_combos(), sysinfo.system_summary())
        self.status.set(f"녹음 중… 저장 위치: {folder}")

    def toggle_pause(self):
        if not self.recorder or not self.recorder.running:
            return
        if self.recorder.paused:
            self.recorder.resume()
            self.pause_btn.configure(text="❚❚ 일시정지")
            now = time.monotonic()
            self.silent_since = {"mic": now, "loop": now}
            self.status.set(f"녹음 중… 저장 위치: {self.last_folder}")
        else:
            self.recorder.pause()
            self.pause_btn.configure(text="▶ 재개")
            self.mic_bar["value"] = self.loop_bar["value"] = 0
            self.rec_warn.set("")
            self.status.set("일시정지됨 (재개를 누르면 이어서 녹음합니다)")

    def stop_recording(self):
        if not self.recorder or not self.recorder.running:
            return
        self.pause_btn.configure(text="❚❚ 일시정지", state="disabled")
        self.stop_btn.configure(state="disabled")
        try:
            res = self.recorder.stop()
        except Exception as e:
            log.exception("녹음 중지 오류")
            messagebox.showerror("녹음 중지", f"녹음을 멈추는 중 오류가 났습니다.\n{e}")
            res = None
        sysinfo.keep_awake(False)
        self.start_btn.configure(state="normal")
        self.mic_cb.configure(state="readonly")
        self.spk_cb.configure(state="readonly")
        for w in (self.reproc_btn, self.file_btn, self.open_btn):
            w.configure(state="normal")
        self.mic_bar["value"] = self.loop_bar["value"] = 0
        self.rec_warn.set("")
        self._show_alarm({})
        if res is None:
            return
        errs = {k: v["error"] for k, v in res.stats.items() if v.get("error")}
        log.info("녹음 끝: %.0f초, 채운 무음(초) mic=%.1f loop=%.1f, 오류=%s", res.seconds,
                 res.stats["mic"]["frames_padded"] / max(1, res.stats["mic"]["samplerate"]),
                 res.stats["loop"]["frames_padded"] / max(1, res.stats["loop"]["samplerate"]), list(errs))
        msg = f"녹음이 끝났습니다({fmt_elapsed(res.seconds)})."
        if errs:
            msg += " 녹음 중 장치 문제가 있었습니다: " + ", ".join("마이크" if k == "mic" else "스피커" for k in errs)
            reconn = sum(v.get("reconnects", 0) for v in res.stats.values())
            if reconn:
                msg += f" (다시 연결 {reconn}번, 끊긴 동안은 무음)"
        self.status.set(msg)
        if self.auto_var.get():
            self._start_job(pipeline.job_for_folder(res.folder, self._lang(), self._spk_n()))

    def _tick(self):
        rec = self.recorder
        if rec and rec.running:
            self.elapsed_var.set(fmt_elapsed(rec.elapsed()))
            if rec.paused:
                self.mic_bar["value"] = self.loop_bar["value"] = 0
                self.rec_warn.set("")
            else:
                lv = rec.levels()
                now = time.monotonic()
                warns = []
                for key, bar, label in (("mic", self.mic_bar, "내 마이크"), ("loop", self.loop_bar, "상대방 소리")):
                    db = lv.get(key, -90.0)
                    bar["value"] = max(0.0, min(100.0, (db + 60.0) / 60.0 * 100.0))
                    if db > -55.0:
                        self.silent_since[key] = now
                    elif self.silent_since[key] and now - self.silent_since[key] > SILENT_WARN_S:
                        warns.append(f"{label}가 1분 넘게 조용합니다. 장치 선택을 확인해 주십시오.")
                        if key == "loop":
                            warns.append("원격(가상) PC 에서는 회의 소리가 이 PC 를 거치지 않고 사용 중인 단말에서 바로 "
                                         "나올 수 있어, 이 프로그램이 상대방 소리를 녹음하지 못할 수 있습니다.")
                if any(w.startswith("내 마이크") for w in warns):
                    warns.append("윈도우 설정 → 개인 정보 및 보안 → 마이크에서 '데스크톱 앱이 마이크에 "
                                 "액세스하도록 허용' 이 켜져 있는지도 확인해 주십시오.")
                self.rec_warn.set(" ".join(warns))
            self._show_alarm(rec.problems())
        self._drain_queue()
        self.root.after(200, self._tick)

    def _show_alarm(self, problems: dict):
        """장치 끊김 등은 창 안의 빨간 띠 + 제목 표시 + 알림음 1번으로 분명히 알립니다."""
        if problems:
            self.rec_alarm.set("⚠ " + " ".join(problems.values()))
            if not self._alarm_shown:
                self.alarm_label.grid(row=4, column=0, columnspan=4, sticky="we", padx=6, pady=4)
                self.root.title("⚠ 녹음 장치 문제 - 회의 녹음·기록")
                try:
                    self.root.bell()
                except tk.TclError:
                    pass
                self._alarm_shown = True
                log.warning("녹음 장치 문제: %s", list(problems))
        elif self._alarm_shown:
            self.alarm_label.grid_remove()
            self.rec_alarm.set("")
            self.root.title("회의 녹음·기록")
            self._alarm_shown = False

    # ------------------------------------------------------------ 처리
    def _lang(self):
        return LANG_CHOICES[max(0, self.lang_cb.current())][1]

    def _spk_n(self):
        return SPK_CHOICES[max(0, self.spk_n_cb.current())][1]

    def _mic_min_db(self) -> float | None:
        return MIC_CUT_CHOICES[max(0, self.mic_cut_cb.current())][1]

    def _meeting_terms(self) -> list[str]:
        from . import glossary

        return glossary.parse_terms(self.meeting_terms_var.get())

    def _update_glossary_note(self):
        try:
            from . import glossary

            self.glossary_var.set("용어: " + glossary.status_text())
        except Exception as e:
            log.warning("용어 파일 확인 실패: %s", type(e).__name__)
            self.glossary_var.set("용어: 확인하지 못함(용어 없이 처리)")

    def open_glossary(self):
        from . import glossary

        d = glossary.glossary_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        open_folder(d)

    def _start_job(self, job: pipeline.Job):
        model = TRANSCRIBE_MODEL
        miss = models.missing_for(model, self._lang() is None)
        if miss:
            log.error("모델 없음(%s): %s", model, miss)
            hint = ("회의 언어를 '영어' 나 '한국어' 로 정하거나, " if not models.missing_for(model, False) else "")
            messagebox.showerror("모델 없음", "필요한 모델 파일이 없어 글로 옮길 수 없습니다.\n"
                                 f"{hint}압축을 다시 풀어 주십시오.")
            return
        job.model = model
        job.mark_low_conf = bool(self.settings.get("mark_low_conf", pipeline.MARK_LOW_CONF_DEFAULT))
        job.guard = bool(self.settings.get("tail_text_guard", True))  # 설정 파일에서 false 로 끌 수 있음
        job.recheck_unsure = bool(self.settings.get("recheck_unsure", False))  # 기본 끔(R6 되돌림)
        job.extra_terms = self._meeting_terms()
        job.mic_min_db = self._mic_min_db()
        self._update_glossary_note()  # 사용자가 용어 파일을 고쳤을 수 있음
        self.settings.update({"language": self._lang(), "auto_process": bool(self.auto_var.get()),
                              "mic_min_db": job.mic_min_db})
        paths.save_settings(self.settings)
        log.info("처리 시작: 모델 %s", model)
        self.processing = True
        self.cancel_flag.clear()
        self.last_folder = job.folder
        self.cancel_btn.configure(state="normal")
        for w in (self.reproc_btn, self.file_btn, self.names_btn, self.start_btn):
            w.configure(state="disabled")
        self.prog["value"] = 0
        self.prog_var.set("시작하는 중…")
        sysinfo.keep_awake(True)
        t0 = time.monotonic()

        def progress(name, frac):
            self.q.put(("progress", name, frac, time.monotonic() - t0))

        def work():
            try:
                res = pipeline.run(job, progress, cancel=self.cancel_flag.is_set)
                self.q.put(("done", res, None))
            except InterruptedError:
                self.q.put(("done", None, "취소했습니다."))
            except MemoryError:
                log.exception("메모리 부족")
                self.q.put(("done", None, "메모리가 부족합니다. 다른 프로그램을 닫고 다시 처리해 주십시오."))
            except Exception as e:
                log.exception("처리 오류")
                self.q.put(("done", None, f"처리 중 오류가 났습니다: {type(e).__name__}: {e}"))

        threading.Thread(target=work, name="process", daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    _, name, frac, elapsed = msg
                    self.prog["value"] = frac * 100
                    eta = ""
                    if frac > 0.1:
                        remain = elapsed / frac * (1 - frac)
                        eta = f" · 남은 시간 약 {max(1, int(remain / 60 + 0.5))}분"
                    self.prog_var.set(f"{name} {frac * 100:.0f}%{eta}")
                elif msg[0] == "done":
                    self._job_done(msg[1], msg[2])
                elif msg[0] == "check":
                    self._check_done(msg[1])
        except queue.Empty:
            pass

    def _job_done(self, res, err):
        self.processing = False
        sysinfo.keep_awake(False)
        self.cancel_btn.configure(state="disabled")
        for w in (self.reproc_btn, self.file_btn, self.start_btn, self.open_btn):
            w.configure(state="normal")
        if err:
            self.prog_var.set(err)
            self.status.set(err)
            if not err.startswith("취소"):
                messagebox.showerror("글로 옮기기", err)
            return
        self.prog["value"] = 100
        self.names_btn.configure(state="normal")
        n = len(res.speakers)
        self.prog_var.set(pipeline.done_summary(res))
        self.status.set(f"결과: {self.last_folder}")
        if n and messagebox.askyesno("참석자 이름", f"상대방을 {n}명으로 구분했습니다.\n지금 참석자 이름을 정할까요?"):
            self.edit_names()

    def cancel_processing(self):
        if self.processing:
            self.cancel_flag.set()
            self.prog_var.set("취소하는 중… (지금 하던 부분이 끝나면 멈춥니다)")

    def reprocess_folder(self):
        d = filedialog.askdirectory(title="다시 처리할 회의 폴더를 고르십시오", initialdir=str(self.out_dir))
        if not d:
            return
        folder = Path(d)
        if not ((folder / MIC_FILE).is_file() or (folder / LOOP_FILE).is_file()):
            messagebox.showerror("다시 처리", f"이 폴더에 {MIC_FILE} 또는 {LOOP_FILE} 가 없습니다.")
            return
        self._start_job(pipeline.job_for_folder(folder, self._lang(), self._spk_n()))

    def process_single_file(self):
        f = filedialog.askopenfilename(
            title="처리할 음성 파일을 고르십시오(모든 발언자를 '참석자'로 구분합니다)",
            filetypes=[("음성 파일", "*.wav *.flac *.mp3 *.ogg"), ("모든 파일", "*.*")])
        if not f:
            return
        try:
            folder = new_meeting_folder(self.out_dir, datetime.now())
        except OSError as e:
            messagebox.showerror("음성 파일 처리", f"결과 폴더를 만들 수 없습니다.\n{e}")
            return
        job = pipeline.Job(folder, None, Path(f), self._lang(), self._spk_n())
        self._start_job(job)

    # ------------------------------------------------------------ 결과
    def change_out_dir(self):
        d = filedialog.askdirectory(title="녹음·기록을 저장할 폴더", initialdir=str(self.out_dir))
        if d:
            self.out_dir = Path(d)
            self.out_var.set(f"저장 폴더: {self.out_dir}")
            self.settings["output_dir"] = str(self.out_dir)
            paths.save_settings(self.settings)

    def open_result(self):
        open_folder(self.last_folder or self.out_dir)

    def edit_names(self):
        folder = self.last_folder
        if not folder or not (folder / output.RESULT_JSON).is_file():
            messagebox.showinfo("참석자 이름", "먼저 글로 옮기기를 끝내 주십시오.")
            return
        try:
            utts, _, names = output.load_result_json(folder)
        except Exception as e:
            messagebox.showerror("참석자 이름", f"결과를 읽지 못했습니다.\n{e}")
            return
        NamesDialog(self.root, folder, utts, names, self.status)

    def on_close(self):
        if self.recorder and self.recorder.running:
            if not messagebox.askyesno("끝내기", "녹음 중입니다. 녹음을 멈추고 끝낼까요?\n(그때까지 녹음은 저장됩니다)"):
                return
            try:
                self.recorder.stop()
            except Exception:
                log.exception("끝낼 때 녹음 중지 오류")
        if self.processing:
            if not messagebox.askyesno("끝내기", "글로 옮기는 중입니다. 취소하고 끝낼까요?\n(녹음 파일은 남고, 나중에 '회의 폴더 다시 처리'로 할 수 있습니다)"):
                return
            self.cancel_flag.set()
        sysinfo.keep_awake(False)
        self.root.destroy()


class NamesDialog:
    MAX_BODY_HEIGHT = 480

    def __init__(self, parent, folder: Path, utts, names: dict, status: tk.StringVar):
        self.folder = folder
        self.status = status
        self.top = tk.Toplevel(parent)
        self.top.title("참석자 이름 정하기")
        self.top.transient(parent)
        ttk.Label(self.top, text="대표 발언을 보고 이름을 적어 주십시오. 비워 두면 '참석자 N' 으로 남습니다.",
                  wraplength=560).pack(anchor="w", padx=10, pady=6)
        container = ttk.Frame(self.top)
        container.pack(fill="both", expand=True, padx=10)
        self.canvas = tk.Canvas(container, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self.canvas)
        self._body_win = self.canvas.create_window((0, 0), window=body, anchor="nw")
        self.entries: dict[str, tk.StringVar] = {}
        keys = ([merge.ME] if any(u.speaker == merge.ME for u in utts) else []) + merge.speakers_in(utts)
        row = 0
        for key in keys:
            default = output.display_name(key, {})
            ttk.Label(body, text=default, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="nw", pady=4)
            var = tk.StringVar(value=names.get(key, ""))
            ttk.Entry(body, textvariable=var, width=24).grid(row=row, column=1, sticky="nw", padx=6, pady=4)
            self.entries[key] = var
            if key != merge.ME:
                reps = merge.representative(utts, key, 2)
                text = "\n".join(f"[{output.fmt_time(u.start)}] {u.text}" for u in reps)
                ttk.Label(body, text=text, wraplength=380, foreground="#444").grid(
                    row=row, column=2, sticky="w", pady=4)
            row += 1

        def _sync_scroll(_event=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        body.bind("<Configure>", _sync_scroll)
        body.update_idletasks()
        req_w = max(body.winfo_reqwidth(), 520)
        req_h = min(body.winfo_reqheight(), self.MAX_BODY_HEIGHT)
        self.canvas.configure(width=req_w, height=req_h, scrollregion=self.canvas.bbox("all"))

        def _on_wheel(event):
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")
            elif event.delta:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.top.bind("<MouseWheel>", _on_wheel)
        self.top.bind("<Button-4>", _on_wheel)
        self.top.bind("<Button-5>", _on_wheel)

        btns = ttk.Frame(self.top)
        btns.pack(fill="x", padx=10, pady=8)
        ttk.Button(btns, text="저장", command=self.save).pack(side="right", padx=4)
        ttk.Button(btns, text="닫기", command=self.top.destroy).pack(side="right")

    def save(self):
        names = {k: v.get() for k, v in self.entries.items()}
        try:
            output.apply_names(self.folder, names)
        except Exception as e:
            messagebox.showerror("참석자 이름", f"저장하지 못했습니다.\n{e}", parent=self.top)
            return
        self.status.set("참석자 이름을 반영해 기록.md 와 Copilot용.txt 를 다시 썼습니다.")
        self.top.destroy()


def main() -> int:
    sysinfo.setup_logging()
    log.info("시작: 버전 %s", __import__("mrec").__version__)
    if os.name == "nt":
        try:  # 고해상도 화면에서 글자가 흐리지 않게
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
