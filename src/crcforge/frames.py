"""Split captured frames into data and an embedded checksum."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .solver import Sample

POSITIONS = ("end", "start")
DEFAULT_FRAME_WIDTHS = (8, 16, 32)


@dataclass(frozen=True)
class FrameLayout:
    """Where the checksum sits inside a frame.

    ``skip`` header bytes (sync words, addresses...) are excluded from the checksummed data.
    """

    width: int
    position: str = "end"
    endian: str = "big"
    skip: int = 0

    @property
    def checksum_bytes(self) -> int:
        return (self.width + 7) // 8

    def split(self, frame: bytes) -> Sample:
        size = self.checksum_bytes
        if len(frame) < size + self.skip:
            raise ValueError(
                f"Frame of {len(frame)} byte{'s' if len(frame) != 1 else ''} is too short for a {size}-byte checksum"
                + (f" and {self.skip} skipped bytes." if self.skip else ".")
            )
        if self.position == "end":
            raw, data = frame[len(frame) - size :], frame[self.skip : len(frame) - size]
        elif self.position == "start":
            raw, data = frame[:size], frame[size + self.skip :]
        else:
            raise ValueError(f"Checksum position must be one of: {', '.join(POSITIONS)}.")
        value = int.from_bytes(raw, "little" if self.endian == "little" else "big")
        if value >> self.width:
            raise ValueError(f"Checksum bytes {raw.hex().upper()} do not fit in {self.width} bits.")
        return Sample(data, value)

    def split_all(self, frames: Iterable[bytes]) -> list[Sample]:
        return [self.split(frame) for frame in frames]

    def describe(self) -> str:
        parts = [f"{self.width}-bit checksum at the {self.position}"]
        if self.checksum_bytes > 1:
            parts.append(f"{self.endian}-endian")
        if self.skip:
            parts.append(f"data skips {self.skip} header byte{'s' if self.skip != 1 else ''}")
        return ", ".join(parts)

    def as_dict(self) -> dict[str, object]:
        return {"width": self.width, "position": self.position, "endian": self.endian, "skip": self.skip}


def candidate_layouts(
    widths: Sequence[int],
    positions: Sequence[str],
    endians: Sequence[str],
    skips: Sequence[int],
) -> list[FrameLayout]:
    layouts = []
    for width in widths:
        for position in positions:
            for endian in endians if (width + 7) // 8 > 1 else ("big",):
                for skip in skips:
                    layouts.append(FrameLayout(width, position, endian, skip))
    return layouts
