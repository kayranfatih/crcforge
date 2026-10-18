"""The CRCForge banner: gradient block letters, with an ASCII fallback for limited terminals."""

from __future__ import annotations

from . import __version__
from .output import ANSI_CREDIT, ANSI_DIM, ANSI_FRAME, ANSI_RESET, ANSI_TEXT, supports_color, supports_unicode

AUTHOR_LINE = "by Fatih Kayran"
SOCIAL_LINE = "X/Twitter: @kayranfatih"
TAGLINE = "CRC & checksum toolkit for reverse engineers"

# "ANSI Shadow" style letters, six rows each.
_LETTERS = {
    "C": (" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"),
    "R": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"),
    "F": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "),
    "O": (" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "G": (" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "E": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"),
}

LOGO_LINES = tuple("".join(_LETTERS[letter][row] for letter in "CRCFORGE").rstrip() for row in range(6))
LOGO_WIDTH = max(len(line) for line in LOGO_LINES)

# Top-to-bottom gradient for the solid blocks; the "shadow" strokes stay muted.
_GRADIENT = ("\033[38;5;51m", "\033[38;5;45m", "\033[38;5;39m", "\033[38;5;33m", "\033[38;5;69m", "\033[38;5;99m")
_SHADOW = "\033[38;5;60m"

PLAIN_BANNER = r"""
+-----------------------------------------------------------+
|   ____ ____   ____ _____ ___  ____   ____ _____           |
|  / ___|  _ \ / ___|  ___/ _ \|  _ \ / ___| ____|          |
| | |   | |_) | |   | |_ | | | | |_) | |  _|  _|            |
| | |___|  _ <| |___|  _|| |_| |  _ <| |_| | |___           |
|  \____|_| \_\\____|_|   \___/|_| \_\\____|_____|          |
|                                                           |
+-----------------------------------------------------------+
""".strip("\n")


def _paint_logo_line(line: str, row: int) -> str:
    """Color runs of block characters with the row's gradient color and box strokes as shadow."""
    painted = []
    current = None
    for character in line:
        if character == "█":
            code = _GRADIENT[row]
        elif character == " ":
            code = None
        else:
            code = _SHADOW
        if code != current:
            if current is not None:
                painted.append(ANSI_RESET)
            if code is not None:
                painted.append(code)
            current = code
        painted.append(character)
    if current is not None:
        painted.append(ANSI_RESET)
    return "".join(painted)


def render_banner(color: bool | None = None, unicode: bool | None = None) -> str:
    """The logo plus a tagline with the version."""
    colored = supports_color() if color is None else color
    fancy = supports_unicode() if unicode is None else unicode
    version = f"v{__version__}"
    if not fancy:
        lines = [
            f"{ANSI_FRAME if line.startswith(('+', '|')) else ANSI_TEXT}{line}{ANSI_RESET}" if colored else line
            for line in PLAIN_BANNER.splitlines()
        ]
        tagline = f"{TAGLINE}  {version}"
        lines.append(f"{ANSI_TEXT}{tagline}{ANSI_RESET}" if colored else tagline)
        return "\n".join(lines)
    lines = [_paint_logo_line(line, row) if colored else line for row, line in enumerate(LOGO_LINES)]
    gap = " " * max(2, LOGO_WIDTH - len(TAGLINE) - len(version) - 1)
    if colored:
        lines.append(f" {ANSI_TEXT}{TAGLINE}{ANSI_RESET}{gap}{ANSI_DIM}{version}{ANSI_RESET}")
    else:
        lines.append(f" {TAGLINE}{gap}{version}")
    return "\n".join(lines)


def render_credit_lines(color: bool | None = None, unicode: bool | None = None) -> str:
    colored = supports_color() if color is None else color
    fancy = supports_unicode() if unicode is None else unicode
    separator = " · " if fancy else " - "
    text = f"{AUTHOR_LINE}{separator}{SOCIAL_LINE}"
    if not fancy:
        return "\n".join(f"{ANSI_CREDIT}{line}{ANSI_RESET}" if colored else line for line in (AUTHOR_LINE, SOCIAL_LINE))
    return f" {ANSI_CREDIT}{text}{ANSI_RESET}" if colored else f" {text}"
