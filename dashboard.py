"""HaruBot 컨트롤 패널 — Windows 전용 GUI 대시보드.

기능
----
- 봇 시작/중지 버튼 (큰 1차 컨트롤)
- 실시간 로그 보기 (색 코딩: INFO/WARN/ERROR/DEBUG)
- .env 환경변수 편집 (저장/다시읽기/.env.example 로 초기화)
- Windows 부팅 시 자동 시작 토글 (HKCU\\...\\Run 레지스트리)
- 크래시 시 자동 재시작 토글 (5초 지연, 정상 종료는 재시작 안 함)
- 로그 저장/지우기, 자동 스크롤
- 단축키: F5(시작) / F6(중지) / Ctrl+L(로그 지우기)

실행: `python dashboard.py`
빌드: `scripts/build_dashboard.bat` → `dist/HaruBotDashboard.exe`
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

APP_NAME = "HaruBotDashboard"


def get_project_dir() -> Path:
    """PyInstaller frozen 이든 .py 든 안전하게 프로젝트 루트를 찾는다."""
    if getattr(sys, "frozen", False):
        d = Path(sys.executable).resolve().parent
        # 보통 dist/HaruBotDashboard.exe 또는 프로젝트 루트에 둠
        for cand in (d, d.parent, d.parent.parent):
            if (cand / "bot.py").exists():
                return cand
        return d
    return Path(__file__).resolve().parent


PROJECT_DIR = get_project_dir()
VENV_PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
BOT_SCRIPT = PROJECT_DIR / "bot.py"
ENV_PATH = PROJECT_DIR / ".env"
ENV_EXAMPLE_PATH = PROJECT_DIR / ".env.example"
SETTINGS_PATH = PROJECT_DIR / "dashboard_settings.json"
LOG_DIR = PROJECT_DIR / "logs"


class HaruBotDashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("HaruBot 컨트롤 패널")
        root.geometry("960x680")
        root.minsize(720, 480)

        LOG_DIR.mkdir(exist_ok=True)

        # 상태
        self.bot_process: subprocess.Popen | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.log_lines = 0
        self.manual_stop = False  # 사용자가 명시적으로 중지했나? (자동재시작 분기용)
        self.settings = self._load_settings()
        self.auto_restart = tk.BooleanVar(value=self.settings.get("auto_restart", True))
        self.auto_start_with_windows = tk.BooleanVar(value=self._check_autostart())
        self.autoscroll = tk.BooleanVar(value=True)

        # 단축키
        root.bind("<F5>", lambda e: self.start_bot())
        root.bind("<F6>", lambda e: self.stop_bot())
        root.bind("<Control-l>", lambda e: self.clear_logs())

        # UI
        self._build_ui()
        self._update_status()

        # 주기 작업
        root.after(120, self._drain_log_queue)
        root.after(1000, self._check_bot_process)

        # 종료 훅
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────── 설정 ─────────────────────
    def _load_settings(self) -> dict:
        if SETTINGS_PATH.exists():
            try:
                return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"auto_restart": True}

    def _save_settings(self) -> None:
        self.settings["auto_restart"] = self.auto_restart.get()
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self.settings, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ─────────────────────────────────────── UI ───────────────────────
    def _build_ui(self) -> None:
        # 상단 상태바
        top = tk.Frame(self.root, bg="#2b2b2b", height=64)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        self.status_dot = tk.Label(
            top, text="●", font=("Segoe UI", 28), fg="#666", bg="#2b2b2b"
        )
        self.status_dot.pack(side=tk.LEFT, padx=(20, 5))

        self.status_text = tk.Label(
            top, text="중지됨", font=("Segoe UI", 14, "bold"),
            fg="white", bg="#2b2b2b",
        )
        self.status_text.pack(side=tk.LEFT)

        self.start_btn = tk.Button(
            top, text="▶  시작 (F5)", font=("Segoe UI", 11, "bold"),
            bg="#4caf50", fg="white", width=14, height=2, bd=0,
            activebackground="#45a049", cursor="hand2",
            command=self.start_bot,
        )
        self.start_btn.pack(side=tk.RIGHT, padx=(0, 20), pady=10)

        self.stop_btn = tk.Button(
            top, text="■  중지 (F6)", font=("Segoe UI", 11, "bold"),
            bg="#f44336", fg="white", width=14, height=2, bd=0,
            activebackground="#d32f2f", cursor="hand2",
            state=tk.DISABLED, command=self.stop_bot,
        )
        self.stop_btn.pack(side=tk.RIGHT, padx=(0, 8))

        # 탭
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._build_log_tab(notebook)
        self._build_env_tab(notebook)
        self._build_host_tab(notebook)
        self._build_about_tab(notebook)

    def _build_log_tab(self, nb: ttk.Notebook) -> None:
        tab = tk.Frame(nb)
        nb.add(tab, text="📜  로그")

        self.log_text = scrolledtext.ScrolledText(
            tab, wrap=tk.WORD, font=("Consolas", 9),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text.tag_config("ERROR", foreground="#ff6e6e")
        self.log_text.tag_config("WARN", foreground="#ffcc66")
        self.log_text.tag_config("INFO", foreground="#88c0d0")
        self.log_text.tag_config("DEBUG", foreground="#888")
        self.log_text.tag_config("SYS", foreground="#a3be8c", font=("Consolas", 9, "bold"))

        bar = tk.Frame(tab)
        bar.pack(fill=tk.X, padx=5, pady=(0, 5))
        tk.Button(bar, text="지우기 (Ctrl+L)", command=self.clear_logs).pack(side=tk.LEFT)
        tk.Button(bar, text="저장", command=self.save_logs).pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(bar, text="자동 스크롤", variable=self.autoscroll).pack(side=tk.LEFT, padx=10)
        self.line_count_label = tk.Label(bar, text="0줄", fg="#666")
        self.line_count_label.pack(side=tk.RIGHT)

    def _build_env_tab(self, nb: ttk.Notebook) -> None:
        tab = tk.Frame(nb)
        nb.add(tab, text="🔑  .env 편집")

        tk.Label(
            tab, anchor=tk.W, justify=tk.LEFT, padx=10,
            text="환경 변수 편집. 저장 후 봇을 재시작해야 반영됩니다.",
        ).pack(fill=tk.X, pady=(10, 5))

        self.env_text = scrolledtext.ScrolledText(
            tab, wrap=tk.NONE, font=("Consolas", 10),
            bg="#fafafa",
        )
        self.env_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self._reload_env()

        bar = tk.Frame(tab)
        bar.pack(fill=tk.X, padx=10, pady=(0, 10))
        tk.Button(
            bar, text="💾  저장", bg="#2196f3", fg="white",
            font=("Segoe UI", 10, "bold"), bd=0, padx=14,
            command=self.save_env,
        ).pack(side=tk.LEFT)
        tk.Button(bar, text="다시 읽기", command=self._reload_env).pack(side=tk.LEFT, padx=8)
        tk.Button(bar, text=".env.example 로 채우기", command=self.reset_env).pack(side=tk.LEFT)
        tk.Label(bar, text=f"경로: {ENV_PATH}", fg="#666").pack(side=tk.RIGHT)

    def _reload_env(self) -> None:
        self.env_text.delete(1.0, tk.END)
        if ENV_PATH.exists():
            try:
                self.env_text.insert(1.0, ENV_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                self.env_text.insert(1.0, f"# 읽기 실패: {e}\n")
        elif ENV_EXAMPLE_PATH.exists():
            self.env_text.insert(
                1.0,
                "# .env 가 없습니다.\n"
                "# 아래는 .env.example 의 내용입니다 — 토큰 등을 채운 뒤 저장하세요.\n\n",
            )
            self.env_text.insert(tk.END, ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
        else:
            self.env_text.insert(1.0, "# .env / .env.example 둘 다 없습니다.\n")

    def save_env(self) -> None:
        content = self.env_text.get(1.0, tk.END).rstrip() + "\n"
        try:
            ENV_PATH.write_text(content, encoding="utf-8")
            messagebox.showinfo("저장 완료", ".env 파일을 저장했어요.\n봇 재시작 시 반영됩니다.")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    def reset_env(self) -> None:
        if not messagebox.askyesno("초기화", "현재 편집 내용을 .env.example 로 덮어쓸까요?"):
            return
        if ENV_EXAMPLE_PATH.exists():
            self.env_text.delete(1.0, tk.END)
            self.env_text.insert(1.0, ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))

    def _build_host_tab(self, nb: ttk.Notebook) -> None:
        tab = tk.Frame(nb)
        nb.add(tab, text="🖥️  호스팅")

        opts = tk.LabelFrame(tab, text="동작 옵션", padx=12, pady=12)
        opts.pack(fill=tk.X, padx=15, pady=15)
        tk.Checkbutton(
            opts, text="크래시 시 자동 재시작 (5초 후)",
            variable=self.auto_restart, command=self._save_settings,
        ).pack(anchor=tk.W)
        tk.Checkbutton(
            opts, text="Windows 시작 시 대시보드 자동 실행",
            variable=self.auto_start_with_windows, command=self._toggle_autostart,
        ).pack(anchor=tk.W, pady=(4, 0))

        info = tk.LabelFrame(tab, text="시스템 정보", padx=12, pady=12)
        info.pack(fill=tk.X, padx=15, pady=5)
        for label, value in (
            ("프로젝트 경로", PROJECT_DIR),
            ("Python", VENV_PYTHON),
            ("봇 스크립트", BOT_SCRIPT),
            (".env", ENV_PATH),
            ("로그 폴더", LOG_DIR),
        ):
            row = tk.Frame(info)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"{label}:", width=14, anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=str(value), anchor=tk.W, fg="#444").pack(side=tk.LEFT, fill=tk.X, expand=True)

        warn = tk.LabelFrame(
            tab, text="⚠️  권장 Windows 설정", padx=12, pady=12, fg="#d97706",
        )
        warn.pack(fill=tk.X, padx=15, pady=5)
        tk.Label(
            warn, anchor=tk.W, justify=tk.LEFT,
            text=(
                "•  설정 → 시스템 → 전원 → 화면/절전 → 절전 모드 전환: 안 함\n"
                "•  노트북: 덮개 닫을 때 → 아무 작업도 안 함\n"
                "•  인터넷 끊김 시 봇이 자동 RESUME 합니다 (걱정 X)"
            ),
        ).pack(fill=tk.X)

    def _check_autostart(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _toggle_autostart(self) -> None:
        # 어떤 명령으로 자동 시작할지 결정
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}"'
        else:
            # 개발 시: pythonw 로 콘솔 없이 실행
            py = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
            if not py.exists():
                py = Path(sys.executable)
            cmd = f'"{py}" "{Path(__file__).resolve()}"'
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            ) as key:
                if self.auto_start_with_windows.get():
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as e:
            messagebox.showerror("레지스트리 오류", str(e))
            self.auto_start_with_windows.set(not self.auto_start_with_windows.get())

    def _build_about_tab(self, nb: ttk.Notebook) -> None:
        tab = tk.Frame(nb)
        nb.add(tab, text="ℹ️  정보")

        tk.Label(tab, text="HaruBot 컨트롤 패널", font=("Segoe UI", 20, "bold")).pack(pady=(28, 5))
        tk.Label(tab, text="단일 길드 Discord 봇 매니저", fg="#666").pack()
        tk.Label(
            tab, text=f"v1.0  ·  GitHub: 9uja/Haru",
            font=("Consolas", 9), fg="#666",
        ).pack(pady=4)

        body = tk.Label(
            tab, justify=tk.LEFT, anchor=tk.W,
            text=(
                "\n주요 기능\n"
                "  •  봇 시작/중지\n"
                "  •  실시간 로그 (색 코딩)\n"
                "  •  .env 환경 변수 편집\n"
                "  •  크래시 시 자동 재시작\n"
                "  •  Windows 부팅 시 자동 실행\n\n"
                "단축키\n"
                "  •  F5  : 시작\n"
                "  •  F6  : 중지\n"
                "  •  Ctrl+L  : 로그 지우기\n"
            ),
        )
        body.pack(padx=40, pady=10, anchor=tk.W)

    # ─────────────────────────────────────── 봇 제어 ──────────────────
    def start_bot(self) -> None:
        if self.bot_process and self.bot_process.poll() is None:
            return
        # 사전 검증
        if not VENV_PYTHON.exists():
            messagebox.showerror(
                "실행 실패",
                f".venv 의 Python 을 못 찾았어요:\n{VENV_PYTHON}\n\n"
                "터미널에서 다음을 한 번 실행하세요:\n\n"
                "  python -m venv .venv\n"
                "  .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            )
            return
        if not BOT_SCRIPT.exists():
            messagebox.showerror("실행 실패", f"bot.py 를 못 찾았어요:\n{BOT_SCRIPT}")
            return
        if not ENV_PATH.exists():
            messagebox.showerror(
                "실행 실패", ".env 파일이 없어요. '.env 편집' 탭에서 작성하고 저장하세요."
            )
            return

        try:
            # CREATE_NO_WINDOW: 자식 콘솔 안 띄우기. text/encoding 으로 한글 안전 처리.
            self.bot_process = subprocess.Popen(
                [str(VENV_PYTHON), "-u", str(BOT_SCRIPT)],
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            messagebox.showerror("실행 실패", str(e))
            return

        self.manual_stop = False
        self._log(f"━━━━━ 봇 시작 [PID {self.bot_process.pid}] ━━━━━\n", "SYS")
        threading.Thread(target=self._stream_logs, daemon=True).start()
        self._update_status()

    def stop_bot(self) -> None:
        if not self.bot_process or self.bot_process.poll() is not None:
            return
        self.manual_stop = True
        self._log("━━━━━ 봇 중지 요청 ━━━━━\n", "SYS")
        try:
            self.bot_process.terminate()
            try:
                self.bot_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.bot_process.kill()
                self.bot_process.wait()
        except Exception as e:
            self._log(f"종료 실패: {e}\n", "ERROR")
        self._update_status()

    # ─────────────────────────────────────── 로그 ─────────────────────
    def _stream_logs(self) -> None:
        try:
            assert self.bot_process is not None and self.bot_process.stdout is not None
            for line in self.bot_process.stdout:
                self.log_queue.put(line)
        except Exception as e:
            self.log_queue.put(f"[로그 스트림 오류] {e}\n")
        self.log_queue.put("━━━━━ 봇 종료 ━━━━━\n")

    def _drain_log_queue(self) -> None:
        try:
            for _ in range(200):
                line = self.log_queue.get_nowait()
                tag = "INFO"
                if "[ERROR]" in line:
                    tag = "ERROR"
                elif "[WARNING]" in line or "[WARN]" in line:
                    tag = "WARN"
                elif "[DEBUG]" in line:
                    tag = "DEBUG"
                elif line.startswith("━"):
                    tag = "SYS"
                self._log(line, tag)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log_queue)

    def _log(self, text: str, tag: str = "INFO") -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text, tag)
        self.log_lines += text.count("\n") or 1
        # 너무 길어지면 앞부분 잘라 메모리/렌더 부하 보호
        if self.log_lines > 10_000:
            try:
                idx = self.log_text.index(f"{self.log_lines - 8000}.0")
                self.log_text.delete("1.0", idx)
            except Exception:
                self.log_text.delete("1.0", "2000.0")
            self.log_lines = 8000
        if self.autoscroll.get():
            self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.line_count_label.config(text=f"{self.log_lines:,}줄")

    def clear_logs(self) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.log_lines = 0
        self.line_count_label.config(text="0줄")

    def save_logs(self) -> None:
        default = f"harubot-{datetime.now():%Y%m%d-%H%M%S}.log"
        path = filedialog.asksaveasfilename(
            defaultextension=".log", initialfile=default,
            initialdir=str(LOG_DIR),
        )
        if not path:
            return
        try:
            Path(path).write_text(self.log_text.get(1.0, tk.END), encoding="utf-8")
            messagebox.showinfo("저장 완료", path)
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    # ─────────────────────────────────────── 폴링 ─────────────────────
    def _check_bot_process(self) -> None:
        if self.bot_process and self.bot_process.poll() is not None:
            ret = self.bot_process.returncode
            self.bot_process = None
            self._update_status()
            if self.manual_stop:
                self._log("정상 중지됨.\n", "SYS")
                self.manual_stop = False
            elif self.auto_restart.get():
                self._log(
                    f"비정상 종료 (코드 {ret}). 5초 후 자동 재시작...\n", "WARN",
                )
                self.root.after(5000, self.start_bot)
            else:
                self._log(f"비정상 종료 (코드 {ret}). 자동 재시작 OFF.\n", "WARN")
        self.root.after(1000, self._check_bot_process)

    def _update_status(self) -> None:
        if self.bot_process and self.bot_process.poll() is None:
            self.status_dot.config(fg="#4caf50")
            self.status_text.config(text=f"실행 중  ·  PID {self.bot_process.pid}")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
        else:
            self.status_dot.config(fg="#666")
            self.status_text.config(text="중지됨")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

    def _on_close(self) -> None:
        if self.bot_process and self.bot_process.poll() is None:
            if not messagebox.askyesno(
                "종료 확인", "봇이 실행 중입니다. 함께 종료할까요?"
            ):
                return
            self.manual_stop = True
            self.stop_bot()
        self._save_settings()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        # Windows 에서 자연스러운 ttk 테마
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    HaruBotDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
