"""화면(tkinter 한 창)."""
from __future__ import annotations

import logging
import os
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
from .devices import DeviceInfo, default_backend
from .recorder import LOOP_FILE, MIC_FILE, Recorder, new_meeting_folder

log = logging.getLogger("mrec")

LANG_CHOICES = [("자동(영어/한국어)", None), ("영어", "en"), ("한국어", "ko")]
SPK_CHOICES = [("자동", None)] + [(f"{n}명", n) for n in range(1, 9)]
SILENT_WARN_S = 60  # 이 시간 동안 소리가 전혀 없으면 안내


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
        self.mic_cb = ttk.Combobox(f, state="readonly", width=48)
        self.mic_cb.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(f, text="상대방 소리(스피커)").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.spk_cb = ttk.Combobox(f, state="readonly", width=48)
        self.spk_cb.grid(row=1, column=1, sticky="we", padx=6)
        ttk.Button(f, text="장치 새로고침", command=self.refresh_devices).grid(row=0, column=2, rowspan=2, padx=6)
        f.columnconfigure(1, weight=1)

        f = ttk.LabelFrame(self.root, text="2. 녹음")
        f.pack(fill="x", **pad)
        self.start_btn = ttk.Button(f, text="● 녹음 시작", command=self.start_recording)
        self.start_btn.grid(row=0, column=0, padx=6, pady=6)
        self.stop_btn = ttk.Button(f, text="■ 중지", command=self.stop_recording, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=6)
        self.elapsed_var = tk.StringVar(value="00:00:00")
        ttk.Label(f, textvariable=self.elapsed_var, font=("Segoe UI", 16)).grid(row=0, column=2, padx=12)
        ttk.Label(f, text="나(마이크)").grid(row=1, column=0, sticky="w", padx=6)
        self.mic_bar = ttk.Progressbar(f, maximum=100, length=300)
        self.mic_bar.grid(row=1, column=1, columnspan=2, sticky="we", padx=6, pady=2)
        ttk.Label(f, text="상대방(스피커)").grid(row=2, column=0, sticky="w", padx=6)
        self.loop_bar = ttk.Progressbar(f, maximum=100, length=300)
        self.loop_bar.grid(row=2, column=1, columnspan=2, sticky="we", padx=6, pady=2)
        self.rec_warn = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.rec_warn, foreground="#b35c00", wraplength=520).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=6)
        f.columnconfigure(2, weight=1)

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
        self.auto_var = tk.BooleanVar(value=self.settings.get("auto_process", True))
        ttk.Checkbutton(f, text="녹음을 멈추면 바로 글로 옮기기", variable=self.auto_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        self.prog = ttk.Progressbar(f, maximum=100, length=300)
        self.prog.grid(row=3, column=0, columnspan=2, sticky="we", padx=6, pady=4)
        self.cancel_btn = ttk.Button(f, text="처리 취소", command=self.cancel_processing, state="disabled")
        self.cancel_btn.grid(row=3, column=2, padx=6)
        self.prog_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.prog_var).grid(row=4, column=0, columnspan=3, sticky="w", padx=6)
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
    def _init_backend(self):
        try:
            self.backend = default_backend()
        except Exception as e:
            self.backend_error = str(e)
            log.warning("장치 계층을 열 수 없음: %s", e)
            self.start_btn.configure(state="disabled")
            self.status.set("소리 장치를 쓸 수 없습니다. 녹음은 안 되고, 기존 녹음 처리만 됩니다.")
            return
        self.refresh_devices()

    def refresh_devices(self):
        if not self.backend:
            return
        try:
            self.mics = self.backend.list_microphones()
            self.spks = self.backend.list_speakers()
        except Exception as e:
            log.warning("장치 목록 오류: %s", e)
            messagebox.showerror("장치", f"장치 목록을 읽지 못했습니다.\n{e}")
            return
        self._fill(self.mic_cb, self.mics, self.settings.get("mic_id"))
        self._fill(self.spk_cb, self.spks, self.settings.get("speaker_id"))

    @staticmethod
    def _fill(cb: ttk.Combobox, devs: list[DeviceInfo], saved: str | None):
        names = [("(기본) " if d.is_default else "") + d.name for d in devs]
        cb.configure(values=names)
        idx = next((i for i, d in enumerate(devs) if d.id == saved), None)
        if idx is None:
            idx = next((i for i, d in enumerate(devs) if d.is_default), 0 if devs else None)
        if idx is not None:
            cb.current(idx)
        else:
            cb.set("")

    def _selected(self, cb: ttk.Combobox, devs: list[DeviceInfo]) -> str | None:
        i = cb.current()
        return devs[i].id if 0 <= i < len(devs) else None

    def _check_models(self):
        missing = models.missing_models()
        if missing:
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
            messagebox.showerror("녹음 시작", f"녹음을 시작하지 못했습니다.\n장치 선택을 확인해 주십시오.\n\n{e}")
            return
        self.settings.update({"mic_id": mic_id, "speaker_id": spk_id})
        paths.save_settings(self.settings)
        sysinfo.keep_awake(True)
        self.last_folder = folder
        self.silent_since = {"mic": time.monotonic(), "loop": time.monotonic()}
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        for w in (self.mic_cb, self.spk_cb, self.reproc_btn, self.file_btn):
            w.configure(state="disabled")
        sr = {k: t.sr for k, t in self.recorder.tracks.items()}
        log.info("녹음 시작: 표본 주파수 %s", sr)
        self.status.set(f"녹음 중… 저장 위치: {folder}")

    def stop_recording(self):
        if not self.recorder or not self.recorder.running:
            return
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
        if res is None:
            return
        errs = {k: v["error"] for k, v in res.stats.items() if v.get("error")}
        log.info("녹음 끝: %.0f초, 채운 무음(초) mic=%.1f loop=%.1f, 오류=%s", res.seconds,
                 res.stats["mic"]["frames_padded"] / max(1, res.stats["mic"]["samplerate"]),
                 res.stats["loop"]["frames_padded"] / max(1, res.stats["loop"]["samplerate"]), list(errs))
        msg = f"녹음이 끝났습니다({fmt_elapsed(res.seconds)})."
        if errs:
            msg += " 일부 장치에 오류가 있었습니다: " + ", ".join("마이크" if k == "mic" else "스피커" for k in errs)
        self.status.set(msg)
        if self.auto_var.get():
            self._start_job(pipeline.job_for_folder(res.folder, self._lang(), self._spk_n()))

    def _tick(self):
        rec = self.recorder
        if rec and rec.running:
            self.elapsed_var.set(fmt_elapsed(rec.elapsed()))
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
            errs = rec.errors()
            for k in errs:
                warns.append(("마이크" if k == "mic" else "스피커") + " 장치 오류로 이 트랙은 무음으로 채우고 있습니다.")
            self.rec_warn.set(" ".join(warns))
        self._drain_queue()
        self.root.after(200, self._tick)

    # ------------------------------------------------------------ 처리
    def _lang(self):
        return LANG_CHOICES[max(0, self.lang_cb.current())][1]

    def _spk_n(self):
        return SPK_CHOICES[max(0, self.spk_n_cb.current())][1]

    def _start_job(self, job: pipeline.Job):
        if models.missing_models():
            messagebox.showerror("모델 없음", "모델 파일이 없어 글로 옮길 수 없습니다.\n압축을 다시 풀어 주십시오.")
            return
        self.settings.update({"language": self._lang(), "auto_process": bool(self.auto_var.get())})
        paths.save_settings(self.settings)
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
        self.prog_var.set(f"끝났습니다. 발언 {res.utterances}개, 상대방 {n}명으로 구분했습니다.")
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
    def __init__(self, parent, folder: Path, utts, names: dict, status: tk.StringVar):
        self.folder = folder
        self.status = status
        self.top = tk.Toplevel(parent)
        self.top.title("참석자 이름 정하기")
        self.top.transient(parent)
        ttk.Label(self.top, text="대표 발언을 보고 이름을 적어 주십시오. 비워 두면 '참석자 N' 으로 남습니다.",
                  wraplength=560).pack(anchor="w", padx=10, pady=6)
        body = ttk.Frame(self.top)
        body.pack(fill="both", expand=True, padx=10)
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
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0
