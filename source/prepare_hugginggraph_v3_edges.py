#!/usr/bin/env python3
"""Prepare normalized, count-verified edge inputs for HuggingGraph v3.

Created: 2026-09-30
Version: v2026.10.02-04
Purpose: Reconcile the latest Space population, normalize all GitHub nodes,
         and prepare the October 2, 2026 Dataset relationship inputs.

Change history:
- 2026-10-02 v2026.10.02-04: updated Dataset relationship counts for the
  October 2 v3 release.
  Backup: prepare_hugginggraph_v3_edges.py.bak.20261002-200822
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from huggingface_hub import HfApi


FIELDS = ["source", "edge_type", "target"]
CORE_FILES = (
    ("01_model_model_edges.csv", "model", "model", 966_035),
    ("02_model_dataset_edges.csv", "dataset", "model", 362_064),
    ("03_dataset_dataset_edges.csv", "dataset", "dataset", 12_541),
    ("04_model_library_edges.csv", "model", "library", 1_311_386),
    ("05_dataset_library_edges.csv", "dataset", "library", 16_864),
    ("06_model_license_edges.csv", "model", "license", 1_090_845),
    ("07_dataset_license_edges.csv", "dataset", "license", 331_317),
    ("08_model_task_edges.csv", "model", "task", 577_077),
    ("09_dataset_task_edges.csv", "dataset", "task", 317_412),
)
EXPECTED_UNAVAILABLE = {
    "Gpo128482owppw/exppo-vision",
    "sumabfron/Crumble",
    "tostido/K-os",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-edge-dir", type=Path, required=True)
    parser.add_argument("--workspace-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def typed(value: str, kind: str) -> str:
    prefix = f"{kind}::"
    return value if value.startswith(prefix) else prefix + value


def open_writer(path: Path):
    stream = path.open("x", newline="", encoding="utf-8")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    return stream, writer


def copy_core(source: Path, destination: Path, source_type: str, target_type: str) -> int:
    count = 0
    output, writer = open_writer(destination)
    try:
        with source.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames != FIELDS:
                raise ValueError(f"Unexpected schema in {source}: {reader.fieldnames}")
            for row in reader:
                writer.writerow(
                    {
                        "source": typed(row["source"], source_type),
                        "edge_type": row["edge_type"],
                        "target": typed(row["target"], target_type),
                    }
                )
                count += 1
    finally:
        output.close()
    return count


def write_github(db_path: Path, destination: Path) -> int:
    count = 0
    output, writer = open_writer(destination)
    db = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        for source, repo_key in db.execute(
            "SELECT source,repo_key FROM edges ORDER BY source,repo_key"
        ):
            writer.writerow(
                {
                    "source": source,
                    "edge_type": "links_to_github",
                    "target": f"https://github.com/{repo_key}",
                }
            )
            count += 1
    finally:
        db.close()
        output.close()
    return count


def read_ids(path: Path) -> set[str]:
    result: set[str] = set()
    with path.open(encoding="utf-8") as input_file:
        for line in input_file:
            if not line.strip():
                continue
            record = json.loads(line)
            result.add(str(record.get("spaceId") or "").strip())
    return result


def fetch_space(space_id: str):
    try:
        info = HfApi().space_info(
            space_id, expand=["models", "datasets"], timeout=30
        )
        return space_id, list(info.models or []), list(info.datasets or []), None
    except Exception as exc:  # API status is reported and checked below.
        return space_id, [], [], type(exc).__name__


def reconcile_space_edges(workspace: Path, workers: int):
    relationship_ids = read_ids(workspace / "all_space_relationship_metadata_2026Sep24.jsonl")
    card_ids = read_ids(workspace / "all_space_card_metadata_2026Sep24.jsonl")
    obsolete_targets = {f"space::{space_id}" for space_id in relationship_ids - card_ids}

    model_edges: set[tuple[str, str, str]] = set()
    dataset_edges: set[tuple[str, str, str]] = set()
    for filename, output in (
        ("model_space_edges.csv", model_edges),
        ("dataset_space_edges.csv", dataset_edges),
    ):
        with (workspace / filename).open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                if row["target"] not in obsolete_targets:
                    output.add((row["source"], row["edge_type"], row["target"]))

    unavailable: set[str] = set()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_space, space_id) for space_id in card_ids - relationship_ids]
        for future in as_completed(futures):
            space_id, model_ids, dataset_ids, error = future.result()
            if error is not None:
                unavailable.add(space_id)
                continue
            target = f"space::{space_id}"
            model_edges.update(
                (f"model::{model_id}", "used_by_space", target) for model_id in model_ids
            )
            dataset_edges.update(
                (f"dataset::{dataset_id}", "used_by_space", target)
                for dataset_id in dataset_ids
            )

    if unavailable != EXPECTED_UNAVAILABLE:
        raise RuntimeError(
            f"Unexpected unavailable Space set: {sorted(unavailable)}; "
            f"expected {sorted(EXPECTED_UNAVAILABLE)}"
        )
    return model_edges, dataset_edges, unavailable


def write_edge_set(path: Path, edges: set[tuple[str, str, str]]) -> int:
    output, writer = open_writer(path)
    try:
        for source, edge_type, target in sorted(edges):
            writer.writerow({"source": source, "edge_type": edge_type, "target": target})
    finally:
        output.close()
    return len(edges)


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    core = args.core_edge_dir.expanduser().resolve()
    workspace = args.workspace_dir.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)

    for filename, source_type, target_type, expected in CORE_FILES:
        actual = copy_core(core / filename, output / filename, source_type, target_type)
        if actual != expected:
            raise ValueError(f"Unexpected {filename} count: {actual:,} != {expected:,}")

    for filename, state_name, expected in (
        ("10_model_github_edges.csv", "model_github_edges_full.state.sqlite3", 962_855),
        ("11_dataset_github_edges.csv", "dataset_github_edges_full.state.sqlite3", 188_888),
        ("14_space_github_edges.csv", "space_github_edges_full.state.sqlite3", 113_755),
    ):
        actual = write_github(workspace / state_name, output / filename)
        if actual != expected:
            raise ValueError(f"Unexpected {filename} count: {actual:,} != {expected:,}")

    model_edges, dataset_edges, unavailable = reconcile_space_edges(
        workspace, args.workers
    )
    for filename, edges, expected in (
        ("12_model_space_edges.csv", model_edges, 941_121),
        ("13_dataset_space_edges.csv", dataset_edges, 83_061),
    ):
        actual = write_edge_set(output / filename, edges)
        if actual != expected:
            raise ValueError(f"Unexpected {filename} count: {actual:,} != {expected:,}")

    for source_name, destination_name, source_type, target_type, expected in (
        ("space_license_edges.csv", "15_space_license_edges.csv", "space", "license", 420_369),
        ("space_task_edges.csv", "16_space_task_edges.csv", "space", "task", 43_223),
        ("space_agent_edges.csv", "17_space_agent_edges.csv", "space", "agent", 37_059),
    ):
        actual = copy_core(
            workspace / source_name,
            output / destination_name,
            source_type,
            target_type,
        )
        if actual != expected:
            raise ValueError(f"Unexpected {destination_name} count: {actual:,} != {expected:,}")

    print(f"Prepared 17 subgraphs in {output}")
    print(f"Unavailable Spaces excluded: {len(unavailable)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
