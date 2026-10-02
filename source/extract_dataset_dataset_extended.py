#!/usr/bin/env python3
"""Build evidence-aware Dataset-to-Dataset relationship graphs.

Created: 2026-10-02
Version: v2026.10.02-01
Purpose: Extend the metadata-only Dataset-Dataset baseline with preserved
         metadata evidence and full-README relationship evidence.

Change history:
- 2026-10-02 v2026.10.02-01: distinguish successful README responses from
  non-empty README bodies. Backup: extract_dataset_dataset_extended.py.bak.20261002-163418
- 2026-10-02 v2026.10.02-01: tighten reference-to-phrase association and
  suppress duplicate bare IDs inside URLs/code. Backup:
  extract_dataset_dataset_extended.py.bak.20261002-163636
- 2026-10-02 v2026.10.02-01: enforce sentence-boundary association and keep
  load_dataset calls as usage evidence. Backup:
  extract_dataset_dataset_extended.py.bak.20261002-164256
- 2026-10-02 v2026.10.02-01: add deduplicated graph-edge exports and a
  relationship-stratified lineage review sample. Backup:
  extract_dataset_dataset_extended.py.bak.20261002-164822

This program reads saved local snapshots only. It performs no network calls,
does not modify the baseline graph files, and can resume both input scans from
byte offsets stored in SQLite.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse


CHANGE_ID = "v2026.10.02-01"
SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
DEFAULT_METADATA = PARENT_DIR / "all_dataset_readme_metadata_2026Aug28.jsonl"
DEFAULT_READMES = PARENT_DIR / "all_dataset_readmes_full_2026Sep28.jsonl"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "refined_v3"

FIELD_RELATION = {
    "derived_from": ("derived_from", "high"),
    "parent_dataset": ("derived_from", "high"),
    "parent_datasets": ("derived_from", "high"),
    "base_dataset": ("derived_from", "high"),
    "base_datasets": ("derived_from", "high"),
    "original_dataset": ("derived_from", "high"),
    "original_dataset_id": ("derived_from", "high"),
    "prov_wasderivedfrom": ("derived_from", "high"),
    "source_dataset": ("uses_source_data", "medium"),
    "source_datasets": ("uses_source_data", "medium"),
    "source_dataasets": ("uses_source_data", "medium"),
    "dataset_source": ("uses_source_data", "medium"),
    "dataset_sources": ("uses_source_data", "medium"),
    "original_source": ("uses_source_data", "medium"),
    "datasets": ("unknown_dataset_relation", "low"),
}

REFERENCE_KEYS = {
    "id", "dataset", "dataset_id", "datasetid", "repo_id", "repository",
    "name", "path", "url",
}

LINEAGE_RELATIONS = {
    "derived_from", "merged_from", "filtered_from", "translated_from",
    "synthetic_from", "subset_of",
}

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
ID_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
HF_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:huggingface\.co|hf\.co)/datasets/"
    r"([A-Za-z0-9_][A-Za-z0-9._-]*/[A-Za-z0-9_][A-Za-z0-9._-]*)",
    re.IGNORECASE,
)
LOAD_DATASET_RE = re.compile(
    r"\bload_dataset\s*\(\s*[rubfRUBF]*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
BARE_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"([A-Za-z0-9_][A-Za-z0-9._-]*/[A-Za-z0-9_][A-Za-z0-9._-]*)"
)

RELATION_PATTERNS = (
    ("translated_from", "high", re.compile(
        r"\b(?:translated|translation|machine[- ]translated)\b.{0,80}\b(?:from|of)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("filtered_from", "high", re.compile(
        r"\b(?:filtered|cleaned|deduplicated)\b.{0,80}\b(?:from|version of)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("merged_from", "high", re.compile(
        r"\b(?:merged|combined|concatenated)\s+(?:from|using)\b|"
        r"\b(?:merge|combination|mixture|concatenation)\s+of\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("synthetic_from", "high", re.compile(
        r"\b(?:synthetic(?:ally)?\s+)?generated\s+(?:from|using|based on)\b|"
        r"\bsynthetic\s+(?:dataset|data).{0,50}\b(?:from|using|based on)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("subset_of", "high", re.compile(
        r"\b(?:subset|sampled|sampling)\b.{0,80}\b(?:from|of)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("derived_from", "high", re.compile(
        r"\b(?:derived|adapted|built|created|constructed|based)\b.{0,80}"
        r"\b(?:from|on)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("evaluates_on", "medium", re.compile(
        r"\b(?:evaluation|evaluate[sd]?|benchmark(?:ed|ing)?|test(?:ed|ing)?)\b",
        re.IGNORECASE,
    )),
    ("uses_source_data", "medium", re.compile(
        r"\b(?:source dataset|source data|data source|sourced from|collected from)\b",
        re.IGNORECASE,
    )),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract evidence-aware dataset relationships from local snapshots."
    )
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--readmes", type=Path, default=DEFAULT_READMES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--max-metadata-records", type=int, default=0)
    parser.add_argument("--max-readme-records", type=int, default=0)
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args()


def normalize_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def reference_strings(value: object):
    if isinstance(value, str):
        value = value.strip()
        if value:
            yield value
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from reference_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if normalize_field_name(key) in REFERENCE_KEYS:
                yield from reference_strings(item)


def clean_reference(value: str) -> str | None:
    candidate = value.strip().strip("'\"`").rstrip(".,;:!?)\]}>")
    for prefix in ("dataset:", "datasets:"):
        if candidate.lower().startswith(prefix):
            candidate = candidate.split(":", 1)[1].strip()
            break
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"}:
            return None
        if parsed.netloc.lower() not in {
            "huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co",
        }:
            return None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if parts and parts[0].lower() == "datasets":
            parts = parts[1:]
        if len(parts) < 2:
            return None
        candidate = "/".join(parts[:2])
    return candidate.strip("/")


def plausible_hf_id(value: str) -> bool:
    if len(value) > 200 or value.count("/") != 1:
        return False
    owner, repository = value.split("/", 1)
    for part in (owner, repository):
        if not part or not ID_PART.fullmatch(part):
            return False
        if part[-1] in ".-" or "--" in part or ".." in part:
            return False
    return not repository.endswith(".git")


def load_universe(path: Path):
    exact: set[str] = set()
    lowercase: dict[str, str] = {}
    basenames: dict[str, str | None] = {}
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            counts[str(record.get("status") or "unknown")] += 1
            dataset_id = record.get("datasetId")
            if not isinstance(dataset_id, str) or not dataset_id.strip():
                continue
            dataset_id = dataset_id.strip()
            exact.add(dataset_id)
            lowercase.setdefault(dataset_id.lower(), dataset_id)
            basename = dataset_id.rsplit("/", 1)[-1].lower()
            if basename not in basenames:
                basenames[basename] = dataset_id
            elif basenames[basename] != dataset_id:
                basenames[basename] = None
    unique_basenames = {key: value for key, value in basenames.items() if value}
    return exact, lowercase, unique_basenames, counts


def readme_snapshot_counts(path: Path):
    """Independently count README statuses and non-empty bodies."""
    statuses: Counter[str] = Counter()
    nonempty = 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            status = str(record.get("status") or "unknown")
            statuses[status] += 1
            if status == "ok" and isinstance(record.get("readme"), str) and record["readme"]:
                nonempty += 1
    return statuses, nonempty


def resolve_reference(value, exact, lowercase, unique_basenames):
    candidate = clean_reference(value)
    if not candidate:
        return None, "rejected"
    if candidate in exact:
        return candidate, "validated"
    canonical = lowercase.get(candidate.lower())
    if canonical:
        return canonical, "validated"
    parts = candidate.split("/")
    if len(parts) > 2:
        canonical = lowercase.get("/".join(parts[:2]).lower())
        return (canonical, "validated") if canonical else (None, "rejected")
    if len(parts) == 1:
        canonical = unique_basenames.get(candidate.lower())
        return (canonical, "validated") if canonical else (None, "rejected")
    if plausible_hf_id(candidate):
        return candidate, "unresolved"
    return None, "rejected"


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS evidence("
        "source_dataset TEXT NOT NULL,target_dataset TEXT NOT NULL,"
        "relationship_type TEXT NOT NULL,evidence_source TEXT NOT NULL,"
        "evidence_field TEXT NOT NULL,raw_reference TEXT NOT NULL,"
        "context TEXT NOT NULL,source_status TEXT NOT NULL,"
        "confidence TEXT NOT NULL,retrieved_at_utc TEXT NOT NULL,"
        "PRIMARY KEY(source_dataset,target_dataset,relationship_type,"
        "evidence_source,evidence_field,raw_reference))"
    )
    db.execute("CREATE INDEX IF NOT EXISTS evidence_target ON evidence(target_dataset)")
    db.execute("CREATE INDEX IF NOT EXISTS evidence_status ON evidence(source_status,confidence)")
    db.commit()
    return db


def state_get(db: sqlite3.Connection, key: str, default: str = "0") -> str:
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return default if row is None else str(row[0])


def state_set(db: sqlite3.Connection, key: str, value: object) -> None:
    db.execute(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def input_signature(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def initialize_state(db, metadata_path: Path, readme_path: Path):
    configuration = json.dumps({
        "change_id": CHANGE_ID,
        "metadata": input_signature(metadata_path),
        "readmes": input_signature(readme_path),
    }, sort_keys=True)
    stored = state_get(db, "configuration", "")
    if stored and stored != configuration:
        raise RuntimeError("State database belongs to different input snapshots")
    if not stored:
        with db:
            state_set(db, "configuration", configuration)
            state_set(db, "metadata_offset", 0)
            state_set(db, "metadata_records", 0)
            state_set(db, "metadata_complete", 0)
            state_set(db, "readme_offset", 0)
            state_set(db, "readme_records", 0)
            state_set(db, "readme_ok", 0)
            state_set(db, "readme_complete", 0)


def evidence_row(source, target, relation, evidence_source, field, raw,
                 context, status, confidence, retrieved):
    return (
        source, target, relation, evidence_source, field, raw[:1000],
        re.sub(r"\s+", " ", context).strip()[:1000], status, confidence,
        str(retrieved or ""),
    )


UPSERT = (
    "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(source_dataset,target_dataset,relationship_type,evidence_source,"
    "evidence_field,raw_reference) DO UPDATE SET "
    "context=CASE WHEN length(excluded.context)>length(context) "
    "THEN excluded.context ELSE context END,"
    "source_status=CASE WHEN source_status='validated' THEN source_status "
    "ELSE excluded.source_status END,"
    "confidence=CASE confidence "
    "WHEN 'high' THEN confidence WHEN 'medium' THEN CASE WHEN excluded.confidence='high' "
    "THEN excluded.confidence ELSE confidence END ELSE excluded.confidence END"
)


def scan_metadata(db, path, exact, lowercase, unique_basenames, batch_size, limit):
    if state_get(db, "metadata_complete") == "1":
        print("Metadata scan already complete.", flush=True)
        return
    offset = int(state_get(db, "metadata_offset"))
    total_records = int(state_get(db, "metadata_records"))
    processed_run = 0
    pending = []
    counters = Counter()
    with path.open("rb") as stream:
        stream.seek(offset)
        while not limit or processed_run < limit:
            line = stream.readline()
            if not line:
                break
            processed_run += 1
            total_records += 1
            record = json.loads(line)
            target = record.get("datasetId")
            metadata = record.get("readme_metadata")
            if isinstance(target, str) and target.strip() and isinstance(metadata, dict):
                target = target.strip()
                for raw_field, value in metadata.items():
                    field = normalize_field_name(raw_field)
                    specification = FIELD_RELATION.get(field)
                    if not specification:
                        continue
                    relation, confidence = specification
                    for raw in set(reference_strings(value)):
                        source, status = resolve_reference(
                            raw, exact, lowercase, unique_basenames
                        )
                        counters[f"candidate_{status}"] += 1
                        if not source or source.lower() == target.lower():
                            continue
                        pending.append(evidence_row(
                            source, target, relation, "card_metadata", field, raw,
                            "", status, confidence, record.get("retrieved_at_utc"),
                        ))
            if processed_run % batch_size == 0:
                with db:
                    db.executemany(UPSERT, pending)
                    state_set(db, "metadata_offset", stream.tell())
                    state_set(db, "metadata_records", total_records)
                pending.clear()
                print(
                    f"metadata records={total_records:,} "
                    f"evidence={db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]:,}",
                    flush=True,
                )
        with db:
            db.executemany(UPSERT, pending)
            state_set(db, "metadata_offset", stream.tell())
            state_set(db, "metadata_records", total_records)
            if not line:
                state_set(db, "metadata_complete", 1)
    print(f"Metadata pass stopped at records={total_records:,}.", flush=True)


def context_window(text: str, start: int, end: int, radius: int = 280) -> str:
    return text[max(0, start - radius):min(len(text), end + radius)]


def classify_reference(text: str, start: int, end: int, evidence_kind: str):
    """Classify a reference only from language directly attached to it."""
    before = text[max(0, start - 220):start]
    after = text[end:min(len(text), end + 100)]

    # A loader call demonstrates use, but surrounding prose is not sufficient
    # to infer that the README's dataset was derived from the loaded dataset.
    if evidence_kind == "load_dataset":
        return "uses_source_data", "medium"

    # Do not borrow a relationship phrase from a completed previous sentence
    # or table cell. Markdown lists without sentence punctuation remain intact,
    # allowing "combined from:\n- dataset" constructions.
    boundaries = list(re.finditer(r"(?:[.!?](?=\s|[|]))|(?:\n\s*\n)", before))
    if boundaries:
        before = before[boundaries[-1].end():]
    for relation, confidence, pattern in RELATION_PATTERNS:
        matches = list(pattern.finditer(before))
        if matches and len(before) - matches[-1].end() <= 120:
            return relation, confidence

    # Limited post-reference constructions such as "X is the source dataset"
    # are allowed, but general keywords elsewhere in the paragraph are not.
    post = after[:80]
    if re.match(r"^\s*(?:\)|\]|[,:;-])*\s*(?:is|was)\s+(?:the\s+|a\s+)?source\b", post, re.I):
        return "uses_source_data", "medium"
    if re.match(r"^\s*(?:\)|\]|[,:;-])*\s*(?:is|was)\s+(?:the\s+|a\s+)?parent\b", post, re.I):
        return "derived_from", "high"
    return "references_dataset", "low"


def readme_references(text: str, exact, lowercase, unique_basenames):
    seen = set()
    occupied = []

    for kind, pattern in (("hf_dataset_url", HF_URL_RE), ("load_dataset", LOAD_DATASET_RE)):
        for match in pattern.finditer(text):
            raw = match.group(1)
            reference_start, reference_end = match.span(1)
            key = (kind, raw, reference_start)
            if key in seen:
                continue
            seen.add(key)
            occupied.append((match.start(), match.end()))
            context = context_window(text, match.start(), match.end())
            yield (
                kind, raw, context,
                classify_reference(text, match.start(), match.end(), kind), True,
            )

    # Bare owner/repository references are accepted only when they exist in the
    # saved universe and directly follow explicit lineage language. IDs already
    # captured inside URLs or load_dataset calls are suppressed.
    for match in BARE_ID_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        raw = match.group(1)
        key = ("lineage_context_id", raw, match.start())
        if key in seen:
            continue
        source, status = resolve_reference(raw, exact, lowercase, unique_basenames)
        if not source or status != "validated":
            continue
        classified = classify_reference(
            text, match.start(), match.end(), "lineage_context_id"
        )
        if classified[0] not in LINEAGE_RELATIONS:
            continue
        seen.add(key)
        context = context_window(text, match.start(), match.end())
        yield "lineage_context_id", raw, context, classified, False


def scan_readmes(db, path, exact, lowercase, unique_basenames, batch_size, limit):
    if state_get(db, "readme_complete") == "1":
        print("README scan already complete.", flush=True)
        return
    offset = int(state_get(db, "readme_offset"))
    total_records = int(state_get(db, "readme_records"))
    readme_ok = int(state_get(db, "readme_ok"))
    processed_run = 0
    pending = []
    with path.open("rb") as stream:
        stream.seek(offset)
        while not limit or processed_run < limit:
            line = stream.readline()
            if not line:
                break
            processed_run += 1
            total_records += 1
            record = json.loads(line)
            target = record.get("datasetId")
            text = record.get("readme")
            if (
                record.get("status") == "ok" and isinstance(target, str)
                and target.strip() and isinstance(text, str) and text
            ):
                readme_ok += 1
                target = target.strip()
                for kind, raw, context, classified, _ in readme_references(
                    text, exact, lowercase, unique_basenames
                ):
                    relation, confidence = classified
                    source, status = resolve_reference(
                        raw, exact, lowercase, unique_basenames
                    )
                    if not source or source.lower() == target.lower():
                        continue
                    pending.append(evidence_row(
                        source, target, relation, "readme", kind, raw, context,
                        status, confidence, record.get("retrieved_at_utc"),
                    ))
            if processed_run % batch_size == 0:
                with db:
                    db.executemany(UPSERT, pending)
                    state_set(db, "readme_offset", stream.tell())
                    state_set(db, "readme_records", total_records)
                    state_set(db, "readme_ok", readme_ok)
                pending.clear()
                print(
                    f"readmes records={total_records:,} ok={readme_ok:,} "
                    f"evidence={db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]:,}",
                    flush=True,
                )
        with db:
            db.executemany(UPSERT, pending)
            state_set(db, "readme_offset", stream.tell())
            state_set(db, "readme_records", total_records)
            state_set(db, "readme_ok", readme_ok)
            if not line:
                state_set(db, "readme_complete", 1)
    print(f"README pass stopped at records={total_records:,}, ok={readme_ok:,}.", flush=True)


OUTPUT_FIELDS = [
    "source_dataset", "target_dataset", "relationship_type", "evidence_source",
    "evidence_field", "raw_reference", "context", "source_status", "confidence",
    "retrieved_at_utc",
]


def atomic_csv(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OUTPUT_FIELDS)
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def graph_stats(rows):
    edge_keys = {(row[0], row[1], row[2]) for row in rows}
    nodes = {value for source, target, _ in edge_keys for value in (source, target)}
    return {"edges": len(edge_keys), "nodes": len(nodes)}


EDGE_FIELDS = [
    "source", "edge_type", "target", "evidence_count", "evidence_sources",
    "evidence_fields", "max_confidence", "source_status",
]


def aggregate_graph_edges(rows):
    grouped = {}
    for row in rows:
        key = (row[0], row[1], row[2])
        item = grouped.setdefault(key, {
            "count": 0, "sources": set(), "fields": set(),
            "confidence": "low", "statuses": set(),
        })
        item["count"] += 1
        item["sources"].add(row[3])
        item["fields"].add(row[4])
        item["statuses"].add(row[7])
        if CONFIDENCE_RANK[row[8]] > CONFIDENCE_RANK[item["confidence"]]:
            item["confidence"] = row[8]
    output = []
    for (source, target, relation), item in sorted(grouped.items()):
        output.append((
            source, relation, target, item["count"],
            ";".join(sorted(item["sources"])),
            ";".join(sorted(item["fields"])), item["confidence"],
            "validated" if "validated" in item["statuses"] else "unresolved",
        ))
    return output


def atomic_edge_csv(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(EDGE_FIELDS)
        writer.writerows(aggregate_graph_edges(rows))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_lineage_validation_sample(path: Path, rows):
    representatives = {}
    for row in rows:
        key = (row[0], row[1], row[2])
        current = representatives.get(key)
        if current is None or (row[3] == "readme" and len(row[6]) > len(current[6])):
            representatives[key] = row
    groups = defaultdict(list)
    for row in representatives.values():
        groups[row[2]].append(row)
    rng = random.Random(20261002)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OUTPUT_FIELDS + ["sample_group", "manual_label", "review_notes"])
        for relation in sorted(groups):
            candidates = groups[relation]
            selected = candidates if len(candidates) <= 100 else rng.sample(candidates, 100)
            for row in sorted(selected):
                writer.writerow(list(row) + [f"lineage_{relation}", "", ""])
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def export_outputs(
    db: sqlite3.Connection,
    output_dir: Path,
    universe_counts,
    readme_status_counts,
    readme_nonempty,
):
    all_rows = db.execute(
        "SELECT source_dataset,target_dataset,relationship_type,evidence_source,"
        "evidence_field,raw_reference,context,source_status,confidence,retrieved_at_utc "
        "FROM evidence ORDER BY source_dataset,target_dataset,relationship_type,"
        "evidence_source,evidence_field,raw_reference"
    ).fetchall()
    high = [
        row for row in all_rows if row[7] == "validated" and row[8] == "high"
        and row[2] in LINEAGE_RELATIONS
    ]
    probable = [
        row for row in all_rows if row[7] == "validated"
        and row[8] in {"high", "medium"} and row[2] in LINEAGE_RELATIONS
    ]
    unresolved = [row for row in all_rows if row[7] == "unresolved"]

    atomic_csv(output_dir / "dataset_lineage_high_confidence.csv", high)
    atomic_csv(output_dir / "dataset_lineage_probable.csv", probable)
    atomic_csv(output_dir / "dataset_relationships_extended.csv", all_rows)
    atomic_csv(output_dir / "unresolved_dataset_references.csv", unresolved)
    atomic_edge_csv(output_dir / "dataset_lineage_high_confidence_edges.csv", high)
    atomic_edge_csv(output_dir / "dataset_lineage_probable_edges.csv", probable)
    atomic_edge_csv(output_dir / "dataset_relationships_extended_edges.csv", all_rows)
    atomic_edge_csv(output_dir / "unresolved_dataset_reference_edges.csv", unresolved)
    atomic_lineage_validation_sample(
        output_dir / "validation_sample_lineage.csv", high
    )

    groups = defaultdict(list)
    for row in all_rows:
        if row[7] == "unresolved":
            group = "unresolved"
        elif row[3] == "readme":
            group = "readme"
        elif row[4] == "datasets":
            group = "metadata_generic_datasets"
        elif row[8] == "high":
            group = "metadata_explicit_high"
        else:
            group = "metadata_source_medium"
        groups[group].append(row)
    rng = random.Random(20261002)
    sample_path = output_dir / "validation_sample.csv"
    temporary = sample_path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(OUTPUT_FIELDS + ["sample_group", "manual_label", "review_notes"])
        for group in sorted(groups):
            candidates = groups[group]
            selected = candidates if len(candidates) <= 100 else rng.sample(candidates, 100)
            for row in sorted(selected):
                writer.writerow(list(row) + [group, "", ""])
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, sample_path)

    by_relation = Counter(row[2] for row in all_rows)
    by_evidence = Counter(row[3] for row in all_rows)
    by_confidence = Counter(row[8] for row in all_rows)
    by_status = Counter(row[7] for row in all_rows)
    summary = {
        "change_id": CHANGE_ID,
        "inputs": json.loads(state_get(db, "configuration")),
        "scan": {
            "metadata_records": int(state_get(db, "metadata_records")),
            "metadata_complete": state_get(db, "metadata_complete") == "1",
            "readme_records": int(state_get(db, "readme_records")),
            "readme_status_counts": dict(readme_status_counts),
            "readme_nonempty_bodies_analyzed": readme_nonempty,
            "readme_complete": state_get(db, "readme_complete") == "1",
            "dataset_population_status": dict(universe_counts),
        },
        "evidence_rows": len(all_rows),
        "high_confidence_lineage": graph_stats(high),
        "probable_lineage": graph_stats(probable),
        "extended_relationships": graph_stats(all_rows),
        "unresolved_relationships": graph_stats(unresolved),
        "by_relationship_type": dict(sorted(by_relation.items())),
        "by_evidence_source": dict(sorted(by_evidence.items())),
        "by_confidence": dict(sorted(by_confidence.items())),
        "by_source_status": dict(sorted(by_status.items())),
        "validation_sample_rows": sum(min(100, len(rows)) for rows in groups.values()),
        "validation_sample_groups": {key: min(100, len(value)) for key, value in sorted(groups.items())},
    }
    summary_path = output_dir / "summary.json"
    temporary = summary_path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, summary_path)
    return summary


def main() -> int:
    args = parse_args()
    metadata_path = args.metadata.expanduser().resolve()
    readme_path = args.readmes.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not metadata_path.is_file() or not readme_path.is_file():
        raise FileNotFoundError("Both saved metadata and README snapshots are required")
    if args.batch_size < 1 or args.max_metadata_records < 0 or args.max_readme_records < 0:
        raise ValueError("Invalid batch or record limit")
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "dataset_dataset_extended.state.sqlite3"
    if args.rebuild and state_path.exists():
        raise RuntimeError(
            "Refusing to delete state automatically; move it aside before rebuilding"
        )

    print("Loading saved dataset universe...", flush=True)
    exact, lowercase, unique_basenames, universe_counts = load_universe(metadata_path)
    print(f"Dataset IDs: {len(exact):,}", flush=True)
    print("Verifying saved README status counts...", flush=True)
    readme_status_counts, readme_nonempty = readme_snapshot_counts(readme_path)
    db = connect(state_path)
    try:
        initialize_state(db, metadata_path, readme_path)
        scan_metadata(
            db, metadata_path, exact, lowercase, unique_basenames,
            args.batch_size, args.max_metadata_records,
        )
        scan_readmes(
            db, readme_path, exact, lowercase, unique_basenames,
            args.batch_size, args.max_readme_records,
        )
        summary = export_outputs(
            db, output_dir, universe_counts, readme_status_counts, readme_nonempty
        )
    finally:
        db.close()
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
