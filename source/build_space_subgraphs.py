#!/usr/bin/env python3
"""Build model-to-Space and dataset-to-Space HuggingGraph subgraphs.

Created: 2026-09-24
Version: v2026.09.24-02
Purpose: Convert Space API relationships, supplemented by reciprocal card
         declarations, into the existing HuggingGraph edge schema.

No existing graph artifact is read as an output or modified. Simple outputs
use ``source,edge_type,target``; detailed outputs additionally preserve
evidence and confidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse


CHANGE_ID = "v2026.09.24-02"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SPACE_INPUT = SCRIPT_DIR / "all_space_relationship_metadata_2026Sep24.jsonl"
DEFAULT_MODEL_INPUT = SCRIPT_DIR / "all_model_readme_metadata_2026Aug28.jsonl"
DEFAULT_DATASET_INPUT = SCRIPT_DIR / "all_dataset_readme_metadata_2026Aug28.jsonl"
SIMPLE_FIELDS = ["source", "edge_type", "target"]
DETAILED_FIELDS = SIMPLE_FIELDS + ["evidence", "confidence"]
REPO_ID = re.compile(r"^[^\s/]+(?:/[^\s/]+)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Space relationship subgraphs.")
    parser.add_argument("--space-input", type=Path, default=DEFAULT_SPACE_INPUT)
    parser.add_argument("--model-input", type=Path, default=DEFAULT_MODEL_INPUT)
    parser.add_argument("--dataset-input", type=Path, default=DEFAULT_DATASET_INPUT)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    return parser.parse_args()


def normalize_repo_id(value: object, kind: str) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
            "huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co",
        }:
            return None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if kind == "space":
            if len(parts) < 3 or parts[0].lower() != "spaces":
                return None
            candidate = "/".join(parts[1:3])
        elif kind == "dataset":
            if len(parts) < 3 or parts[0].lower() != "datasets":
                return None
            candidate = "/".join(parts[1:3])
        else:
            if len(parts) < 2 or parts[0].lower() in {"datasets", "spaces"}:
                return None
            candidate = "/".join(parts[:2])
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    if not REPO_ID.fullmatch(candidate):
        return None
    if kind == "space" and "/" not in candidate:
        return None
    return candidate


def declared_spaces(value: object) -> Iterator[str]:
    values = value if isinstance(value, list) else [value]
    for item in values:
        normalized = normalize_repo_id(item, "space")
        if normalized is not None:
            yield normalized


def connect_edges(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute(
        """
        CREATE TABLE edges(
            source TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            target TEXT NOT NULL,
            api_evidence INTEGER NOT NULL DEFAULT 0,
            card_evidence INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(source, edge_type, target)
        ) WITHOUT ROWID
        """
    )
    return connection


def add_edge(
    connection: sqlite3.Connection,
    source: str,
    edge_type: str,
    target: str,
    evidence: str,
) -> None:
    api = int(evidence.startswith("space_api."))
    card = int(evidence.endswith("_card.spaces"))
    connection.execute(
        """
        INSERT INTO edges(source, edge_type, target, api_evidence, card_evidence)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(source, edge_type, target) DO UPDATE SET
            api_evidence = MAX(api_evidence, excluded.api_evidence),
            card_evidence = MAX(card_evidence, excluded.card_evidence)
        """,
        (source, edge_type, target, api, card),
    )


def load_space_api(connection: sqlite3.Connection, path: Path) -> tuple[int, int]:
    records = invalid = 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} line {line_number}: {exc}") from exc
            space_id = normalize_repo_id(record.get("spaceId"), "space")
            if space_id is None:
                invalid += 1
                continue
            records += 1
            for model in record.get("models") or []:
                model_id = normalize_repo_id(model, "model")
                if model_id is None:
                    invalid += 1
                    continue
                add_edge(connection, f"model::{model_id}", "used_by_space", f"space::{space_id}", "space_api.models")
            for dataset in record.get("datasets") or []:
                dataset_id = normalize_repo_id(dataset, "dataset")
                if dataset_id is None:
                    invalid += 1
                    continue
                add_edge(connection, f"dataset::{dataset_id}", "used_by_space", f"space::{space_id}", "space_api.datasets")
            if records % 10_000 == 0:
                connection.commit()
    connection.commit()
    return records, invalid


def load_card_declarations(
    connection: sqlite3.Connection,
    path: Path,
    id_field: str,
    prefix: str,
) -> tuple[int, int]:
    accepted = invalid = 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} line {line_number}: {exc}") from exc
            repo_id = normalize_repo_id(record.get(id_field), prefix)
            metadata = record.get("readme_metadata")
            if repo_id is None or not isinstance(metadata, dict) or "spaces" not in metadata:
                continue
            raw = metadata.get("spaces")
            normalized = list(declared_spaces(raw))
            if raw not in (None, False, "") and not normalized:
                invalid += 1
            for space_id in normalized:
                add_edge(
                    connection,
                    f"{prefix}::{repo_id}",
                    "used_by_space",
                    f"space::{space_id}",
                    f"{prefix}_card.spaces",
                )
                accepted += 1
            if accepted and accepted % 10_000 == 0:
                connection.commit()
    connection.commit()
    return accepted, invalid


def output_paths(output_dir: Path, prefix: str) -> tuple[Path, Path, Path, Path]:
    return (
        output_dir / f"{prefix}_edges.csv",
        output_dir / f"{prefix}_edges.jsonl",
        output_dir / f"{prefix}_edges_detailed.csv",
        output_dir / f"{prefix}_edges_detailed.jsonl",
    )


def evidence_and_confidence(api: int, card: int, kind: str) -> tuple[str, str]:
    if api and card:
        return f"space_api.{kind}s;{kind}_card.spaces", "corroborated"
    if api:
        return f"space_api.{kind}s", "platform_linked"
    return f"{kind}_card.spaces", "declared"


def export_family(
    connection: sqlite3.Connection, output_dir: Path, kind: str
) -> int:
    prefix = f"{kind}_space"
    paths = output_paths(output_dir, prefix)
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite:\n" + "\n".join(map(str, existing)))
    count = 0
    with (
        paths[0].open("x", newline="", encoding="utf-8") as simple_csv,
        paths[1].open("x", encoding="utf-8") as simple_jsonl,
        paths[2].open("x", newline="", encoding="utf-8") as detailed_csv,
        paths[3].open("x", encoding="utf-8") as detailed_jsonl,
    ):
        simple_writer = csv.DictWriter(simple_csv, fieldnames=SIMPLE_FIELDS)
        detailed_writer = csv.DictWriter(detailed_csv, fieldnames=DETAILED_FIELDS)
        simple_writer.writeheader()
        detailed_writer.writeheader()
        rows = connection.execute(
            """
            SELECT source, edge_type, target, api_evidence, card_evidence
            FROM edges WHERE source LIKE ? ORDER BY source, target
            """,
            (f"{kind}::%",),
        )
        for source, edge_type, target, api, card in rows:
            simple = {"source": source, "edge_type": edge_type, "target": target}
            evidence, confidence = evidence_and_confidence(api, card, kind)
            detailed = {**simple, "evidence": evidence, "confidence": confidence}
            simple_writer.writerow(simple)
            detailed_writer.writerow(detailed)
            simple_jsonl.write(json.dumps(simple, ensure_ascii=False, separators=(",", ":")) + "\n")
            detailed_jsonl.write(json.dumps(detailed, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def main() -> int:
    args = parse_args()
    space_input = args.space_input.expanduser().resolve()
    model_input = args.model_input.expanduser().resolve()
    dataset_input = args.dataset_input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    for path in (space_input, model_input, dataset_input):
        if not path.is_file():
            raise FileNotFoundError(path)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hugginggraph-space-edges-") as temp_dir:
        connection = connect_edges(Path(temp_dir) / "edges.sqlite3")
        spaces, api_invalid = load_space_api(connection, space_input)
        model_declared, model_invalid = load_card_declarations(
            connection, model_input, "modelId", "model"
        )
        dataset_declared, dataset_invalid = load_card_declarations(
            connection, dataset_input, "datasetId", "dataset"
        )
        model_edges = export_family(connection, output_dir, "model")
        dataset_edges = export_family(connection, output_dir, "dataset")
        connection.close()

    print(f"Space records:             {spaces:12,}")
    print(f"Model-Space edges:         {model_edges:12,}")
    print(f"Dataset-Space edges:       {dataset_edges:12,}")
    print(f"Model-card declarations:   {model_declared:12,}")
    print(f"Dataset-card declarations: {dataset_declared:12,}")
    print(f"Rejected values:           {api_invalid + model_invalid + dataset_invalid:12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
