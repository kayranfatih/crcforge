"""Colored, table-style help screens and friendly command-line errors."""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import textwrap
from collections.abc import Callable, Sequence
from typing import IO, NoReturn

from .output import ANSI_DIM, ANSI_ERROR, colorize, pad_visible, supports_color, supports_unicode
from .output import terminal_width as _terminal_width

ANSI_HEADING = "\033[1;38;5;39m"
ANSI_COMMAND = "\033[38;5;51m"
ANSI_OPTION = "\033[38;5;45m"
ANSI_PROMPT = "\033[38;5;245m"

DOCS_URL = "https://kayranfatih.github.io/crcforge/"
OPTION_COLUMN = 30


class HelpStyle:
    def __init__(self, color: bool, unicode: bool, width: int) -> None:
        self.color = color
        self.unicode = unicode
        self.width = width

    def paint(self, text: str, code: str) -> str:
        return colorize(text, code, self.color)

    def heading(self, text: str) -> str:
        return self.paint(text, ANSI_HEADING)

    def dim(self, text: str) -> str:
        return self.paint(text, ANSI_DIM)

    def command_line(self, text: str) -> str:
        """``crcforge find ...`` with a muted prompt and colored command."""
        if text.startswith("crcforge "):
            return f"{self.paint('crcforge', ANSI_PROMPT)} {self.paint(text[len('crcforge ') :], ANSI_COMMAND)}"
        return self.paint(text, ANSI_COMMAND)

    def rows(self, entries: Sequence[tuple[str, str]], key_code: str, key_width: int | None = None) -> list[str]:
        """Two-column rows: colored key, wrapped description aligned in a column."""
        widest = max((len(key) for key, _ in entries), default=0)
        column = min(key_width or widest, OPTION_COLUMN)
        text_width = max(30, self.width - column - 6)
        lines = []
        for key, text in entries:
            wrapped = textwrap.wrap(text, text_width) or [""]
            painted_key = self.paint(key, key_code)
            if len(key) > column:
                lines.append(f"  {painted_key}")
                lines.extend(f"  {'':{column}}  {part}" for part in wrapped)
                continue
            lines.append(f"  {pad_visible(painted_key, column)}  {wrapped[0]}")
            lines.extend(f"  {'':{column}}  {part}" for part in wrapped[1:])
        return lines


def help_style(stream: IO[str] | None = None) -> HelpStyle:
    target = stream if stream is not None else sys.stdout
    color = supports_color(target) and not CrcforgeParser.no_color
    return HelpStyle(color, supports_unicode(target), _terminal_width())


def _option_metavar(action: argparse.Action) -> str:
    if action.nargs == 0:
        return ""
    if action.metavar is not None:
        return str(action.metavar)
    if action.choices:
        joined = "|".join(str(choice) for choice in action.choices)
        if len(joined) <= 22:
            return joined
    return str(action.dest).upper()


def _option_entry(action: argparse.Action) -> tuple[str, str]:
    if isinstance(action, argparse._HelpAction):
        return ", ".join(action.option_strings), "Show this help."
    if isinstance(action, argparse._VersionAction):
        return ", ".join(action.option_strings), "Show the version."
    metavar = _option_metavar(action)
    key = ", ".join(action.option_strings) + (f" {metavar}" if metavar else "")
    return key, str(action.help or "")


def _section_title(title: str | None) -> str:
    if not title or title in ("options", "optional arguments"):
        return "Options"
    if title == "positional arguments":
        return "Arguments"
    return title[0].upper() + title[1:]


class CrcforgeParser(argparse.ArgumentParser):
    """ArgumentParser with CRCForge's own help screens and error messages."""

    no_color = False
    json_errors = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.examples: list[str] = []
        self.example_comments: list[str] = []
        self.notes: list[str] = []
        self.argument_docs: list[tuple[str, str]] = []
        self.command_groups: list[tuple[str, list[str]]] = []
        self.main_sections: list[tuple[str, list[tuple[str, str]]]] = []
        self.banner: Callable[[bool, bool], str] | None = None
        self.summary = ""
        self.command_parsers: dict[str, CrcforgeParser] = {}
        self.command_help: dict[str, str] = {}
        self.required_text: str | None = None
        self._last_args: list[str] = []
        self._error_example: str | None = None
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    # -- help ----------------------------------------------------------------------------

    def parse_known_args(  # type: ignore[override]
        self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        self._last_args = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    def format_help(self) -> str:
        style = help_style()
        lines = self._main_help(style) if self.command_parsers else self._command_help(style)
        return "\n".join(lines).rstrip() + "\n"

    def print_help(self, file: IO[str] | None = None) -> None:  # type: ignore[override]
        target = file if file is not None else sys.stdout
        style = help_style(target)
        lines = self._main_help(style) if self.command_parsers else self._command_help(style)
        target.write("\n".join(lines).rstrip() + "\n")

    def _main_help(self, style: HelpStyle) -> list[str]:
        lines: list[str] = []
        if self.banner is not None:
            lines.extend([self.banner(style.color, style.unicode), ""])
        if self.summary:
            lines.extend([self.summary, ""])
        lines.append(style.heading("Usage"))
        lines.append(f"  {style.command_line('crcforge <command> [options]')}")
        lines.append(
            f"  {style.command_line('crcforge <command> -h')}   {style.dim('help and examples for a command')}"
        )
        widest = max(len(name) for _, names in self.command_groups for name in names)
        for title, names in self.command_groups:
            lines.extend(["", style.heading(title)])
            entries = [(name, self.command_help.get(name, "")) for name in names]
            lines.extend(style.rows(entries, ANSI_COMMAND, widest))
        for title, entries in self.main_sections:
            lines.extend(["", style.heading(title)])
            lines.extend(style.rows(entries, ANSI_OPTION))
        if self.examples:
            lines.extend(["", style.heading("Examples")])
            comments = self.example_comments or [""] * len(self.examples)
            for comment, example in zip(comments, self.examples, strict=True):
                if comment:
                    lines.append(f"  {style.dim('# ' + comment)}")
                lines.append(f"  {style.command_line(example)}")
        lines.extend(["", style.heading("Options")])
        lines.extend(style.rows(self._option_entries(include_groups=True), ANSI_OPTION))
        lines.extend(["", f"{style.dim('Docs')}  {DOCS_URL}"])
        return lines

    def _option_entries(self, include_groups: bool) -> list[tuple[str, str]]:
        entries = []
        for group in self._action_groups:
            for action in group._group_actions:
                if action.option_strings and action.help != argparse.SUPPRESS:
                    entries.append(_option_entry(action))
        return entries

    def _command_help(self, style: HelpStyle) -> list[str]:
        name = self.prog.split(" ", 1)[1] if " " in self.prog else self.prog
        lines = [f"{style.paint(self.prog, ANSI_COMMAND)}  {style.dim(self.summary)}" if self.summary else self.prog]
        lines.extend(["", style.heading("Usage")])
        usage_lines = (self.usage or self.prog).splitlines()
        lines.extend(f"  {style.command_line(line.strip())}" for line in usage_lines)
        if self.description:
            lines.append("")
            lines.extend(f"  {part}" for part in textwrap.wrap(self.description, style.width - 4))
        if self.argument_docs:
            lines.extend(["", style.heading("Arguments")])
            lines.extend(style.rows(self.argument_docs, ANSI_OPTION))
        option_column = 0
        groups: list[tuple[str, list[tuple[str, str]]]] = []
        for group in self._action_groups:
            entries = []
            for action in group._group_actions:
                if action.help == argparse.SUPPRESS:
                    continue
                if not action.option_strings:
                    if self.argument_docs:
                        continue
                    entries.append((str(action.metavar or action.dest), str(action.help or "")))
                else:
                    entries.append(_option_entry(action))
            if entries:
                groups.append((_section_title(group.title), entries))
                option_column = max(option_column, *(len(key) for key, _ in entries))
        # Output options are shared by every command, so they go last.
        groups.sort(key=lambda item: item[0] == "Output options")
        for title, entries in groups:
            lines.extend(["", style.heading(title)])
            lines.extend(style.rows(entries, ANSI_OPTION, option_column))
        if self.examples:
            lines.extend(["", style.heading("Examples")])
            lines.extend(f"  {style.command_line(example)}" for example in self.examples)
        for note in self.notes:
            lines.append("")
            lines.extend(f"  {style.dim(part)}" for part in textwrap.wrap(note, style.width - 4))
        lines.extend(["", style.dim(f"Docs: {DOCS_URL}commands/#{name}")])
        return lines

    # -- errors --------------------------------------------------------------------------

    def error(self, message: str) -> NoReturn:
        text, hint, usage = self._explain(message)
        self._write_error(text, hint, usage, self._error_example)
        raise SystemExit(2)

    def report(self, text: str, hint: str | None = None, with_usage: bool = False) -> None:
        """Print an error in the layout of argument errors, optionally with usage lines and an example."""
        usage = self.usage if with_usage else None
        example = self.examples[0] if with_usage and self.examples else None
        self._write_error(text, hint, usage, example)

    def _write_error(self, text: str, hint: str | None, usage: str | None, example: str | None) -> None:
        stream = sys.stderr
        if CrcforgeParser.json_errors:
            import json

            payload = {"error": text}
            payload.update(
                {key: value for key, value in (("hint", hint), ("usage", usage), ("example", example)) if value}
            )
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            return
        color = supports_color(stream) and not CrcforgeParser.no_color
        stream.write(colorize(f"error: {text}", ANSI_ERROR, color) + "\n")
        labelled = [("usage:", line.strip()) for line in (usage or "").splitlines()]
        if example:
            labelled.append(("example:", example))
        for index, (label, line) in enumerate(labelled):
            shown = label if index == 0 or labelled[index - 1][0] != label else ""
            stream.write(f"{colorize(f'{shown:<9}', ANSI_DIM, color)}{colorize(line, ANSI_COMMAND, color)}\n")
        if hint:
            label = "hint:" if not labelled else f"{'hint:':<9}"
            stream.write(colorize(f"{label}{'' if labelled else ' '}{hint}", ANSI_DIM, color) + "\n")

    def _command_for_args(self) -> CrcforgeParser | None:
        for token in self._last_args:
            if token in self.command_parsers:
                return self.command_parsers[token]
        return None

    def _explain(self, message: str) -> tuple[str, str | None, str | None]:
        """Turn an argparse message into (error, hint, usage); may set an example line."""
        self._error_example = None
        target: CrcforgeParser = self
        if self.command_parsers:
            target = self._command_for_args() or self
        help_hint = f"Run '{target.prog} -h' for all options and examples."

        match = re.search(r"argument (?P<arg>[^:]+): invalid choice: (?P<value>'[^']*'|\S+)", message)
        if match:
            value = match.group("value").strip("'")
            argument = match.group("arg")
            if argument == "<command>" or not argument.startswith("-"):
                commands = list(self.command_parsers)
                guess = difflib.get_close_matches(value, commands, n=1, cutoff=0.5)
                hint = f"Did you mean '{guess[0]}'? " if guess else ""
                return f"Unknown command '{value}'.", hint + "Run 'crcforge -h' to see all commands.", None
            action = target._find_action(argument.split("/")[0])
            choices = [str(choice) for choice in (action.choices or [])] if action else []
            guess = difflib.get_close_matches(value, choices, n=1, cutoff=0.5)
            hint = (f"Did you mean '{guess[0]}'? " if guess else "") + (
                f"Choose one of: {', '.join(choices)}." if choices else help_hint
            )
            return f"'{value}' is not a valid value for {argument.split('/')[0]}.", hint, None

        match = re.search(r"the following arguments are required: (?P<names>.+)", message)
        if match:
            names = [name.strip() for name in match.group("names").split(",")]
            if names == ["<command>"]:
                unknown = [token for token in self._last_args if token.startswith("-") and not self._find_action(token)]
                if unknown:
                    options = [option for option in self._option_string_actions if option.startswith("--")]
                    word = unknown[0].lstrip("-").lower()
                    names = [option[2:] for option in options]
                    initials = [name for name in names if len(word) == 1 and name.startswith(word)]
                    guess = initials[:1] if len(initials) == 1 else difflib.get_close_matches(word, names, 1, 0.5)
                    hint = f"Did you mean --{guess[0]}? " if guess else ""
                    return f"Unknown option {unknown[0]}.", hint + "Run 'crcforge -h' to see all commands.", None
                return "Missing command.", "Run 'crcforge -h' to see all commands.", None
            missing = target.required_text or " and ".join(f"<{name.strip('<>')}>" for name in names)
            self._error_example = target.examples[0] if target.examples else None
            return f"{target.prog} needs {missing}.", help_hint, target.usage

        match = re.search(r"unrecognized arguments: (?P<args>.+)", message)
        if match:
            tokens = match.group("args").split()
            first = tokens[0]
            if first.startswith("-"):
                options = [option for option in target._option_string_actions if option.startswith("--")]
                guess = difflib.get_close_matches(first.split("=")[0], options, n=1, cutoff=0.6)
                hint = (f"Did you mean {guess[0]}? " if guess else "") + help_hint
                where = f" for '{target.prog}'" if target is not self or not self.command_parsers else ""
                return f"Unknown option {first}{where}.", hint, None
            return f"Unexpected value '{first}'.", help_hint, target.usage

        match = re.search(r"argument (?P<arg>[^:]+): expected one argument", message)
        if match:
            option = match.group("arg").split("/")[0]
            action = target._find_action(option)
            metavar = _option_metavar(action) if action else "VALUE"
            return f"{option} needs a value.", f"For example: {option} {metavar}. {help_hint}", None

        match = re.search(
            r"argument (?P<arg>[^:]+): invalid (?P<kind>int|float) value: (?P<value>'[^']*'|\S+)", message
        )
        if match:
            option = match.group("arg").split("/")[0]
            kind = "a whole number" if match.group("kind") == "int" else "a number"
            return f"{option} needs {kind}, not {match.group('value')}.", help_hint, None

        match = re.search(r"ambiguous option: (?P<option>\S+) could match (?P<matches>.+)", message)
        if match:
            return (
                f"{match.group('option')} is ambiguous.",
                f"It could mean {match.group('matches')}; type more of the option name.",
                None,
            )

        text = message[0].upper() + message[1:]
        return (text if text.endswith(".") else text + "."), help_hint, None

    def _find_action(self, option: str) -> argparse.Action | None:
        return self._option_string_actions.get(option)
