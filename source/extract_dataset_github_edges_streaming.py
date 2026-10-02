#!/usr/bin/env python3
"""Incrementally extract Dataset-GitHub edges from the full README crawl.

Created: 2026-09-28
Version: v2026.09.28-01
Purpose: Build deduplicated Dataset-GitHub outputs during README collection.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import time
from pathlib import Path

from extract_model_github_edges_streaming import github_candidates


CHANGE_ID = "v2026.09.28-01"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_dataset_readmes_full_2026Sep28.jsonl"
DEFAULT_CRAWL_STATE = SCRIPT_DIR / "all_dataset_readmes_full_2026Sep28.jsonl.state.sqlite3"
DEFAULT_STATE = SCRIPT_DIR / "dataset_github_edges_full.state.sqlite3"
SIMPLE_FIELDS = ("source", "edge_type", "target")
DETAILED_FIELDS = SIMPLE_FIELDS + ("evidence", "confidence", "population")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream full-crawl Dataset-GitHub extraction.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--crawl-state", type=Path, default=DEFAULT_CRAWL_STATE)
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--batch-records", type=int, default=1_000)
    return parser.parse_args()


def output_paths(directory: Path) -> dict[str, Path]:
    return {
        "csv": directory / "dataset_github_edges_full.csv",
        "jsonl": directory / "dataset_github_edges_full.jsonl",
        "detailed_csv": directory / "dataset_github_edges_full_detailed.csv",
        "detailed_jsonl": directory / "dataset_github_edges_full_detailed.jsonl",
    }


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS edges("
        "source TEXT NOT NULL,repo_key TEXT NOT NULL,target TEXT NOT NULL,evidence TEXT NOT NULL,"
        "confidence TEXT NOT NULL,population TEXT NOT NULL,"
        "PRIMARY KEY(source,repo_key))"
    )
    db.commit()
    return db


def state_get(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def state_set(db: sqlite3.Connection, key: str, value: object) -> None:
    db.execute(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def crawl_value(path: Path, key: str) -> str | None:
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=60)
    try:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        db.close()


def csv_bytes(fields: tuple[str, ...], row: dict[str, str], header: bool = False) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    if header:
        writer.writeheader()
    else:
        writer.writerow({field: row[field] for field in fields})
    return stream.getvalue().encode("utf-8")


def jsonl_bytes(fields: tuple[str, ...], row: dict[str, str]) -> bytes:
    value = {field: row[field] for field in fields}
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def initialize(db: sqlite3.Connection, input_path: Path, crawl_state: Path, paths: dict[str, Path]) -> None:
    config = json.dumps(
        {
            "change_id": CHANGE_ID,
            "input": str(input_path),
            "crawl_state": str(crawl_state),
            "outputs": {key: str(value) for key, value in paths.items()},
        },
        sort_keys=True,
    )
    stored = state_get(db, "configuration")
    if stored is not None and stored != config:
        raise RuntimeError("Extractor state belongs to another configuration")
    if stored is not None:
        return
    existing = [path for path in paths.values() if path.exists() and path.stat().st_size]
    if existing:
        raise RuntimeError(f"Non-empty outputs exist without state: {existing}")
    streams = {key: path.open("w+b") for key, path in paths.items()}
    try:
        streams["csv"].write(csv_bytes(SIMPLE_FIELDS, {}, header=True))
        streams["detailed_csv"].write(csv_bytes(DETAILED_FIELDS, {}, header=True))
        for stream in streams.values():
            stream.flush()
            os.fsync(stream.fileno())
        with db:
            state_set(db, "configuration", config)
            state_set(db, "input_offset", 0)
            state_set(db, "records_processed", 0)
            state_set(db, "readmes_processed", 0)
            state_set(db, "edges", 0)
            state_set(db, "finished", 0)
            for key, stream in streams.items():
                state_set(db, f"output_offset_{key}", stream.tell())
    finally:
        for stream in streams.values():
            stream.close()


def open_outputs(db: sqlite3.Connection, paths: dict[str, Path]):
    streams = {key: path.open("r+b") for key, path in paths.items()}
    for key, stream in streams.items():
        committed = int(state_get(db, f"output_offset_{key}") or 0)
        stream.seek(0, os.SEEK_END)
        if stream.tell() < committed:
            raise RuntimeError(f"Output shorter than committed offset: {paths[key]}")
        if stream.tell() > committed:
            stream.truncate(committed)
        stream.seek(committed)
    return streams


def main() -> int:
    args = parse_args()
    if args.poll_seconds <= 0 or args.batch_records < 1:
        raise ValueError("Invalid polling or batch configuration")
    input_path = args.input.expanduser().resolve()
    crawl_state = args.crawl_state.expanduser().resolve()
    state_path = args.state_db.expanduser().resolve()
    directory = args.output_dir.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = output_paths(directory)
    if not input_path.is_file() or not crawl_state.is_file():
        raise FileNotFoundError("Dataset README input or crawler state is missing")
    db = connect(state_path)
    initialize(db, input_path, crawl_state, paths)
    streams = open_outputs(db, paths)
    input_stream = input_path.open("rb")
    try:
        while True:
            input_offset = int(state_get(db, "input_offset") or 0)
            committed = int(crawl_value(crawl_state, "output_offset") or 0)
            crawl_finished = crawl_value(crawl_state, "finished") == "1"
            if input_offset >= committed:
                if crawl_finished:
                    with db:
                        state_set(db, "finished", 1)
                    print("Extraction complete.", flush=True)
                    break
                time.sleep(args.poll_seconds)
                continue
            input_stream.seek(input_offset)
            batch: list[tuple[int, dict]] = []
            while len(batch) < args.batch_records and input_stream.tell() < committed:
                line = input_stream.readline()
                if not line or input_stream.tell() > committed:
                    break
                batch.append((input_stream.tell(), json.loads(line)))
            if not batch:
                time.sleep(args.poll_seconds)
                continue
            records_processed = int(state_get(db, "records_processed") or 0)
            readmes_processed = int(state_get(db, "readmes_processed") or 0)
            edges_count = int(state_get(db, "edges") or 0)
            db.execute("BEGIN IMMEDIATE")
            try:
                for _, record in batch:
                    records_processed += 1
                    markdown = record.get("readme")
                    if record.get("status") != "ok" or not isinstance(markdown, str):
                        continue
                    readmes_processed += 1
                    dataset_id = str(record.get("datasetId") or "").strip()
                    population = str(record.get("population") or "unknown")
                    for repo_key, (target, evidence, confidence) in github_candidates(markdown).items():
                        row = {
                            "source": f"dataset::{dataset_id}",
                            "edge_type": "links_to_github",
                            "target": target,
                            "evidence": evidence,
                            "confidence": confidence,
                            "population": population,
                        }
                        inserted = db.execute(
                            "INSERT OR IGNORE INTO edges(source,repo_key,target,evidence,confidence,population) "
                            "VALUES(?,?,?,?,?,?)",
                            (row["source"], repo_key, target, evidence, confidence, population),
                        ).rowcount
                        if not inserted:
                            continue
                        streams["csv"].write(csv_bytes(SIMPLE_FIELDS, row))
                        streams["jsonl"].write(jsonl_bytes(SIMPLE_FIELDS, row))
                        streams["detailed_csv"].write(csv_bytes(DETAILED_FIELDS, row))
                        streams["detailed_jsonl"].write(jsonl_bytes(DETAILED_FIELDS, row))
                        edges_count += 1
                for stream in streams.values():
                    stream.flush()
                    os.fsync(stream.fileno())
                state_set(db, "input_offset", batch[-1][0])
                state_set(db, "records_processed", records_processed)
                state_set(db, "readmes_processed", readmes_processed)
                state_set(db, "edges", edges_count)
                for key, stream in streams.items():
                    state_set(db, f"output_offset_{key}", stream.tell())
                db.commit()
            except Exception:
                db.rollback()
                raise
            print(
                f"records={records_processed:,} readmes={readmes_processed:,} "
                f"unique_edges={edges_count:,}",
                flush=True,
            )
    finally:
        input_stream.close()
        for stream in streams.values():
            stream.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
