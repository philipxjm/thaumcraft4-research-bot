"""Persistent tkinter GUI for the research bot.

The window stays open across boards: press "Solve board" (or the global
hotkey from config.toml) for each new Research Notes item. Errors are shown
in the log pane instead of killing the process, and all bot output (print
and logging) is mirrored into the window.

tkinter is standard library, so this adds no dependencies; the Nuitka build
just needs --enable-plugin=tk-inter.
"""

import logging
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import scrolledtext


class _QueueStream:
    """File-like object that forwards writes into a queue (captures print)."""

    def __init__(self, q):
        self._q = q

    def write(self, text):
        if text:
            self._q.put(text)

    def flush(self):
        pass


class _QueueLogHandler(logging.Handler):
    """Mirrors the bot's logger into the GUI log pane."""

    def __init__(self, q):
        super().__init__()
        self._q = q
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            self._q.put(self.format(record) + "\n")
        except Exception:
            pass


def run_gui(solve_and_place, retry_place, hotkey=None):
    """Runs the persistent window.

    solve_and_place: callable performing one full board cycle (solve + place).
    retry_place: callable re-placing the last solution.
    hotkey: optional global hotkey string (from config) that triggers a solve.
    """
    root = tk.Tk()
    root.title("Thaumcraft 4 Research Bot")
    root.geometry("640x460")
    root.minsize(480, 320)

    status_var = tk.StringVar(
        value="Ready. Open a Research Table with unsolved Research Notes, then press Solve."
    )

    top = tk.Frame(root)
    top.pack(fill=tk.X, padx=8, pady=6)
    solve_btn = tk.Button(top, text="Solve board" + (f"  ({hotkey})" if hotkey else ""), width=22)
    retry_btn = tk.Button(top, text="Retry placement", width=16, state=tk.DISABLED)
    solve_btn.pack(side=tk.LEFT)
    retry_btn.pack(side=tk.LEFT, padx=(8, 0))

    tk.Label(root, textvariable=status_var, anchor="w").pack(fill=tk.X, padx=8)

    log_box = scrolledtext.ScrolledText(root, state=tk.DISABLED, wrap=tk.WORD, font=("Consolas", 9))
    log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

    logq = queue.Queue()
    ui_calls = queue.Queue()
    sys.stdout = _QueueStream(logq)
    sys.stderr = _QueueStream(logq)
    logging.getLogger("bot").addHandler(_QueueLogHandler(logq))

    busy = threading.Event()
    hotkey_fired = threading.Event()
    has_solution = threading.Event()

    def append_log(text):
        log_box.config(state=tk.NORMAL)
        log_box.insert(tk.END, text)
        log_box.see(tk.END)
        log_box.config(state=tk.DISABLED)

    def set_buttons(enabled):
        solve_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)
        retry_btn.config(
            state=tk.NORMAL if (enabled and has_solution.is_set()) else tk.DISABLED
        )

    def run_in_thread(fn, running_text, done_text, marks_solution=False):
        if busy.is_set():
            return
        busy.set()
        set_buttons(False)
        status_var.set(running_text)

        def work():
            ok = False
            try:
                fn()
                ok = True
            except Exception:
                traceback.print_exc()

            def finish():
                if ok:
                    if marks_solution:
                        has_solution.set()
                    status_var.set(done_text)
                else:
                    status_var.set("Error - see log below. Fix the cause and press Solve again.")
                busy.clear()
                set_buttons(True)

            # Tk widgets must only be touched from the main thread; the poll
            # loop below executes this on our behalf.
            ui_calls.put(finish)

        threading.Thread(target=work, daemon=True).start()

    def on_solve():
        run_in_thread(
            solve_and_place,
            "Solving board...",
            "Done. Put in the next unsolved notes, then press Solve.",
            marks_solution=True,
        )

    def on_retry():
        run_in_thread(retry_place, "Re-placing aspects...", "Placement retried.")

    solve_btn.config(command=on_solve)
    retry_btn.config(command=on_retry)

    if hotkey:
        try:
            import keyboard

            # The callback runs on the keyboard library's own thread - only
            # set a flag; the poll loop triggers the solve on the Tk thread.
            keyboard.add_hotkey(hotkey, hotkey_fired.set)
            append_log(f"Global hotkey active: {hotkey}\n")
        except Exception:
            append_log("Could not register global hotkey:\n" + traceback.format_exc())

    def poll():
        while True:
            try:
                append_log(logq.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                ui_calls.get_nowait()()
            except queue.Empty:
                break
        if hotkey_fired.is_set():
            hotkey_fired.clear()
            on_solve()
        root.after(50, poll)

    root.after(50, poll)
    root.mainloop()
