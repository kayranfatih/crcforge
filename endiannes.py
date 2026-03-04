from __future__ import annotations

from typing import Iterable, List


BYTE_ORDER_MODES = ("native", "reverse", "swap16", "swap32", "swap64")

_BYTE_ORDER_DESCRIPTIONS = {
    "native": "Use the input bytes exactly as provided.",
    "reverse": "Reverse the full byte stream.",
    "swap16": "Reverse bytes inside each 16-bit word.",
    "swap32": "Reverse bytes inside each 32-bit word.",
    "swap64": "Reverse bytes inside each 64-bit word.",
}


def _swap_chunks(data: bytes, chunk_size: int) -> bytes:
    if chunk_size <= 0:
        raise ValueError("Chunk size must be positive.")
    if len(data) % chunk_size != 0:
        raise ValueError(
            f"Input length ({len(data)} bytes) must be a multiple of {chunk_size} bytes."
        )

    chunks: List[bytes] = []
    for index in range(0, len(data), chunk_size):
        chunks.append(data[index : index + chunk_size][::-1])
    return b"".join(chunks)


def transform_byte_order(data: bytes, mode: str) -> bytes:
    normalized = mode.strip().lower()
    if normalized == "native":
        return data
    if normalized == "reverse":
        return data[::-1]
    if normalized == "swap16":
        return _swap_chunks(data, 2)
    if normalized == "swap32":
        return _swap_chunks(data, 4)
    if normalized == "swap64":
        return _swap_chunks(data, 8)
    raise ValueError(f"Unsupported byte-order mode: {mode}")


def describe_byte_order(mode: str) -> str:
    normalized = mode.strip().lower()
    try:
        return _BYTE_ORDER_DESCRIPTIONS[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported byte-order mode: {mode}") from error


def all_byte_orders() -> Iterable[str]:
    return BYTE_ORDER_MODES
