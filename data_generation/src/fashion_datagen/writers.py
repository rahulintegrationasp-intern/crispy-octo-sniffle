"""Streaming writers for JSONL (+ optional Parquet) and committed samples.

Memory-safe by construction: rows are buffered in chunks (default 5k) and each
chunk is flushed to disk immediately; Parquet row-groups mirror the chunks.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import TracebackType
from typing import Any

INTERNAL_FIELDS = {"_quality"}


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Drop generator-internal fields before writing."""
    return {k: v for k, v in row.items() if k not in INTERNAL_FIELDS}


class JsonlWriter:
    """Append-only JSONL writer with chunked buffering."""

    def __init__(self, path: Path | str, chunk_size: int = 5000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.chunk_size = chunk_size
        self._fh = self.path.open("w", encoding="utf-8")
        self._buf: list[str] = []
        self.n_rows = 0

    def write(self, row: dict[str, Any]) -> None:
        self._buf.append(json.dumps(clean_row(row), ensure_ascii=False))
        self.n_rows += 1
        if len(self._buf) >= self.chunk_size:
            self.flush()

    def flush(self) -> None:
        if self._buf:
            self._fh.write("\n".join(self._buf) + "\n")
            self._buf.clear()
            self._fh.flush()

    def close(self) -> None:
        self.flush()
        self._fh.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


class ParquetWriter:
    """Optional Parquet twin of a JSONL stream (needs pyarrow)."""

    def __init__(self, path: Path | str, schema_hint: list[str] | None = None) -> None:
        import pyarrow as pa  # noqa: PLC0415 - optional dependency
        import pyarrow.parquet as pq  # noqa: PLC0415

        self._pa = pa
        self._pq = pq
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._writer: Any = None
        self.n_rows = 0

    def write_table(self, rows: list[dict[str, Any]]) -> None:
        table = self._pa.Table.from_pylist(rows)
        if self._writer is None:
            self._writer = self._pq.ParquetWriter(self.path, table.schema)
        else:
            # align schemas across row groups
            table = table.select([c for c in self._writer.schema.names if c in table.column_names]) \
                if set(table.column_names) != set(self._writer.schema.names) else table
        self._writer.write_table(table)
        self.n_rows += len(rows)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()

    def __enter__(self) -> "ParquetWriter":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


def write_samples(products: list[dict[str, Any]], reviews: list[dict[str, Any]], samples_dir: Path | str, n_products: int = 100) -> None:
    """Persist the first ``n_products`` products (and their reviews) as committed samples."""
    d = Path(samples_dir)
    d.mkdir(parents=True, exist_ok=True)
    prods = [clean_row(p) for p in products[:n_products]]
    keep_ids = {p["product_id"] for p in prods}
    revs = [clean_row(r) for r in reviews if r["product_id"] in keep_ids]
    with (d / "products_sample.jsonl").open("w", encoding="utf-8") as fh:
        for p in prods:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    with (d / "reviews_sample.jsonl").open("w", encoding="utf-8") as fh:
        for r in revs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read an entire JSONL file (for samples/tests only — not for raw data)."""
    out = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def copy_if_exists(src: Path, dst: Path) -> bool:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return True
    return False
