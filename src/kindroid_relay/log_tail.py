from __future__ import annotations

from pathlib import Path

MAX_LOG_TAIL_BYTES = 2 * 1024 * 1024
LOG_READ_CHUNK_BYTES = 64 * 1024


def read_log_tail(path: Path, line_limit: int) -> tuple[list[str], bool]:
    """Read only a bounded suffix, dropping any partial first line."""
    if not 1 <= line_limit <= 500:
        raise ValueError("log line limit must be between 1 and 500")
    try:
        with path.open("rb") as handle:
            position = handle.seek(0, 2)
            chunks: list[bytes] = []
            newline_count = bytes_read = 0
            while position > 0 and newline_count <= line_limit and bytes_read < MAX_LOG_TAIL_BYTES:
                chunk_size = min(LOG_READ_CHUNK_BYTES, position, MAX_LOG_TAIL_BYTES - bytes_read)
                position -= chunk_size
                handle.seek(position)
                chunk = handle.read(chunk_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
                bytes_read += len(chunk)
    except FileNotFoundError:
        return [], False
    values = b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()
    if position > 0:
        values = values[1:]
    selected = values[-line_limit:]
    return selected, position > 0 or len(values) > len(selected)
