#!/usr/bin/env python3
"""Download Hugging Face model-card (README YAML) metadata in bulk.

The CSV is the authoritative whitelist of model IDs. Instead of downloading
README.md once per model, this script walks the paginated Hub models API with
``cardData=true``. Hugging Face returns the parsed YAML metadata from each
model card in ``cardData``. Matching records are written as JSON Lines.

The adjacent SQLite state database contains only the CSV whitelist and resume
state. It lets an interrupted crawl continue from the saved pagination URL
without holding roughly three million model IDs in memory.

Change history:
2026-09-01 v2026.09.01-01
- Added bulk, authenticated cardData collection from the Hub models API.
- Added disk-backed CSV filtering, durable JSONL output, and exact resume state.
- Backup: download_model_metadata.py.bak.20260901-165207
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from huggingface_hub import get_token
from tqdm import tqdm


CHANGE_ID = "v2026.09.01-01"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_huggingface_models_2026Aug28.csv"
DEFAULT_OUTPUT = SCRIPT_DIR / "all_model_readme_metadata_2026Aug28.jsonl"
API_URL = "https://huggingface.co/api/models"
TRANSIENT_STATUS_CODES = {500, 502, 503, 504}
INDEX_BATCH_SIZE = 10_000
MISSING_BATCH_SIZE = 1_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect parsed README/model-card metadata for model IDs in a CSV. "
            "Output is resumable JSON Lines (one JSON object per model)."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--state-db",
        type=Path,
        help="Resume database (default: <output>.state.sqlite3)",
    )
    parser.add_argument("--start", type=int, default=0, help="First CSV data row")
    parser.add_argument(
        "--end",
        type=int,
        default=0,
        help="Exclusive final CSV data row; 0 means all remaining rows",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=0.5,
        help="Minimum seconds between Hub API page requests",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Maximum API pages in this invocation; 0 means no limit",
    )
    return parser.parse_args()


def default_state_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.name + ".state.sqlite3")


def validate_args(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path, int | None]:
    input_path = args.input.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    state_path = (
        args.state_db.expanduser().resolve()
        if args.state_db
        else default_state_path(output_path)
    )
    end_row = None if args.end == 0 else args.end

    if not input_path.is_file():
        raise FileNotFoundError(f"Input CSV does not exist: {input_path}")
    if args.start < 0:
        raise ValueError("--start must be zero or greater")
    if end_row is not None and end_row <= args.start:
        raise ValueError("--end must exceed --start, or be 0 for no limit")
    if args.request_interval < 0:
        raise ValueError("--request-interval cannot be negative")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than zero")
    if args.max_retries < 0:
        raise ValueError("--max-retries cannot be negative")
    if args.max_pages < 0:
        raise ValueError("--max-pages cannot be negative")
    if len({input_path, output_path, state_path}) != 3:
        raise ValueError("Input, output, and state database paths must differ")

    return input_path, output_path, state_path, end_row


def state_get(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM state WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else str(row[0])


def state_set(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        """
        INSERT INTO state(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def connect_state(state_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(state_path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS wanted_models(
            model_id TEXT PRIMARY KEY,
            row_index INTEGER NOT NULL,
            emitted INTEGER NOT NULL DEFAULT 0 CHECK(emitted IN (0, 1))
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS wanted_models_emitted_idx "
        "ON wanted_models(emitted, row_index)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def input_configuration(
    input_path: Path,
    output_path: Path,
    start_row: int,
    end_row: int | None,
) -> str:
    stat = input_path.stat()
    return json.dumps(
        {
            "input_path": str(input_path),
            "input_size": stat.st_size,
            "input_mtime_ns": stat.st_mtime_ns,
            "output_path": str(output_path),
            "start_row": start_row,
            "end_row": end_row,
        },
        sort_keys=True,
    )


def build_or_validate_whitelist(
    connection: sqlite3.Connection,
    input_path: Path,
    output_path: Path,
    start_row: int,
    end_row: int | None,
) -> int:
    configuration = input_configuration(
        input_path, output_path, start_row, end_row
    )
    stored_configuration = state_get(connection, "configuration")

    if stored_configuration is not None and stored_configuration != configuration:
        raise RuntimeError(
            "The state database belongs to a different input, output, or row "
            "range. Choose a different --state-db path."
        )

    if state_get(connection, "index_complete") == "1":
        count = state_get(connection, "wanted_count")
        if count is None:
            raise RuntimeError("State database is missing wanted_count")
        return int(count)

    if output_path.exists() and output_path.stat().st_size > 0:
        raise RuntimeError(
            "A non-empty output exists without a complete matching state index: "
            f"{output_path}"
        )

    with connection:
        connection.execute("DELETE FROM wanted_models")
        connection.execute("DELETE FROM state")
        state_set(connection, "configuration", configuration)

    inserted = 0
    batch: list[tuple[str, int]] = []
    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV has no header: {input_path}")
        field_lookup = {name.strip().lower(): name for name in reader.fieldnames}
        model_column = field_lookup.get("modelid") or field_lookup.get("model_id")
        if model_column is None:
            raise ValueError(
                f"Input CSV must contain modelId; found {reader.fieldnames}"
            )

        for row_index, row in enumerate(reader):
            if row_index < start_row:
                continue
            if end_row is not None and row_index >= end_row:
                break
            model_id = (row.get(model_column) or "").strip()
            if not model_id:
                continue
            batch.append((model_id, row_index))
            if len(batch) >= INDEX_BATCH_SIZE:
                before = connection.total_changes
                with connection:
                    connection.executemany(
                        "INSERT OR IGNORE INTO wanted_models(model_id, row_index) "
                        "VALUES(?, ?)",
                        batch,
                    )
                inserted += connection.total_changes - before
                batch.clear()

    if batch:
        before = connection.total_changes
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO wanted_models(model_id, row_index) VALUES(?, ?)",
                batch,
            )
        inserted += connection.total_changes - before

    wanted_count = int(
        connection.execute("SELECT COUNT(*) FROM wanted_models").fetchone()[0]
    )
    with connection:
        state_set(connection, "wanted_count", wanted_count)
        state_set(connection, "output_offset", 0)
        state_set(connection, "next_url", API_URL)
        state_set(connection, "first_page", 1)
        state_set(connection, "crawl_complete", 0)
        state_set(connection, "finished", 0)
        state_set(connection, "index_complete", 1)

    print(
        f"Indexed {wanted_count:,} unique model IDs from the CSV "
        f"({inserted:,} inserted)."
    )
    return wanted_count


def reconcile_output(
    connection: sqlite3.Connection, output_path: Path
):
    confirmed_offset = int(state_get(connection, "output_offset") or "0")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_file = output_path.open("a+b")
    output_file.seek(0, os.SEEK_END)
    actual_size = output_file.tell()

    if actual_size < confirmed_offset:
        output_file.close()
        raise RuntimeError(
            f"Output is shorter than its confirmed state: {actual_size} < "
            f"{confirmed_offset}. Restore the matching output or use new paths."
        )
    if actual_size > confirmed_offset:
        # Bytes beyond the committed offset came from an interrupted page write.
        output_file.truncate(confirmed_offset)
        output_file.flush()
        os.fsync(output_file.fileno())
    output_file.seek(confirmed_offset)
    return output_file, confirmed_offset


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def encode_record(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            json_safe(record),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def append_committed_batch(
    connection: sqlite3.Connection,
    output_file,
    confirmed_offset: int,
    records: Iterable[dict[str, Any]],
    emitted_ids: list[str],
    state_updates: dict[str, Any],
) -> int:
    payload = b"".join(encode_record(record) for record in records)
    output_file.seek(confirmed_offset)
    if payload:
        output_file.write(payload)
        output_file.flush()
        os.fsync(output_file.fileno())
    new_offset = confirmed_offset + len(payload)

    with connection:
        if emitted_ids:
            connection.executemany(
                "UPDATE wanted_models SET emitted = 1 WHERE model_id = ?",
                ((model_id,) for model_id in emitted_ids),
            )
        state_set(connection, "output_offset", new_offset)
        for key, value in state_updates.items():
            state_set(connection, key, value)

    return new_offset


def rate_limit_wait_seconds(response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return max(1.0, float(retry_after)) + 1.0
        except ValueError:
            try:
                reset_at = parsedate_to_datetime(retry_after)
                if reset_at.tzinfo is None:
                    reset_at = reset_at.replace(tzinfo=timezone.utc)
                seconds = (reset_at - datetime.now(timezone.utc)).total_seconds()
                return max(1.0, seconds) + 1.0
            except (TypeError, ValueError, OverflowError):
                pass

    for header_name in ("RateLimit-Reset", "X-RateLimit-Reset"):
        raw_value = response.headers.get(header_name, "").strip()
        if not raw_value:
            continue
        try:
            value = float(raw_value)
            if value > 10_000_000_000:
                value /= 1000.0
            if value > time.time():
                return max(1.0, value - time.time()) + 1.0
            return max(1.0, value) + 1.0
        except ValueError:
            pass

    rate_limit = response.headers.get("RateLimit", "")
    match = re.search(r"(?:reset|t)\s*=\s*(\d+)", rate_limit, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 1.0
    return 310.0


def request_page(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None,
    request_interval: float,
    timeout: float,
    max_retries: int,
    last_request_at: float,
) -> tuple[requests.Response, float]:
    transient_attempts = 0
    while True:
        delay = request_interval - (time.monotonic() - last_request_at)
        if delay > 0:
            time.sleep(delay)
        last_request_at = time.monotonic()

        try:
            response = session.get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            transient_attempts += 1
            if transient_attempts > max_retries:
                raise RuntimeError(
                    f"Request failed after {max_retries} retries: {url}"
                ) from exc
            time.sleep(min(60.0, 2.0**transient_attempts))
            continue

        if response.status_code == 429:
            wait_seconds = rate_limit_wait_seconds(response)
            tqdm.write(
                f"Rate limited by Hugging Face; waiting {wait_seconds:.0f} seconds."
            )
            time.sleep(wait_seconds)
            continue

        if response.status_code in TRANSIENT_STATUS_CODES:
            transient_attempts += 1
            if transient_attempts <= max_retries:
                time.sleep(min(60.0, 2.0**transient_attempts))
                continue

        if response.status_code == 401:
            raise RuntimeError(
                "Hugging Face rejected the token. Run `hf auth login` and retry."
            )
        response.raise_for_status()
        return response, last_request_at


def metadata_record(item: dict[str, Any], model_id: str) -> dict[str, Any]:
    card_data = item.get("cardData")
    if card_data is None:
        card_data = item.get("card_data")
    return {
        "modelId": model_id,
        "status": "ok" if card_data else "no_metadata",
        "readme_metadata": card_data if card_data is not None else {},
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "https://huggingface.co/api/models?cardData=true",
    }


def crawl_api(
    connection: sqlite3.Connection,
    output_file,
    confirmed_offset: int,
    session: requests.Session,
    args: argparse.Namespace,
    progress: tqdm,
) -> tuple[int, int]:
    if state_get(connection, "crawl_complete") == "1":
        return confirmed_offset, 0

    url = state_get(connection, "next_url") or API_URL
    first_page = state_get(connection, "first_page") != "0"
    last_request_at = 0.0
    pages_this_run = 0

    while True:
        params = {"cardData": "true"} if first_page else None
        response, last_request_at = request_page(
            session,
            url,
            params,
            args.request_interval,
            args.timeout,
            args.max_retries,
            last_request_at,
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected models API response at {url}")

        records: list[dict[str, Any]] = []
        emitted_ids: list[str] = []
        page_seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            model_id = item.get("modelId") or item.get("id")
            if not isinstance(model_id, str) or not model_id or model_id in page_seen:
                continue
            page_seen.add(model_id)
            row = connection.execute(
                "SELECT emitted FROM wanted_models WHERE model_id = ?", (model_id,)
            ).fetchone()
            if row is None or int(row[0]) == 1:
                continue
            records.append(metadata_record(item, model_id))
            emitted_ids.append(model_id)

        next_link = response.links.get("next", {}).get("url")
        crawl_complete = not bool(next_link)
        confirmed_offset = append_committed_batch(
            connection,
            output_file,
            confirmed_offset,
            records,
            emitted_ids,
            {
                "next_url": str(next_link) if next_link else "",
                "first_page": 0,
                "crawl_complete": int(crawl_complete),
            },
        )
        progress.update(len(emitted_ids))
        pages_this_run += 1

        if crawl_complete:
            break
        if args.max_pages and pages_this_run >= args.max_pages:
            tqdm.write(
                f"Stopped after --max-pages={args.max_pages}; rerun the same "
                "command to resume."
            )
            break
        url = str(next_link)
        first_page = False

    return confirmed_offset, pages_this_run


def emit_missing_models(
    connection: sqlite3.Connection,
    output_file,
    confirmed_offset: int,
    progress: tqdm,
) -> int:
    if state_get(connection, "crawl_complete") != "1":
        return confirmed_offset

    while True:
        rows = connection.execute(
            """
            SELECT model_id FROM wanted_models
            WHERE emitted = 0
            ORDER BY row_index
            LIMIT ?
            """,
            (MISSING_BATCH_SIZE,),
        ).fetchall()
        if not rows:
            break

        retrieved_at = datetime.now(timezone.utc).isoformat()
        emitted_ids = [str(row[0]) for row in rows]
        records = [
            {
                "modelId": model_id,
                "status": "not_returned_by_models_api",
                "readme_metadata": None,
                "retrieved_at_utc": retrieved_at,
                "source": "https://huggingface.co/api/models?cardData=true",
            }
            for model_id in emitted_ids
        ]
        confirmed_offset = append_committed_batch(
            connection,
            output_file,
            confirmed_offset,
            records,
            emitted_ids,
            {},
        )
        progress.update(len(emitted_ids))

    with connection:
        state_set(connection, "finished", 1)
    return confirmed_offset


def run(args: argparse.Namespace) -> None:
    input_path, output_path, state_path, end_row = validate_args(args)
    token = get_token()
    if not token:
        raise RuntimeError(
            "No Hugging Face token found. Run `hf auth login` before this script."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_state(state_path)
    output_file = None
    try:
        wanted_count = build_or_validate_whitelist(
            connection, input_path, output_path, args.start, end_row
        )
        output_file, confirmed_offset = reconcile_output(connection, output_path)
        emitted_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM wanted_models WHERE emitted = 1"
            ).fetchone()[0]
        )

        if state_get(connection, "finished") == "1":
            print(
                f"Already complete: {emitted_count:,}/{wanted_count:,} records in "
                f"{output_path}"
            )
            return

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": f"hf-readme-metadata-collector/{CHANGE_ID}",
            }
        )

        with tqdm(
            total=wanted_count,
            initial=emitted_count,
            desc="Model metadata",
            unit="model",
        ) as progress:
            confirmed_offset, pages = crawl_api(
                connection,
                output_file,
                confirmed_offset,
                session,
                args,
                progress,
            )
            if state_get(connection, "crawl_complete") == "1":
                confirmed_offset = emit_missing_models(
                    connection, output_file, confirmed_offset, progress
                )

        final_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM wanted_models WHERE emitted = 1"
            ).fetchone()[0]
        )
        if state_get(connection, "finished") == "1":
            print(
                f"Complete: {final_count:,} JSON records written to {output_path}"
            )
        else:
            print(
                f"Paused after {pages:,} API pages this invocation: "
                f"{final_count:,}/{wanted_count:,} records written."
            )
            print("Rerun the identical command to resume.")
        print(f"Resume state: {state_path}")
    finally:
        if output_file is not None:
            output_file.close()
        connection.close()


def main() -> int:
    try:
        run(parse_args())
    except KeyboardInterrupt:
        print("Interrupted. Rerun the same command to resume from committed output.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
