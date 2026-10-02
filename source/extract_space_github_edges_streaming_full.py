#!/usr/bin/env python3
"""Stream Space-GitHub edges from the full Space README crawl.

Created: 2026-09-28
Version: v2026.09.28-04
Purpose: Add README-body Space-GitHub evidence with canonical URL targets.

Change history:
2026-09-30 v2026.09.30-01
- Replaced embedded NUL characters before persisting edge text so CSV output
  remains serializable during checkpointed extraction.
- Backup: extract_space_github_edges_streaming_full.py.bak.20260930-173616
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import time
from pathlib import Path

from extract_model_github_edges_streaming import github_candidates


CHANGE_ID = "v2026.09.28-04"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_space_readmes_full_2026Sep28.jsonl"
DEFAULT_CRAWL_STATE = SCRIPT_DIR / "all_space_readmes_full_2026Sep28.jsonl.state.sqlite3"
DEFAULT_STATE = SCRIPT_DIR / "space_github_edges_full.state.sqlite3"
SIMPLE_FIELDS = ("source", "edge_type", "target")
DETAILED_FIELDS = SIMPLE_FIELDS + ("evidence", "confidence", "population")


def clean_text(value: object) -> str:
    """Return text that is safe and consistent across SQLite, CSV, and JSONL."""
    return str(value).replace("\x00", " ")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stream full-crawl Space-GitHub extraction.")
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--crawl-state", type=Path, default=DEFAULT_CRAWL_STATE)
    p.add_argument("--state-db", type=Path, default=DEFAULT_STATE)
    p.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    p.add_argument("--poll-seconds", type=float, default=10.0)
    p.add_argument("--batch-records", type=int, default=1_000)
    return p.parse_args()


def output_paths(directory: Path) -> dict[str, Path]:
    return {
        "csv": directory / "space_github_edges_full.csv",
        "jsonl": directory / "space_github_edges_full.jsonl",
        "detailed_csv": directory / "space_github_edges_full_detailed.csv",
        "detailed_jsonl": directory / "space_github_edges_full_detailed.jsonl",
    }


def state_get(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def state_set(db: sqlite3.Connection, key: str, value: object) -> None:
    db.execute(
        "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS edges("
        "source TEXT NOT NULL,repo_key TEXT NOT NULL,target TEXT NOT NULL,evidence TEXT NOT NULL,"
        "confidence TEXT NOT NULL,population TEXT NOT NULL,PRIMARY KEY(source,repo_key))"
    )
    db.commit()
    return db


def crawl_value(path: Path, key: str) -> str | None:
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=60)
    try:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        db.close()


def initialize(db: sqlite3.Connection, input_path: Path, crawl_state: Path, paths: dict[str, Path]) -> None:
    config = json.dumps(
        {"change_id": CHANGE_ID, "input": str(input_path), "crawl_state": str(crawl_state),
         "outputs": {key: str(value) for key, value in paths.items()}},
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
    streams = {key: path.open("w", newline="" if "csv" in key else None, encoding="utf-8") for key, path in paths.items()}
    try:
        csv.DictWriter(streams["csv"], fieldnames=SIMPLE_FIELDS).writeheader()
        csv.DictWriter(streams["detailed_csv"], fieldnames=DETAILED_FIELDS).writeheader()
        for stream in streams.values():
            stream.flush(); os.fsync(stream.fileno())
        with db:
            state_set(db, "configuration", config); state_set(db, "input_offset", 0)
            state_set(db, "records_processed", 0); state_set(db, "readmes_processed", 0)
            state_set(db, "edges", 0); state_set(db, "finished", 0)
            for key, stream in streams.items(): state_set(db, f"output_offset_{key}", stream.tell())
    finally:
        for stream in streams.values(): stream.close()


def open_outputs(db: sqlite3.Connection, paths: dict[str, Path]):
    streams = {key: path.open("r+", newline="" if "csv" in key else None, encoding="utf-8") for key, path in paths.items()}
    for key, stream in streams.items():
        committed = int(state_get(db, f"output_offset_{key}") or 0)
        stream.seek(0, os.SEEK_END)
        if stream.tell() < committed: raise RuntimeError(f"Output shorter than state: {paths[key]}")
        if stream.tell() > committed: stream.truncate(committed)
        stream.seek(committed)
    return streams


def main() -> int:
    args = parse_args()
    input_path=args.input.expanduser().resolve(); crawl_state=args.crawl_state.expanduser().resolve()
    state_path=args.state_db.expanduser().resolve(); directory=args.output_dir.expanduser().resolve()
    if args.poll_seconds <= 0 or args.batch_records < 1: raise ValueError("Invalid arguments")
    if not input_path.is_file() or not crawl_state.is_file(): raise FileNotFoundError("Crawl input/state missing")
    directory.mkdir(parents=True,exist_ok=True); paths=output_paths(directory); db=connect(state_path)
    initialize(db,input_path,crawl_state,paths); streams=open_outputs(db,paths); raw=input_path.open("rb")
    csv_writer=csv.DictWriter(streams["csv"],fieldnames=SIMPLE_FIELDS,lineterminator="\n")
    detailed_writer=csv.DictWriter(streams["detailed_csv"],fieldnames=DETAILED_FIELDS,lineterminator="\n")
    try:
        while True:
            offset=int(state_get(db,"input_offset") or 0); committed=int(crawl_value(crawl_state,"output_offset") or 0)
            finished=crawl_value(crawl_state,"finished")=="1"
            if offset>=committed:
                if finished:
                    with db: state_set(db,"finished",1)
                    print("Extraction complete.",flush=True); break
                time.sleep(args.poll_seconds); continue
            raw.seek(offset); batch=[]
            while len(batch)<args.batch_records and raw.tell()<committed:
                line=raw.readline()
                if not line or raw.tell()>committed: break
                batch.append((raw.tell(),json.loads(line)))
            if not batch: time.sleep(args.poll_seconds); continue
            records=int(state_get(db,"records_processed") or 0); readmes=int(state_get(db,"readmes_processed") or 0); edges=int(state_get(db,"edges") or 0)
            db.execute("BEGIN IMMEDIATE")
            try:
                for _,record in batch:
                    records+=1; markdown=record.get("readme")
                    if record.get("status")!="ok" or not isinstance(markdown,str): continue
                    readmes+=1; space_id=clean_text(record.get("spaceId") or record.get("datasetId") or "").strip()
                    population=clean_text(record.get("population") or "unknown")
                    for repo_key,(target,evidence,confidence) in github_candidates(markdown).items():
                        repo_key=clean_text(repo_key); target=clean_text(target)
                        evidence=clean_text(evidence); confidence=clean_text(confidence)
                        source=f"space::{space_id}"
                        inserted=db.execute(
                            "INSERT OR IGNORE INTO edges(source,repo_key,target,evidence,confidence,population) VALUES(?,?,?,?,?,?)",
                            (source,repo_key,target,evidence,confidence,population),
                        ).rowcount
                        if not inserted: continue
                        simple={"source":source,"edge_type":"links_to_github","target":target}
                        detailed={**simple,"evidence":evidence,"confidence":confidence,"population":population}
                        csv_writer.writerow(simple); detailed_writer.writerow(detailed)
                        streams["jsonl"].write(json.dumps(simple,ensure_ascii=False,separators=(",",":"))+"\n")
                        streams["detailed_jsonl"].write(json.dumps(detailed,ensure_ascii=False,separators=(",",":"))+"\n")
                        edges+=1
                for stream in streams.values(): stream.flush(); os.fsync(stream.fileno())
                state_set(db,"input_offset",batch[-1][0]); state_set(db,"records_processed",records)
                state_set(db,"readmes_processed",readmes); state_set(db,"edges",edges)
                for key,stream in streams.items(): state_set(db,f"output_offset_{key}",stream.tell())
                db.commit()
            except Exception:
                db.rollback(); raise
            print(f"records={records:,} readmes={readmes:,} unique_edges={edges:,}",flush=True)
    finally:
        raw.close()
        for stream in streams.values(): stream.close()
        db.close()
    return 0


if __name__=="__main__": raise SystemExit(main())
