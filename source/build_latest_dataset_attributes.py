#!/usr/bin/env python3
"""Build Dataset Library, License, and Task edges from the latest full READMEs.

Created: 2026-10-02
Version: v2026.10.02-02
Purpose: Parse YAML front matter from the 2026-09-28 full Dataset README
         snapshot into independent, resumable, evidence-bearing subgraphs.

Change history:
- 2026-10-02 v2026.10.02-02: use LibYAML's safe loader for the full-corpus
  scan. Backup: build_latest_dataset_attributes.py.bak.20261002-184103
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from huggingface_hub.repocard import REGEX_YAML_BLOCK


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INPUT = ROOT / "all_dataset_readmes_full_2026Sep28.jsonl"
STATE = HERE / "latest_dataset_attributes.state.sqlite3"
RESULTS = HERE / "results"
SNAPSHOT = "full_dataset_readmes_2026Sep28"

import sys
sys.path.insert(0, str(ROOT))
from model_dataset_attribute_edges import DATASET_EXTRACTORS, RELATIONS  # noqa: E402


CONFIG = {
    relation: {
        "edge_type": RELATIONS[relation]["dataset_edge_type"],
        "prefix": RELATIONS[relation]["target_prefix"],
    }
    for relation in ("library", "license", "task")
}


def connect():
    db = sqlite3.connect(STATE, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS edges("
        "relation TEXT NOT NULL,source TEXT NOT NULL,edge_type TEXT NOT NULL,target TEXT NOT NULL,"
        "evidence TEXT NOT NULL,confidence TEXT NOT NULL,retrieved_at_utc TEXT NOT NULL,"
        "PRIMARY KEY(relation,source,target))"
    )
    db.commit()
    return db


def get(db, key, default="0"):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return default if row is None else str(row[0])


def set_value(db, key, value):
    db.execute(
        "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def initialize(db):
    stat = INPUT.stat()
    config = json.dumps({
        "change_id": "v2026.10.02-02", "input": str(INPUT),
        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
    }, sort_keys=True)
    stored = get(db, "configuration", "")
    if stored and stored != config:
        raise RuntimeError("State database belongs to a different input snapshot")
    if stored:
        return
    with db:
        set_value(db, "configuration", config)
        for key in (
            "input_offset", "records", "ok", "restricted", "no_readme",
            "yaml_records", "no_yaml", "invalid_yaml", "empty_yaml", "complete",
        ):
            set_value(db, key, 0)


def scan(db):
    if get(db, "complete") == "1":
        return
    keys = (
        "records", "ok", "restricted", "no_readme", "yaml_records",
        "no_yaml", "invalid_yaml", "empty_yaml",
    )
    counts = Counter({key: int(get(db, key)) for key in keys})
    offset = int(get(db, "input_offset"))
    pending = []
    with INPUT.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line:
                with db:
                    set_value(db, "complete", 1)
                break
            record = json.loads(line)
            counts["records"] += 1
            status = str(record.get("status") or "unknown")
            counts[status] += 1
            if status == "ok" and isinstance(record.get("readme"), str):
                match = REGEX_YAML_BLOCK.search(record["readme"])
                if not match:
                    counts["no_yaml"] += 1
                else:
                    try:
                        metadata = yaml.load(match.group(2), Loader=yaml.CSafeLoader)
                    except Exception:
                        metadata = None
                        counts["invalid_yaml"] += 1
                    if isinstance(metadata, dict) and metadata:
                        counts["yaml_records"] += 1
                        dataset_id = str(record.get("datasetId") or "").strip()
                        if dataset_id:
                            source = f"dataset::{dataset_id}"
                            for relation, cfg in CONFIG.items():
                                for target, (evidence, confidence) in DATASET_EXTRACTORS[relation](metadata).items():
                                    pending.append((
                                        relation, source, cfg["edge_type"], cfg["prefix"] + target,
                                        evidence, confidence, str(record.get("retrieved_at_utc") or ""),
                                    ))
                    elif metadata is not None:
                        counts["empty_yaml"] += 1
            if counts["records"] % 2000 == 0:
                with db:
                    db.executemany(
                        "INSERT INTO edges VALUES(?,?,?,?,?,?,?) ON CONFLICT(relation,source,target) "
                        "DO UPDATE SET evidence=excluded.evidence,confidence=excluded.confidence,"
                        "retrieved_at_utc=excluded.retrieved_at_utc",
                        pending,
                    )
                    pending.clear()
                    set_value(db, "input_offset", stream.tell())
                    for key, value in counts.items():
                        set_value(db, key, value)
                if counts["records"] % 100000 < 2000:
                    stored = db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                    print(
                        f"records={counts['records']:,} ok={counts['ok']:,} "
                        f"yaml={counts['yaml_records']:,} edges={stored:,}", flush=True,
                    )
        if pending:
            with db:
                db.executemany(
                    "INSERT INTO edges VALUES(?,?,?,?,?,?,?) ON CONFLICT(relation,source,target) "
                    "DO UPDATE SET evidence=excluded.evidence,confidence=excluded.confidence,"
                    "retrieved_at_utc=excluded.retrieved_at_utc",
                    pending,
                )
                set_value(db, "input_offset", stream.tell())
                for key, value in counts.items():
                    set_value(db, key, value)


def write_csv(path, fields, rows):
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export(db):
    summary = {
        "change_id": "v2026.10.02-02",
        "snapshot": SNAPSHOT,
        "input": str(INPUT),
        "input_sha256": sha256(INPUT),
        "scan": {key: int(get(db, key)) for key in (
            "records", "ok", "restricted", "no_readme", "yaml_records",
            "no_yaml", "invalid_yaml", "empty_yaml",
        )},
        "subgraphs": {},
    }
    rng = random.Random(20261002)
    for relation in CONFIG:
        query = (
            "SELECT source,edge_type,target,evidence,confidence,retrieved_at_utc "
            "FROM edges WHERE relation=? ORDER BY source,target"
        )
        data = [dict(zip(
            ("source", "edge_type", "target", "evidence", "confidence", "retrieved_at_utc"), row
        )) for row in db.execute(query, (relation,))]
        simple = [{key: row[key] for key in ("source", "edge_type", "target")} for row in data]
        write_csv(
            RESULTS / f"dataset_{relation}_latest_readme_edges.csv",
            ["source", "edge_type", "target"], simple,
        )
        write_csv(
            RESULTS / f"dataset_{relation}_latest_readme_evidence.csv",
            ["source", "edge_type", "target", "evidence", "confidence", "retrieved_at_utc"], data,
        )
        grouped = defaultdict(list)
        for row in data:
            grouped[row["confidence"]].append(row)
        sample = []
        for confidence in sorted(grouped):
            candidates = grouped[confidence]
            selected = candidates if len(candidates) <= 100 else rng.sample(candidates, 100)
            for row in selected:
                sample.append({**row, "manual_label": "", "review_notes": ""})
        write_csv(
            RESULTS / f"dataset_{relation}_latest_validation_sample.csv",
            ["source", "edge_type", "target", "evidence", "confidence", "retrieved_at_utc",
             "manual_label", "review_notes"],
            sample,
        )
        sources = {row["source"] for row in data}
        targets = {row["target"] for row in data}
        summary["subgraphs"][relation] = {
            "edges": len(data), "source_nodes": len(sources), "target_nodes": len(targets),
            "nodes": len(sources | targets),
            "confidence": dict(sorted(Counter(row["confidence"] for row in data).items())),
            "evidence": dict(Counter(row["evidence"] for row in data).most_common()),
            "duplicate_edges": len(data) - len({(r["source"], r["target"]) for r in data}),
            "self_loops": len(sources & targets),
            "validation_sample_rows": len(sample),
        }
    (RESULTS / "latest_dataset_attributes_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main():
    if not INPUT.is_file():
        raise FileNotFoundError(INPUT)
    RESULTS.mkdir(parents=True, exist_ok=True)
    db = connect()
    try:
        initialize(db)
        scan(db)
        if get(db, "complete") != "1":
            raise RuntimeError("Scan did not complete")
        export(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
