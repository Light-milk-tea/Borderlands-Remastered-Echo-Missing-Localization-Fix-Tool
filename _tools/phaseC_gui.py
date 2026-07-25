#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 C 空壳工具 — GUI

  python _tools/phaseC_gui.py
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

def _app_root() -> Path:
    """Project/share-pack root; next to .exe when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


sys.path.insert(0, str(Path(__file__).resolve().parent))
from phaseC_pipeline import GamePaths, check_game, run_pipeline

ROOT = _app_root()
DEFAULT_HINT = r"C:\Program Files (x86)\Steam\steamapps\common\BorderlandsGOTYEnhanced"


class PhaseCGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("无主之地重制版 Echo 汉化丢失修复工具")
        self.geometry("760x560")
        self.minsize(640, 480)
        self._log_q: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._result: tuple[bool, str] | None = None
        self._build()
        self.after(100, self._drain_log)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        ttk.Label(
            frm,
            text="无主之地重制版 Echo 汉化丢失修复工具",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            frm,
            text="Borderlands Remastered Echo Missing Localization Fix Tool",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W)

        ttk.Label(
            frm,
            text="请先安装天邈汉化，再运行本工具。会改 CookedPC 下 DLC LOC/VO，并自动备份。",
            wraplength=720,
        ).pack(anchor=tk.W, pady=(0, 8))

        path_row = ttk.Frame(frm)
        path_row.pack(fill=tk.X)
        ttk.Label(path_row, text="游戏路径:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self._guess_path())
        self.path_entry = ttk.Entry(path_row, textvariable=self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(path_row, text="浏览…", command=self._browse).pack(side=tk.LEFT)

        opt_row = ttk.Frame(frm)
        opt_row.pack(fill=tk.X, pady=4)
        self.boost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt_row,
            text="同时加强天邈 .int 键名（推荐）",
            variable=self.boost_var,
        ).pack(side=tk.LEFT)

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill=tk.X, pady=4)
        self.btn_check = ttk.Button(btn_row, text="仅检查路径", command=self._on_check)
        self.btn_check.pack(side=tk.LEFT)
        self.btn_run = ttk.Button(btn_row, text="一键修复", command=self._on_run)
        self.btn_run.pack(side=tk.LEFT, padx=8)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(btn_row, textvariable=self.status_var).pack(side=tk.LEFT, padx=8)

        ttk.Label(frm, text="日志:").pack(anchor=tk.W)
        log_frm = ttk.Frame(frm)
        log_frm.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frm, height=20, wrap=tk.WORD, font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frm, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tip = (
            "步骤: 1) LOC 空壳  2) 洗 Bulk 尾  3) VO 去 LocalizedSubtitles  4) 加强 .int\n"
            "重装游戏后：先装天邈 → 完全退出游戏 → 再点「一键修复」。勿用 Steam 验证文件。"
        )
        ttk.Label(frm, text=tip, foreground="#444", wraplength=720).pack(
            anchor=tk.W, pady=(6, 0)
        )

    def _guess_path(self) -> str:
        candidates = [
            Path(r"C:\downloadapps\sssteam\steamapps\common\BorderlandsGOTYEnhanced"),
            Path(r"C:\Program Files (x86)\Steam\steamapps\common\BorderlandsGOTYEnhanced"),
            Path(r"D:\Steam\steamapps\common\BorderlandsGOTYEnhanced"),
        ]
        for c in candidates:
            if (c / "WillowGame" / "CookedPC").is_dir() or (c / "CookedPC").is_dir():
                return str(c)
        return DEFAULT_HINT

    def _browse(self) -> None:
        d = filedialog.askdirectory(title="选择 BorderlandsGOTYEnhanced 或 WillowGame")
        if d:
            self.path_var.set(d)

    def _append(self, msg: str) -> None:
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def _drain_log(self) -> None:
        try:
            while True:
                self._append(self._log_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain_log)

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.btn_check.configure(state=state)
        self.btn_run.configure(state=state)
        self.path_entry.configure(state=state)

    def _on_check(self) -> None:
        self.log.delete("1.0", tk.END)
        try:
            paths = GamePaths.resolve(self.path_var.get().strip())
            check_game(paths, log=self._append)
            self.status_var.set("检查通过")
            messagebox.showinfo("检查", "路径有效，可以一键修复。")
        except Exception as ex:
            self.status_var.set("检查失败")
            self._append(str(ex))
            messagebox.showerror("检查失败", str(ex))

    def _on_run(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先填写游戏路径。")
            return
        if not messagebox.askyesno(
            "确认",
            "将修改游戏 CookedPC 中的 DLC LOC/VO（并备份）。\n"
            "请确认：\n"
            "1. 已安装天邈汉化\n"
            "2. 游戏已完全退出\n\n"
            "是否继续？",
        ):
            return
        self.log.delete("1.0", tk.END)
        self._set_busy(True)
        self.status_var.set("运行中…")
        boost = bool(self.boost_var.get())

        self._result = None

        def work() -> None:
            def log(msg: str) -> None:
                self._log_q.put(msg)

            try:
                r = run_pipeline(
                    path,
                    boost_int=boost,
                    backup_parent=ROOT / "_knoxx_echo_backup" / "phaseC_gui",
                    log=log,
                )
                self._result = (r.ok, r.message)
            except Exception as ex:
                self._result = (False, str(ex))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self.after(200, self._poll_done)

    def _poll_done(self) -> None:
        if self._worker and self._worker.is_alive():
            self.after(200, self._poll_done)
            return
        self._set_busy(False)
        if self._result is None:
            self.status_var.set("结束")
            return
        ok, msg = self._result
        self.status_var.set("完成" if ok else "失败")
        if ok:
            messagebox.showinfo("完成", msg)
        else:
            messagebox.showerror("失败", msg)


def main() -> int:
    app = PhaseCGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
