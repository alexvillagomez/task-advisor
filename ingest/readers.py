"""Streaming JSONL readers, including a dependency-free libzstd binding."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
from pathlib import Path
from typing import Any, Iterator


class ReaderError(RuntimeError):
    pass


class _InBuffer(ctypes.Structure):
    _fields_ = [("src", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


class _OutBuffer(ctypes.Structure):
    _fields_ = [("dst", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


def _zstd_library() -> ctypes.CDLL:
    candidates = [ctypes.util.find_library("zstd")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\mingw64\bin\libzstd.dll",
                r"C:\Program Files\Git\mingw64\libexec\git-core\libzstd.dll",
                "libzstd.dll",
            ]
        )
    else:
        candidates.extend(["libzstd.so.1", "libzstd.so", "libzstd.dylib"])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            continue
    raise ReaderError("No zstandard decoder is available (install zstandard or libzstd)")


def _zstd_chunks(path: Path) -> Iterator[bytes]:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        zstandard = None
    if zstandard is not None:
        with path.open("rb") as source, zstandard.ZstdDecompressor().stream_reader(source) as reader:
            while chunk := reader.read(1 << 20):
                yield chunk
        return

    lib = _zstd_library()
    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_OutBuffer),
        ctypes.POINTER(_InBuffer),
    ]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
    lib.ZSTD_getErrorName.restype = ctypes.c_char_p
    lib.ZSTD_DStreamInSize.restype = ctypes.c_size_t
    lib.ZSTD_DStreamOutSize.restype = ctypes.c_size_t

    stream = lib.ZSTD_createDStream()
    if not stream:
        raise ReaderError("Could not create a zstandard stream")

    def check(code: int) -> int:
        if lib.ZSTD_isError(code):
            message = lib.ZSTD_getErrorName(code).decode("utf-8", "replace")
            raise ReaderError(f"zstandard decode failed: {message}")
        return code

    try:
        check(lib.ZSTD_initDStream(stream))
        in_size = max(131072, int(lib.ZSTD_DStreamInSize()))
        out_size = max(131072, int(lib.ZSTD_DStreamOutSize()))
        with path.open("rb") as source:
            remaining = 1
            while data := source.read(in_size):
                input_array = ctypes.create_string_buffer(data)
                input_buffer = _InBuffer(ctypes.addressof(input_array), len(data), 0)
                while input_buffer.pos < input_buffer.size:
                    output_array = ctypes.create_string_buffer(out_size)
                    output_buffer = _OutBuffer(ctypes.addressof(output_array), out_size, 0)
                    remaining = check(
                        lib.ZSTD_decompressStream(
                            stream, ctypes.byref(output_buffer), ctypes.byref(input_buffer)
                        )
                    )
                    if output_buffer.pos:
                        yield output_array.raw[: output_buffer.pos]
            if remaining != 0:
                raise ReaderError("Truncated zstandard stream")
    finally:
        lib.ZSTD_freeDStream(stream)


def iter_text_lines(path: Path) -> Iterator[str]:
    if path.suffix.casefold() != ".zst":
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield from handle
        return
    pending = b""
    for chunk in _zstd_chunks(path):
        parts = (pending + chunk).split(b"\n")
        pending = parts.pop()
        for line in parts:
            yield (line + b"\n").decode("utf-8", "replace")
    if pending:
        yield pending.decode("utf-8", "replace")


def iter_jsonl(path: Path, diagnostics: dict[str, int] | None = None) -> Iterator[dict[str, Any]]:
    for line in iter_text_lines(path):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
            if diagnostics is not None:
                diagnostics["parse_errors"] = diagnostics.get("parse_errors", 0) + 1
            continue
        if isinstance(value, dict):
            yield value
