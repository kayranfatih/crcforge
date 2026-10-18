"""Terminal output: human-readable sections, JSON, quiet mode and progress."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import IO, Any

ANSI_RESET = "\033[0m"
ANSI_FRAME = "\033[38;5;39m"
ANSI_TEXT = "\033[38;5;51m"
ANSI_CREDIT = "\033[38;5;220m"
ANSI_INFO = "\033[38;5;45m"
ANSI_WARN = "\033[38;5;214m"
ANSI_ERROR = "\033[38;5;196m"
ANSI_SUCCESS = "\033[38;5;82m"
ANSI_DIM = "\033[38;5;245m"

_PALETTE = {
    "info": ANSI_INFO,
    "success": ANSI_SUCCESS,
    "warn": ANSI_WARN,
    "error": ANSI_ERROR,
    "text": ANSI_TEXT,
    "dim": ANSI_DIM,
}

LABEL_WIDTH = 16


def supports_color(stream: object | None = None) -> bool:
    target = stream if stream is not None else sys.stdout
    if os.getenv("NO_COLOR") is not None:
        return False
    if os.getenv("FORCE_COLOR") is not None:
        return True
    return bool(getattr(target, "isatty", lambda: False)())


def colorize(text: str, ansi_code: str, enabled: bool) -> str:
    return f"{ansi_code}{text}{ANSI_RESET}" if enabled else text


def _is_tty(stream: IO[str]) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)())


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds + 0.5))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class Output:
    """Writes command results in one of three modes: human (default), JSON or quiet."""

    def __init__(
        self,
        json_mode: bool = False,
        quiet: bool = False,
        color: bool | None = None,
        stdout: IO[str] | None = None,
        stderr: IO[str] | None = None,
    ) -> None:
        self.json = json_mode
        self.quiet = quiet and not json_mode
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.color = supports_color(self.stdout) if color is None else color
        self.color_err = supports_color(self.stderr) if color is None else color

    @property
    def human(self) -> bool:
        return not (self.json or self.quiet)

    def _style(self, text: str, status: str) -> str:
        return colorize(text, _PALETTE.get(status, ANSI_INFO), self.color)

    def line(self, text: str = "") -> None:
        print(text, file=self.stdout)

    def section(self, title: str) -> None:
        if self.human:
            self.line(colorize(title, ANSI_FRAME, self.color))

    def kv(self, label: str, value: object, status: str = "info") -> None:
        if self.human:
            self.line(f"{self._style(f'{label:{LABEL_WIDTH}}', status)} {value}")

    def detail(self, text: str, status: str = "text") -> None:
        """An indented continuation line under the previous key/value pair."""
        if self.human:
            self.line(f"{'':{LABEL_WIDTH}} {self._style(text, status)}")

    def note(self, text: str) -> None:
        if self.human:
            self.line(self._style(f"note: {text}", "dim"))

    def warn(self, text: str) -> None:
        if self.human:
            self.line(self._style(f"warning: {text}", "warn"))

    def success(self, text: str) -> None:
        if self.human:
            self.line(self._style(text, "success"))

    def failure(self, text: str) -> None:
        if self.human:
            self.line(self._style(text, "error"))

    def error(self, message: str, hint: str | None = None) -> None:
        if self.json:
            payload: dict[str, Any] = {"error": message}
            if hint:
                payload["hint"] = hint
            self.emit_json(payload)
            return
        print(colorize(f"error: {message}", ANSI_ERROR, self.color_err), file=self.stderr)
        if hint:
            print(colorize(f"hint: {hint}", ANSI_DIM, self.color_err), file=self.stderr)

    def emit_json(self, payload: object) -> None:
        print(json.dumps(payload, indent=2, sort_keys=False), file=self.stdout)

    def value(self, text: str) -> None:
        """The bare result printed in quiet mode."""
        print(text, file=self.stdout)

    def progress(self, interval: float) -> Progress:
        return Progress(self.stderr, interval, enabled=interval > 0 and (self.human or _is_tty(self.stderr)))


class Progress:
    """Live progress on stderr: one updating line on a terminal, periodic lines otherwise."""

    def __init__(self, stream: IO[str], interval: float, enabled: bool = True) -> None:
        self.stream = stream
        self.interval = interval
        self.enabled = enabled
        self.tty = _is_tty(stream)
        self.start = time.monotonic()
        # Short operations finish silently: the first update waits one full interval.
        self.last = self.start if not self.tty else self.start - 1.0
        self.drawn = False

    def update(self, done: int, total: int, label: str = "") -> None:
        if not self.enabled or total <= 0:
            return
        now = time.monotonic()
        refresh = 0.25 if self.tty else self.interval
        if now - self.last < refresh or (not self.tty and done >= total):
            return
        if self.tty and now - self.start < 0.5:
            return
        self.last = now
        elapsed = now - self.start
        rate = done / elapsed if elapsed > 0 else 0.0
        percent = 100.0 * done / total
        eta = format_duration((total - done) / rate) if rate > 0 and done < total else "--:--"
        text = f"Progress {percent:6.2f}%  {done:,}/{total:,}  {rate:,.0f}/s  ETA {eta}"
        if label:
            text += f"  {label}"
        if self.tty:
            self.stream.write("\r" + text[:120].ljust(120))
            self.drawn = True
        else:
            self.stream.write(text + "\n")
        self.stream.flush()

    def close(self) -> None:
        if self.enabled and self.tty and self.drawn:
            self.stream.write("\r" + " " * 120 + "\r")
            self.stream.flush()
            self.drawn = False
