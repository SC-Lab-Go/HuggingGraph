#!/usr/bin/env python3
"""Build and merge README-derived model library, license, and task edges.

Created: 2026-09-28
Version: v2026.09.28-02
Purpose: Persist reproducible attribute subgraphs from full model READMEs.

Only YAML front matter is parsed. Existing metadata-derived graph files are
read as evidence and are never overwritten. SQLite provides exact scan resume
and case-independent edge deduplication through normalized target identifiers.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

import yaml
from huggingface_hub.repocard import REGEX_YAML_BLOCK

from model_library_edges import candidate_libraries
from model_license_task_github_edges import candidate_licenses, candidate_tasks


CHANGE_ID = "v2026.09.28-02"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_model_readmes_full_2026Sep25.jsonl"
DEFAULT_STATE = SCRIPT_DIR / "model_readme_attribute_subgraphs_v2.state.sqlite3"
SNAPSHOT = "full_model_readmes_2026Sep25"
RELATIONS = {
    "library": {
        "edge_type": "uses_library",
        "prefix": "library::",
        "metadata": SCRIPT_DIR / "model_library_edges_detailed.csv",
    },
    "license": {
        "edge_type": "has_license",
        "prefix": "license::",
        "metadata": SCRIPT_DIR / "model_license_edges_detailed.csv",
    },
    "task": {
        "edge_type": "performs_task",
        "prefix": "task::",
        "metadata": SCRIPT_DIR / "model_task_edges_detailed.csv",
    },
}
EXTRACTORS = {
    "library": candidate_libraries,
    "license": candidate_licenses,
    "task": candidate_tasks,
}
SIMPLE_FIELDS = ("source", "edge_type", "target")
README_DETAIL_FIELDS = SIMPLE_FIELDS + ("evidence", "confidence", "evidence_source", "snapshot")
MERGED_DETAIL_FIELDS = SIMPLE_FIELDS + (
    "evidence_sources",
    "metadata_evidence",
    "metadata_confidence",
    "readme_evidence",
    "readme_confidence",
    "readme_snapshot",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build README model attribute subgraphs.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    parser.add_argument("--commit-records", type=int, default=2_000)
    parser.add_argument("--max-records", type=int, default=0)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS edges("
        "relation TEXT NOT NULL,source TEXT NOT NULL,edge_type TEXT NOT NULL,target TEXT NOT NULL,"
        "readme_evidence TEXT,readme_confidence TEXT,metadata_evidence TEXT,metadata_confidence TEXT,"
        "PRIMARY KEY(relation,source,target))"
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


def initialize(db: sqlite3.Connection, input_path: Path, output_dir: Path) -> None:
    stat = input_path.stat()
    config = json.dumps(
        {
            "change_id": CHANGE_ID,
            "input": str(input_path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "output_dir": str(output_dir),
            "metadata": {key: str(value["metadata"]) for key, value in RELATIONS.items()},
        },
        sort_keys=True,
    )
    stored = state_get(db, "configuration")
    if stored is not None and stored != config:
        raise RuntimeError("State database belongs to another configuration")
    if stored is not None:
        return
    with db:
        state_set(db, "configuration", config)
        state_set(db, "input_offset", 0)
        state_set(db, "raw_records", 0)
        state_set(db, "readable_records", 0)
        state_set(db, "yaml_records", 0)
        state_set(db, "no_yaml", 0)
        state_set(db, "invalid_yaml", 0)
        state_set(db, "empty_yaml", 0)
        state_set(db, "readme_complete", 0)
        state_set(db, "export_complete", 0)


def scan_readmes(db: sqlite3.Connection, input_path: Path, commit_records: int, max_records: int) -> None:
    if state_get(db, "readme_complete") == "1":
        return
    offset = int(state_get(db, "input_offset") or 0)
    counts = Counter(
        {
            key: int(state_get(db, key) or 0)
            for key in ("raw_records", "readable_records", "yaml_records", "no_yaml", "invalid_yaml", "empty_yaml")
        }
    )
    pending: list[tuple[str, str, str, str, str, str]] = []
    processed_this_run = 0
    with input_path.open("rb") as stream:
        stream.seek(offset)
        while max_records == 0 or processed_this_run < max_records:
            line = stream.readline()
            if not line:
                with db:
                    state_set(db, "readme_complete", 1)
                break
            counts["raw_records"] += 1
            processed_this_run += 1
            record = json.loads(line)
            if record.get("status") == "ok" and isinstance(record.get("readme"), str):
                counts["readable_records"] += 1
                match = REGEX_YAML_BLOCK.search(record["readme"])
                if not match:
                    counts["no_yaml"] += 1
                else:
                    try:
                        metadata = yaml.safe_load(match.group(2))
                    except Exception:
                        counts["invalid_yaml"] += 1
                        metadata = None
                    if isinstance(metadata, dict) and metadata:
                        counts["yaml_records"] += 1
                        source = f"model::{str(record.get('modelId') or '').strip()}"
                        for relation, extractor in EXTRACTORS.items():
                            config = RELATIONS[relation]
                            for target, (evidence, confidence) in extractor(metadata).items():
                                pending.append(
                                    (
                                        relation,
                                        source,
                                        str(config["edge_type"]),
                                        f"{config['prefix']}{target}",
                                        evidence,
                                        confidence,
                                    )
                                )
                    elif metadata is not None:
                        counts["empty_yaml"] += 1
            if processed_this_run % commit_records == 0:
                with db:
                    db.executemany(
                        "INSERT INTO edges(relation,source,edge_type,target,readme_evidence,readme_confidence) "
                        "VALUES(?,?,?,?,?,?) ON CONFLICT(relation,source,target) DO UPDATE SET "
                        "readme_evidence=excluded.readme_evidence,readme_confidence=excluded.readme_confidence",
                        pending,
                    )
                    pending.clear()
                    state_set(db, "input_offset", stream.tell())
                    for key, value in counts.items():
                        state_set(db, key, value)
                if counts["readable_records"] and counts["readable_records"] % 100_000 < commit_records:
                    print(
                        f"readable={counts['readable_records']:,} yaml={counts['yaml_records']:,} "
                        f"stored_edges={db.execute('SELECT COUNT(*) FROM edges WHERE readme_evidence IS NOT NULL').fetchone()[0]:,}",
                        flush=True,
                    )
        if pending or int(state_get(db, "input_offset") or 0) != stream.tell():
            with db:
                db.executemany(
                    "INSERT INTO edges(relation,source,edge_type,target,readme_evidence,readme_confidence) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(relation,source,target) DO UPDATE SET "
                    "readme_evidence=excluded.readme_evidence,readme_confidence=excluded.readme_confidence",
                    pending,
                )
                state_set(db, "input_offset", stream.tell())
                for key, value in counts.items():
                    state_set(db, key, value)


def import_metadata(db: sqlite3.Connection) -> None:
    for relation, config in RELATIONS.items():
        state_key = f"metadata_complete_{relation}"
        if state_get(db, state_key) == "1":
            continue
        path = Path(config["metadata"])
        pending = []
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                pending.append(
                    (
                        relation,
                        row["source"],
                        row["edge_type"],
                        row["target"],
                        row.get("evidence", ""),
                        row.get("confidence", ""),
                    )
                )
                if len(pending) >= 10_000:
                    with db:
                        db.executemany(
                            "INSERT INTO edges(relation,source,edge_type,target,metadata_evidence,metadata_confidence) "
                            "VALUES(?,?,?,?,?,?) ON CONFLICT(relation,source,target) DO UPDATE SET "
                            "metadata_evidence=excluded.metadata_evidence,metadata_confidence=excluded.metadata_confidence",
                            pending,
                        )
                    pending.clear()
        with db:
            db.executemany(
                "INSERT INTO edges(relation,source,edge_type,target,metadata_evidence,metadata_confidence) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(relation,source,target) DO UPDATE SET "
                "metadata_evidence=excluded.metadata_evidence,metadata_confidence=excluded.metadata_confidence",
                pending,
            )
            state_set(db, state_key, 1)
        print(f"Imported metadata evidence: {relation}", flush=True)


def paths(output_dir: Path, relation: str, scope: str) -> dict[str, Path]:
    stem = f"model_{relation}_edges_{scope}"
    return {
        "csv": output_dir / f"{stem}.csv",
        "jsonl": output_dir / f"{stem}.jsonl",
        "detailed_csv": output_dir / f"{stem}_detailed.csv",
        "detailed_jsonl": output_dir / f"{stem}_detailed.jsonl",
    }


def export_relation(db: sqlite3.Connection, output_dir: Path, relation: str, scope: str) -> None:
    final = paths(output_dir, relation, scope)
    existing = [path for path in final.values() if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite:\n" + "\n".join(map(str, existing)))
    temporary = {key: path.with_name(path.name + ".tmp") for key, path in final.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    condition = "readme_evidence IS NOT NULL" if scope == "readme_full" else "1=1"
    fields = README_DETAIL_FIELDS if scope == "readme_full" else MERGED_DETAIL_FIELDS
    handles = {key: path.open("x" if "jsonl" in key else "x", newline="" if "csv" in key else None, encoding="utf-8") for key, path in temporary.items()}
    try:
        csv_writer = csv.DictWriter(handles["csv"], fieldnames=SIMPLE_FIELDS)
        detailed_writer = csv.DictWriter(handles["detailed_csv"], fieldnames=fields)
        csv_writer.writeheader()
        detailed_writer.writeheader()
        query = (
            "SELECT source,edge_type,target,readme_evidence,readme_confidence,metadata_evidence,metadata_confidence "
            f"FROM edges WHERE relation=? AND {condition} ORDER BY source,target"
        )
        count = 0
        for source, edge_type, target, revidence, rconfidence, mevidence, mconfidence in db.execute(query, (relation,)):
            simple = {"source": source, "edge_type": edge_type, "target": target}
            if scope == "readme_full":
                detailed = {
                    **simple,
                    "evidence": revidence,
                    "confidence": rconfidence,
                    "evidence_source": "full_readme_yaml",
                    "snapshot": SNAPSHOT,
                }
            else:
                sources = []
                if mevidence is not None:
                    sources.append("api_card_metadata")
                if revidence is not None:
                    sources.append("full_readme_yaml")
                detailed = {
                    **simple,
                    "evidence_sources": ";".join(sources),
                    "metadata_evidence": mevidence or "",
                    "metadata_confidence": mconfidence or "",
                    "readme_evidence": revidence or "",
                    "readme_confidence": rconfidence or "",
                    "readme_snapshot": SNAPSHOT if revidence is not None else "",
                }
            csv_writer.writerow(simple)
            detailed_writer.writerow(detailed)
            handles["jsonl"].write(json.dumps(simple, ensure_ascii=False, separators=(",", ":")) + "\n")
            handles["detailed_jsonl"].write(json.dumps(detailed, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
        for handle in handles.values():
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        for handle in handles.values():
            handle.close()
    for key, temp in temporary.items():
        temp.rename(final[key])
    print(f"Exported {relation} {scope}: {count:,} edges", flush=True)


def main() -> int:
    args = parse_args()
    if args.commit_records < 1 or args.max_records < 0:
        raise ValueError("Invalid commit or record limit")
    input_path = args.input.expanduser().resolve()
    state_path = args.state_db.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not input_path.is_file() or input_path == state_path:
        raise ValueError("Invalid input/state paths")
    output_dir.mkdir(parents=True, exist_ok=True)
    db = connect(state_path)
    try:
        initialize(db, input_path, output_dir)
        scan_readmes(db, input_path, args.commit_records, args.max_records)
        if state_get(db, "readme_complete") != "1":
            print("Stopped at --max-records; rerun to resume.")
            return 0
        import_metadata(db)
        if state_get(db, "export_complete") != "1":
            for relation in RELATIONS:
                export_relation(db, output_dir, relation, "readme_full")
                export_relation(db, output_dir, relation, "merged_v2")
            with db:
                state_set(db, "export_complete", 1)
        for relation in RELATIONS:
            readme_edges = db.execute(
                "SELECT COUNT(*) FROM edges WHERE relation=? AND readme_evidence IS NOT NULL", (relation,)
            ).fetchone()[0]
            merged_edges = db.execute("SELECT COUNT(*) FROM edges WHERE relation=?", (relation,)).fetchone()[0]
            print(f"{relation}: README={readme_edges:,} merged={merged_edges:,}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
