"""
Shinra Typewriter - window app.

Start it, click into the game, press Ctrl+Alt+S (or the Start button) and it types the
word boxes for you. Settings are saved to Documents\\Shinra Typewriter\\settings.json.
"""

import os
import queue
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import bot_core
from bot_core import Bot, Settings

HOTKEY_TOGGLE = "ctrl+alt+s"
HOTKEY_SNAPSHOT = "ctrl+alt+d"
HOTKEY_SCREENSHOT = "ctrl+alt+p"

BG = "#16181d"
PANEL = "#1f232a"
FG = "#e6e8eb"
MUTED = "#8b929c"
ACCENT = "#4f8cff"
GO = "#2fb36d"
STOP = "#e0524d"
WARN = "#e0a43a"
GREEN_TXT = "#57d38c"
RED_TXT = "#ff7a75"


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.settings = Settings.load()
        self.bot = Bot(self.settings, lambda kind, text: self.events.put((kind, text)))
        self.typed_count = 0

        root.title("Shinra Typewriter")
        root.configure(bg=BG)
        root.minsize(460, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._style()
        self._build()

        self.tess = bot_core.setup_tesseract()
        if not self.tess:
            self.start_btn.configure(state="disabled")
            self.log("Tesseract OCR wasn't found, so the bot can't read words.", "error")
            if messagebox.askyesno(
                    "Tesseract missing",
                    "Tesseract OCR wasn't found.\n\nThis build doesn't include it. Install it "
                    "(default location) and restart the app.\n\nOpen the download page?"):
                webbrowser.open("https://github.com/UB-Mannheim/tesseract/wiki")
        else:
            self.log("Ready. Click into the game, then press Ctrl+Alt+S to start.", "info")

        self._hotkeys()
        self.bot.start()
        self.root.after(80, self._pump)

    # ---------------------------------------------------------------- UI
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=("Segoe UI", 10))
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("Panel.TLabel", background=PANEL, foreground=FG)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("Head.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 16))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("Section.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI Semibold", 9))
        st.configure("Panel.TCheckbutton", background=PANEL, foreground=FG)
        st.map("Panel.TCheckbutton", background=[("active", PANEL)])
        st.configure("Tool.TButton", background="#2a2f38", foreground=FG, borderwidth=0, padding=(10, 6))
        st.map("Tool.TButton", background=[("active", "#353b46")])
        st.configure("Horizontal.TScale", background=PANEL, troughcolor="#2a2f38")
        st.configure("TCombobox", fieldbackground="#2a2f38", background="#2a2f38", foreground=FG,
                     arrowcolor=FG)
        st.configure("TSpinbox", fieldbackground="#2a2f38", foreground=FG, arrowcolor=FG)

    def _panel(self, parent, title):
        outer = ttk.Frame(parent, style="Panel.TFrame", padding=(14, 10))
        outer.pack(fill="x", pady=(0, 10))
        ttk.Label(outer, text=title.upper(), style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        return outer

    def _build(self):
        wrap = ttk.Frame(self.root, padding=16)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="Shinra Typewriter", style="Head.TLabel").pack(anchor="w")
        ttk.Label(wrap, text="Reads the word boxes on screen and types them for you",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 12))

        # Start / status
        top = ttk.Frame(wrap, style="Panel.TFrame", padding=14)
        top.pack(fill="x", pady=(0, 10))
        self.start_btn = tk.Button(top, text="Start", command=self.toggle, bg=GO, fg="white",
                                   activebackground=GO, activeforeground="white", relief="flat",
                                   font=("Segoe UI Semibold", 13), padx=18, pady=6, cursor="hand2")
        self.start_btn.pack(side="left")
        status = ttk.Frame(top, style="Panel.TFrame")
        status.pack(side="left", padx=14)
        self.status_dot = tk.Label(status, text="●", bg=PANEL, fg=MUTED, font=("Segoe UI", 12))
        self.status_dot.grid(row=0, column=0, sticky="w")
        self.status_lbl = ttk.Label(status, text="Paused", style="Panel.TLabel",
                                    font=("Segoe UI Semibold", 11))
        self.status_lbl.grid(row=0, column=1, sticky="w", padx=(4, 0))
        ttk.Label(status, text="Ctrl+Alt+S to start/stop from in-game", style="Muted.TLabel") \
            .grid(row=1, column=0, columnspan=2, sticky="w")
        self.count_lbl = ttk.Label(top, text="0 typed", style="Muted.TLabel")
        self.count_lbl.pack(side="right")

        # Settings
        p = self._panel(wrap, "Settings")
        grid = ttk.Frame(p, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Game screen", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self.monitors = self._list_monitors()
        self.monitor_var = tk.StringVar()
        names = [n for _, n in self.monitors]
        cb = ttk.Combobox(grid, textvariable=self.monitor_var, values=names, state="readonly", width=28)
        cb.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        current = next((n for i, n in self.monitors if i == self.settings.monitor), names[0] if names else "")
        self.monitor_var.set(current)
        cb.bind("<<ComboboxSelected>>", lambda e: self._set_monitor())

        self.key_delay = self._slider(grid, 1, "Delay between keys", 5, 80, self.settings.key_delay_ms, "key_delay_ms")
        self.enter_delay = self._slider(grid, 2, "Pause before Enter", 0, 300, self.settings.enter_delay_ms, "enter_delay_ms")

        ttk.Label(grid, text="Don't repeat a word for", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=3)
        rb = ttk.Frame(grid, style="Panel.TFrame")
        rb.grid(row=3, column=1, columnspan=2, sticky="w")
        self.repeat_var = tk.DoubleVar(value=self.settings.repeat_block_s)
        sp = ttk.Spinbox(rb, from_=0, to=10, increment=0.5, width=5, textvariable=self.repeat_var,
                         command=self._set_repeat)
        sp.pack(side="left")
        sp.bind("<FocusOut>", lambda e: self._set_repeat())
        ttk.Label(rb, text="seconds", style="Muted.TLabel").pack(side="left", padx=6)

        checks = ttk.Frame(p, style="Panel.TFrame")
        checks.pack(fill="x", pady=(8, 0))
        self._check(checks, 0, 0, "Type green boxes first", "green_first")
        self._check(checks, 0, 1, "Then red boxes", "red_second")
        self._check(checks, 1, 0, "Press Enter after each word", "press_enter")
        self._check(checks, 1, 1, "Type capital letters", "match_case")
        self._check(checks, 2, 0, "Only type while the game window is active", "only_in_game")

        # Tools
        t = self._panel(wrap, "Tools")
        row = ttk.Frame(t, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="Debug snapshot", style="Tool.TButton",
                   command=self.bot.request_snapshot).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Screenshot", style="Tool.TButton",
                   command=self.bot.request_screenshot).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Open folder", style="Tool.TButton",
                   command=self.open_folder).pack(side="left")
        self.auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(t, text=f"Auto screenshot every {bot_core.AUTOSHOT_INTERVAL:g}s",
                        style="Panel.TCheckbutton", variable=self.auto_var,
                        command=lambda: self.bot.set_autoshot(self.auto_var.get())).pack(anchor="w", pady=(8, 0))
        ttk.Label(t, text="Hotkeys: Ctrl+Alt+D debug snapshot · Ctrl+Alt+P screenshot",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        # Log
        ttk.Label(wrap, text="ACTIVITY", style="Sub.TLabel").pack(anchor="w")
        self.log_box = ScrolledText(wrap, height=10, bg=PANEL, fg=FG, insertbackground=FG,
                                    relief="flat", font=("Consolas", 10), wrap="word",
                                    borderwidth=0, highlightthickness=0)
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))
        self.log_box.tag_config("info", foreground=MUTED)
        self.log_box.tag_config("error", foreground=STOP)
        self.log_box.tag_config("green", foreground=GREEN_TXT)
        self.log_box.tag_config("red", foreground=RED_TXT)
        self.log_box.tag_config("other", foreground=FG)
        self.log_box.configure(state="disabled")

    def _slider(self, grid, row, label, lo, hi, value, attr):
        ttk.Label(grid, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        var = tk.IntVar(value=value)
        val_lbl = ttk.Label(grid, text=f"{value} ms", style="Muted.TLabel", width=7)
        val_lbl.grid(row=row, column=2, sticky="e")

        def changed(_=None):
            v = int(float(var.get()))
            var.set(v)
            val_lbl.configure(text=f"{v} ms")
            setattr(self.settings, attr, v)
        ttk.Scale(grid, from_=lo, to=hi, variable=var, command=changed).grid(
            row=row, column=1, sticky="ew", padx=(10, 6), pady=3)
        return var

    def _check(self, parent, r, c, text, attr):
        var = tk.BooleanVar(value=getattr(self.settings, attr))
        ttk.Checkbutton(parent, text=text, variable=var, style="Panel.TCheckbutton",
                        command=lambda: setattr(self.settings, attr, var.get())).grid(
            row=r, column=c, columnspan=2 if r == 2 else 1, sticky="w", padx=(0, 18), pady=2)
        return var

    # ---------------------------------------------------------------- helpers
    def _list_monitors(self):
        try:
            import mss
            mss_cls = getattr(mss, "MSS", None) or mss.mss
            with mss_cls() as sct:
                out = []
                for i, m in enumerate(sct.monitors[1:], start=1):
                    tag = " (main)" if m["left"] == 0 and m["top"] == 0 else ""
                    out.append((i, f"Screen {i}: {m['width']}×{m['height']}{tag}"))
                return out or [(1, "Screen 1")]
        except Exception:
            return [(1, "Screen 1")]

    def _set_monitor(self):
        name = self.monitor_var.get()
        for i, n in self.monitors:
            if n == name:
                self.settings.monitor = i

    def _set_repeat(self):
        try:
            self.settings.repeat_block_s = max(0.0, float(self.repeat_var.get()))
        except (tk.TclError, ValueError):
            pass

    def _hotkeys(self):
        try:
            import keyboard
            keyboard.add_hotkey(HOTKEY_TOGGLE, lambda: self.events.put(("toggle", "")))
            keyboard.add_hotkey(HOTKEY_SNAPSHOT, self.bot.request_snapshot)
            keyboard.add_hotkey(HOTKEY_SCREENSHOT, self.bot.request_screenshot)
        except Exception as e:
            self.log(f"Hotkeys unavailable ({e}). Use the buttons instead.", "error")

    def log(self, text, tag="info"):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n", tag)
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_box.delete("1.0", f"{lines - 500}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_status(self, state):
        text, colour = {"running": ("Running", GO), "paused": ("Paused", MUTED),
                        "disabled": ("Inputs disabled — waiting", WARN),
                        "away": ("Waiting for the game window", WARN)}[state]
        self.status_lbl.configure(text=text)
        self.status_dot.configure(fg=colour)
        if state == "paused":
            self.start_btn.configure(text="Start", bg=GO, activebackground=GO)
        else:
            self.start_btn.configure(text="Stop", bg=STOP, activebackground=STOP)

    # ---------------------------------------------------------------- actions
    def toggle(self):
        if not self.tess:
            return
        self.bot.toggle()

    def open_folder(self):
        os.makedirs(bot_core.DATA_DIR, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(bot_core.DATA_DIR)
        else:
            self.log(bot_core.DATA_DIR, "info")

    def _pump(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "toggle":
                    self.toggle()
                elif kind == "status":
                    self.set_status(text)
                elif kind == "typed":
                    word, colour = text.rsplit("|", 1)
                    self.typed_count += 1
                    self.count_lbl.configure(text=f"{self.typed_count} typed")
                    self.log(f"✓ {word}", colour if colour in ("green", "red") else "other")
                elif kind == "snapshot":
                    self.log(f"Debug snapshot saved: {text}", "info")
                    if sys.platform == "win32":
                        try:
                            os.startfile(text)
                        except OSError:
                            pass
                elif kind == "error":
                    self.log(text, "error")
                else:
                    self.log(text, "info")
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def on_close(self):
        self._set_repeat()
        self.settings.save()
        self.bot.shutdown()
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        self.root.destroy()


def main():
    if sys.platform == "win32":
        try:    # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
