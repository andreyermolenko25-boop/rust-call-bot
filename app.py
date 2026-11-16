import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
SETTINGS = BASE_DIR / "settings.env"
PYTHON = Path(sys.executable).with_name("python.exe")
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

HELP_TEXT = (
    "Во время звонка: напиши подсказку и нажми «Подсказать ИИ» (или Enter) — бот учтёт её в следующем ответе. "
    "«Сказать дословно» — бот сразу произнесёт твой текст слово в слово."
)


def load_settings() -> dict:
    return {k: (v or "") for k, v in dotenv_values(SETTINGS).items()}


def save_setting(key: str, value: str) -> None:
    lines = SETTINGS.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    SETTINGS.write_text("\n".join(lines) + "\n", encoding="utf-8")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()

        root.title("Звонилка")
        root.geometry("720x760")
        root.minsize(560, 560)
        style = ttk.Style()
        style.configure("Start.TButton", font=("Segoe UI", 12, "bold"), padding=10)
        style.configure("Stop.TButton", font=("Segoe UI", 12, "bold"), padding=10)
        style.configure("Status.TLabel", font=("Segoe UI", 11, "bold"))

        s = load_settings()
        top = ttk.LabelFrame(root, text="Звонок", padding=12)
        top.pack(fill="x", padx=12, pady=(12, 6))
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Ник в Telegram:").grid(row=0, column=0, sticky="w")
        self.friend = ttk.Entry(top)
        self.friend.insert(0, s.get("FRIEND", "@"))
        self.friend.grid(row=0, column=1, sticky="ew", padx=(6, 12))

        ttk.Label(top, text="Как зовут:").grid(row=0, column=2, sticky="w")
        self.friend_name = ttk.Entry(top)
        self.friend_name.insert(0, s.get("FRIEND_NAME", ""))
        self.friend_name.grid(row=0, column=3, sticky="ew", padx=(6, 0))

        ttk.Label(top, text="Основная тема:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.topic = ttk.Entry(top)
        self.topic.insert(0, s.get("CALL_TOPIC", ""))
        self.topic.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(10, 0))

        self.swear = tk.BooleanVar(value=s.get("SWEAR") == "1")
        ttk.Checkbutton(top, text="С матом", variable=self.swear).grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(10, 0))
        self.president = tk.BooleanVar(value=s.get("PRESIDENT") == "1")
        ttk.Checkbutton(top, text="🎩 Пафосный президент", variable=self.president).grid(
            row=2, column=2, columnspan=2, sticky="w", pady=(10, 0)
        )

        buttons = ttk.Frame(top)
        buttons.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.start_btn = ttk.Button(buttons, text="📞  Начать звонок", style="Start.TButton", command=self.start_call)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_btn = ttk.Button(buttons, text="⛔  Завершить звонок", style="Stop.TButton", command=self.stop_call, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.status = ttk.Label(top, text="Готово к звонку", style="Status.TLabel")
        self.status.grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))

        mid = ttk.LabelFrame(root, text="Разговор", padding=8)
        mid.pack(fill="both", expand=True, padx=12, pady=6)
        self.log = scrolledtext.ScrolledText(mid, wrap="word", state="disabled", font=("Segoe UI", 11), height=14)
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("friend", foreground="#0b5394")
        self.log.tag_configure("bot", foreground="#38761d")
        self.log.tag_configure("info", foreground="#777777")

        bottom = ttk.LabelFrame(root, text="Подсказка ИИ во время звонка", padding=8)
        bottom.pack(fill="x", padx=12, pady=(6, 12))
        bottom.columnconfigure(0, weight=1)
        self.hint = ttk.Entry(bottom, font=("Segoe UI", 11))
        self.hint.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.hint.bind("<Return>", lambda _e: self.send_hint())
        self.hint_btn = ttk.Button(bottom, text="💡 Подсказать ИИ", command=self.send_hint, state="disabled")
        self.hint_btn.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.say_btn = ttk.Button(bottom, text="🗣 Сказать дословно", command=self.say_exact, state="disabled")
        self.say_btn.grid(row=1, column=1, sticky="e", pady=(6, 0))
        ttk.Label(bottom, text=HELP_TEXT, wraplength=660, foreground="#666666").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll_output)


    def write(self, text: str, tag: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_running(self, running: bool) -> None:
        state_on, state_off = ("normal", "disabled") if running else ("disabled", "normal")
        self.stop_btn.configure(state=state_on)
        self.hint_btn.configure(state=state_on)
        self.say_btn.configure(state=state_on)
        self.start_btn.configure(state=state_off)
        for entry in (self.friend, self.friend_name, self.topic):
            entry.configure(state=state_off)


    def start_call(self) -> None:
        friend = self.friend.get().strip()
        name = self.friend_name.get().strip()
        topic = self.topic.get().strip()
        if not friend.startswith("@") or len(friend) < 2:
            messagebox.showwarning("Звонилка", "Ник должен начинаться с @, например @username")
            return
        if not name:
            name = friend.lstrip("@")
        if not topic:
            messagebox.showwarning("Звонилка", "Напиши основную тему звонка")
            return
        save_setting("FRIEND", friend)
        save_setting("FRIEND_NAME", name)
        save_setting("CALL_TOPIC", topic)
        save_setting("SWEAR", "1" if self.swear.get() else "0")
        save_setting("PRESIDENT", "1" if self.president.get() else "0")

        self.write(f"\n— Звонок {name} ({friend}). Тема: {topic}", "info")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [str(PYTHON), str(BASE_DIR / "bot.py"), topic],
            cwd=BASE_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self.read_output, args=(self.proc,), daemon=True).start()
        self.status.configure(text="Соединяюсь…")
        self.set_running(True)

    def stop_call(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        self.send_line("/стоп")
        self.status.configure(text="Кладу трубку…")
        self.stop_btn.configure(state="disabled")
        proc = self.proc
        self.root.after(10000, lambda: proc.poll() is None and proc.terminate())

    def send_line(self, text: str) -> None:
        if self.proc and self.proc.poll() is None and self.proc.stdin:
            try:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def send_hint(self) -> None:
        text = self.hint.get().strip()
        if text and self.proc and self.proc.poll() is None:
            self.send_line(text)
            self.write(f"💡 Подсказка: {text}", "info")
            self.hint.delete(0, "end")

    def say_exact(self) -> None:
        text = self.hint.get().strip()
        if text and self.proc and self.proc.poll() is None:
            self.send_line("!" + text)
            self.hint.delete(0, "end")


    def read_output(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.put(line.rstrip("\n"))
        proc.wait()
        self.lines.put(None)

    def poll_output(self) -> None:
        try:
            while True:
                line = self.lines.get_nowait()
                if line is None:
                    self.on_finished()
                    continue
                self.show_line(line)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_output)

    def show_line(self, line: str) -> None:
        text = line.strip()
        if not text or text.startswith(("•", "ℹ️ Пиши в это окно", "Тема звонка:", "ℹ️ Подсказка принята")):
            return
        name = self.friend_name.get().strip()
        if text.startswith("Звоню:"):
            self.status.configure(text="Идут гудки…")
        elif text.startswith("Соединено"):
            self.status.configure(text="🟢 Идёт разговор")
        if " Бот: " in text[:20]:
            self.write(text, "bot")
        elif name and f" {name}: " in text[: 12 + len(name)]:
            self.write(text, "friend")
        else:
            self.write(text, "info")

    def on_finished(self) -> None:
        self.proc = None
        self.set_running(False)
        self.status.configure(text="Звонок завершён. Готово к новому звонку")
        self.write("— Звонок завершён", "info")

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Звонилка", "Идёт звонок. Положить трубку и закрыть?"):
                return
            self.send_line("/стоп")
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
