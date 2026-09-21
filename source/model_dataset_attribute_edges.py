#!/usr/bin/env python3
"""Build combined model+dataset library, license, task, and GitHub subgraphs.


"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Iterator

from model_library_edges import (
    KNOWN_LIBRARY_TAGS,
    candidate_libraries,
    normalize_library,
)
from model_license_task_github_edges import (
    LICENSE_ALIASES,
    TASK_ALIASES,
    candidate_github,
    candidate_licenses,
    candidate_tasks,
    normalize_github_query,
    normalize_identifier,
    normalize_field_name,
    string_values,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_INPUT = SCRIPT_DIR / "all_dataset_readme_metadata_2026Aug28.jsonl"
SIMPLE_FIELDS = ["source", "edge_type", "target"]
DETAILED_FIELDS = SIMPLE_FIELDS + ["evidence", "confidence"]

RELATIONS = {
    "library": {
        "model_input": SCRIPT_DIR / "model_library_edges_detailed.csv",
        "stem": "model_dataset_library_edges",
        "target_prefix": "library::",
        "dataset_edge_type": "uses_library",
    },
    "license": {
        "model_input": SCRIPT_DIR / "model_license_edges_detailed.csv",
        "stem": "model_dataset_license_edges",
        "target_prefix": "license::",
        "dataset_edge_type": "has_license",
    },
    "task": {
        "model_input": SCRIPT_DIR / "model_task_edges_detailed.csv",
        "stem": "model_dataset_task_edges",
        "target_prefix": "task::",
        "dataset_edge_type": "supports_task",
    },
    "github": {
        "model_input": SCRIPT_DIR / "model_github_edges_detailed.csv",
        "stem": "model_dataset_github_edges",
        "target_prefix": "github::",
        "dataset_edge_type": "links_to_github",
    },
}

DATASET_LIBRARY_TAGS = KNOWN_LIBRARY_TAGS | {
    "dask", "datasets", "duckdb", "mlcroissant", "numpy", "pandas",
    "parquet", "polars", "pyarrow", "spark", "webdataset",
}
DATASET_LIBRARY_FIELDS = {"library_names", "librarys"}

DATASET_LICENSE_FIELDS = {
    "bigbio_license_shortname",
    "bigbio_license_short_name",
    "license_policy",
    "expert_generated_license",
    "annotations_and_code_license",
    "spdx_license_identifier",
    "source_licence_variant",
}
DATASET_LICENSE_ALIASES = {
    **LICENSE_ALIASES,
    "apache_2p0": "apache-2.0",
    "gpl_3p0": "gpl-3.0",
    "cc_by_4p0": "cc-by-4.0",
    "cc_by_nc_4p0": "cc-by-nc-4.0",
    "cc_by_nc_sa_4p0": "cc-by-nc-sa-4.0",
    "mit only": "mit",
}

DATASET_TASK_FIELDS = {
    "bigbio_tasks",
    "subtasks",
    "sub_tasks",
    "task_types",
    "task_category",
    "supported_tasks",
    "taskcategories",
    "fine_grained_tasks",
    "subtask_categories",
    "custom_task",
    "task_names",
    "primary_task",
    "secondary_tasks",
    "task_family",
}

DATASET_DIRECT_GITHUB_FIELDS = {
    "github", "github_link", "source_github", "repo_url", "repository",
    "repo", "source", "source_url", "challenge_repository", "code",
    "source_code", "source_code_repository", "repository_code",
    "restored_repository", "source_repository", "upstream_repository",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or query four combined model+dataset attribute subgraphs."
    )
    parser.add_argument("--dataset-input", type=Path, default=DEFAULT_DATASET_INPUT)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    query = parser.add_mutually_exclusive_group()
    query.add_argument("--query-library", metavar="LIBRARY")
    query.add_argument("--query-license", metavar="LICENSE")
    query.add_argument("--query-task", metavar="TASK")
    query.add_argument("--query-github", metavar="OWNER/REPOSITORY")
    query.add_argument(
        "--query-source",
        metavar="TYPED_ID",
        help="List all four attributes for model::id or dataset::id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum query results to print; 0 prints all (default: 20).",
    )
    return parser.parse_args()


def relation_paths(output_dir: Path, relation: str) -> dict[str, Path]:
    stem = RELATIONS[relation]["stem"]
    return {
        "csv": output_dir / f"{stem}.csv",
        "jsonl": output_dir / f"{stem}.jsonl",
        "detailed_csv": output_dir / f"{stem}_detailed.csv",
        "detailed_jsonl": output_dir / f"{stem}_detailed.jsonl",
    }


def add_candidate(
    candidates: dict[str, tuple[str, str]],
    value: str | None,
    evidence: str,
    confidence: str,
) -> None:
    if value is not None and value not in candidates:
        candidates[value] = (evidence, confidence)


def candidate_dataset_libraries(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates = candidate_libraries(metadata)
    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        if field in DATASET_LIBRARY_FIELDS:
            for raw in string_values(raw_value):
                add_candidate(
                    candidates,
                    normalize_library(raw),
                    field,
                    "declared_nonstandard",
                )
    for raw in string_values(metadata.get("tags")):
        library = normalize_library(raw)
        if library in DATASET_LIBRARY_TAGS:
            add_candidate(candidates, library, "tags", "tag_association")
    return candidates


def license_values(value: object) -> Iterator[str]:
    yield from string_values(value)
    if isinstance(value, dict):
        for key in ("id", "spdx", "name", "license"):
            yield from string_values(value.get(key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                yield from license_values(item)


def candidate_dataset_licenses(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates = candidate_licenses(metadata)
    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        if field == "license_info" or field in DATASET_LICENSE_FIELDS:
            for raw in license_values(raw_value):
                add_candidate(
                    candidates,
                    normalize_identifier(raw, DATASET_LICENSE_ALIASES),
                    field,
                    "declared_nonstandard",
                )
    return candidates


def nested_task_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            yield cleaned
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from nested_task_values(item)
    elif isinstance(value, dict):
        conventional = value.get("type") or value.get("id") or value.get("name")
        if conventional is not None:
            yield from nested_task_values(conventional)
        else:
            for key, item in value.items():
                yield str(key)
                yield from nested_task_values(item)


def candidate_dataset_tasks(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates = candidate_tasks(metadata)
    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        if field in DATASET_TASK_FIELDS:
            for raw in nested_task_values(raw_value):
                add_candidate(
                    candidates,
                    normalize_identifier(raw, TASK_ALIASES),
                    field,
                    "declared_nonstandard",
                )
    return candidates


def candidate_dataset_github(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates = candidate_github(metadata)
    upgraded = {}
    for repository, (evidence, confidence) in candidates.items():
        top_field = evidence.split(".", 1)[0]
        if top_field in DATASET_DIRECT_GITHUB_FIELDS:
            upgraded[repository] = (evidence, "declared_link")
        else:
            upgraded[repository] = (evidence, confidence)
    return upgraded


DATASET_EXTRACTORS = {
    "library": candidate_dataset_libraries,
    "license": candidate_dataset_licenses,
    "task": candidate_dataset_tasks,
    "github": candidate_dataset_github,
}


def validate_generation_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    dataset_input = args.dataset_input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not dataset_input.is_file():
        raise FileNotFoundError(f"Dataset metadata not found: {dataset_input}")
    for relation, config in RELATIONS.items():
        if not config["model_input"].is_file():
            raise FileNotFoundError(
                f"Model {relation} detailed input not found: {config['model_input']}"
            )
    outputs = [
        path.resolve()
        for relation in RELATIONS
        for path in relation_paths(output_dir, relation).values()
    ]
    existing = [path for path in outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing output files:\n"
            + "\n".join(str(path) for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    return dataset_input, output_dir


def write_edge(
    handles: dict[str, object],
    writers: dict[str, csv.DictWriter],
    row: dict[str, str],
) -> None:
    simple = {key: row[key] for key in SIMPLE_FIELDS}
    detailed = {key: row[key] for key in DETAILED_FIELDS}
    writers["csv"].writerow(simple)
    handles["jsonl"].write(
        json.dumps(simple, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    writers["detailed_csv"].writerow(detailed)
    handles["detailed_jsonl"].write(
        json.dumps(detailed, ensure_ascii=False, separators=(",", ":")) + "\n"
    )


def generate(args: argparse.Namespace) -> None:
    dataset_input, output_dir = validate_generation_paths(args)
    counters = {relation: Counter() for relation in RELATIONS}
    target_counts = {relation: Counter() for relation in RELATIONS}

    with ExitStack() as stack:
        handles: dict[str, dict[str, object]] = {}
        writers: dict[str, dict[str, csv.DictWriter]] = {}
        for relation in RELATIONS:
            paths = relation_paths(output_dir, relation)
            handles[relation] = {
                "csv": stack.enter_context(paths["csv"].open("x", newline="", encoding="utf-8")),
                "jsonl": stack.enter_context(paths["jsonl"].open("x", encoding="utf-8")),
                "detailed_csv": stack.enter_context(
                    paths["detailed_csv"].open("x", newline="", encoding="utf-8")
                ),
                "detailed_jsonl": stack.enter_context(
                    paths["detailed_jsonl"].open("x", encoding="utf-8")
                ),
            }
            writers[relation] = {
                "csv": csv.DictWriter(handles[relation]["csv"], fieldnames=SIMPLE_FIELDS),
                "detailed_csv": csv.DictWriter(
                    handles[relation]["detailed_csv"], fieldnames=DETAILED_FIELDS
                ),
            }
            writers[relation]["csv"].writeheader()
            writers[relation]["detailed_csv"].writeheader()

        # Reuse the already-generated, validated model edge sets exactly.
        for relation, config in RELATIONS.items():
            previous_source = None
            with config["model_input"].open(newline="", encoding="utf-8") as model_file:
                reader = csv.DictReader(model_file)
                if reader.fieldnames != DETAILED_FIELDS:
                    raise ValueError(
                        f"Unexpected schema in {config['model_input']}: {reader.fieldnames}"
                    )
                for row in reader:
                    write_edge(handles[relation], writers[relation], row)
                    counters[relation]["model_edges"] += 1
                    counters[relation][f"model_confidence:{row['confidence']}"] += 1
                    if row["source"] != previous_source:
                        counters[relation]["model_nodes"] += 1
                        previous_source = row["source"]
                    target_counts[relation][row["target"]] += 1

        with dataset_input.open(encoding="utf-8") as dataset_file:
            for line_number, line in enumerate(dataset_file, start=1):
                if not line.strip():
                    continue
                for relation in RELATIONS:
                    counters[relation]["dataset_records"] += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    for relation in RELATIONS:
                        counters[relation]["invalid_json"] += 1
                    print(f"Skipping invalid JSON at line {line_number}: {exc}")
                    continue
                dataset_id = record.get("datasetId")
                metadata = record.get("readme_metadata")
                if not isinstance(dataset_id, str) or not dataset_id.strip():
                    for relation in RELATIONS:
                        counters[relation]["invalid_dataset_id"] += 1
                    continue
                if not isinstance(metadata, dict) or not metadata:
                    continue
                dataset_id = dataset_id.strip()
                for relation, extractor in DATASET_EXTRACTORS.items():
                    counters[relation]["dataset_metadata_records"] += 1
                    candidates = extractor(metadata)
                    if not candidates:
                        continue
                    counters[relation]["dataset_nodes"] += 1
                    if len(candidates) > 1:
                        counters[relation]["dataset_nodes_multiple"] += 1
                    config = RELATIONS[relation]
                    for target, (evidence, confidence) in sorted(candidates.items()):
                        row = {
                            "source": f"dataset::{dataset_id}",
                            "edge_type": config["dataset_edge_type"],
                            "target": f"{config['target_prefix']}{target}",
                            "evidence": evidence,
                            "confidence": confidence,
                        }
                        write_edge(handles[relation], writers[relation], row)
                        counters[relation]["dataset_edges"] += 1
                        counters[relation][f"dataset_confidence:{confidence}"] += 1
                        target_counts[relation][row["target"]] += 1

    for relation in RELATIONS:
        stats = counters[relation]
        model_nodes = stats["model_nodes"]
        dataset_nodes = stats["dataset_nodes"]
        total_edges = stats["model_edges"] + stats["dataset_edges"]
        attribute_nodes = len(target_counts[relation])
        print(f"\n## Combined {relation} subgraph")
        print(f"Model source nodes:          {model_nodes:12,}")
        print(f"Dataset source nodes:        {dataset_nodes:12,}")
        print(f"Unique attribute nodes:      {attribute_nodes:12,}")
        print(f"Total unique nodes:          {model_nodes + dataset_nodes + attribute_nodes:12,}")
        print(f"Model edges:                 {stats['model_edges']:12,}")
        print(f"Dataset edges:               {stats['dataset_edges']:12,}")
        print(f"Total unique edges:          {total_edges:12,}")
        print(f"Datasets with multiple:      {stats['dataset_nodes_multiple']:12,}")
        print("Evidence-edge counts:")
        for key in sorted(stats):
            if "confidence:" in key:
                print(f"  {key:38} {stats[key]:12,}")
        print(f"Most common {relation} targets:")
        for target, count in target_counts[relation].most_common(15):
            print(f"  {target:45} {count:12,}")
        paths = relation_paths(output_dir, relation)
        print(f"CSV:            {paths['csv']}")
        print(f"JSONL:          {paths['jsonl']}")
        print(f"Detailed CSV:   {paths['detailed_csv']}")
        print(f"Detailed JSONL: {paths['detailed_jsonl']}")
    print(f"\nDataset records scanned: {counters['library']['dataset_records']:,}")
    print(
        "Dataset records with metadata: "
        f"{counters['library']['dataset_metadata_records']:,}"
    )
    print(f"Invalid dataset IDs: {counters['library']['invalid_dataset_id']:,}")
    print(f"Invalid JSON lines: {counters['library']['invalid_json']:,}")


def read_results(
    path: Path,
    source: str | None = None,
    target: str | None = None,
) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Combined edge CSV not found: {path}")
    results = []
    with path.open(newline="", encoding="utf-8") as input_file:
        for row in csv.DictReader(input_file):
            if source is not None and row.get("source") == source:
                results.append(row["target"])
            elif target is not None and row.get("target") == target:
                results.append(row["source"])
    return sorted(set(results), key=str.lower)


def print_results(results: list[str], limit: int) -> None:
    shown = results if limit == 0 else results[:limit]
    print(f"Distinct results: {len(results):,}")
    print(f"Results shown: {len(shown):,}/{len(results):,}")
    for result in shown:
        print(result)


def query(args: argparse.Namespace) -> None:
    if args.limit < 0:
        raise ValueError("--limit cannot be negative")
    output_dir = args.output_dir.expanduser().resolve()
    if args.query_source is not None:
        source = args.query_source.strip()
        if not source.startswith(("model::", "dataset::")):
            raise ValueError("--query-source must start with model:: or dataset::")
        print(f"Source: {source}")
        for relation in RELATIONS:
            print(f"\n{relation.capitalize()} targets")
            print_results(
                read_results(relation_paths(output_dir, relation)["csv"], source=source),
                args.limit,
            )
        return

    if args.query_library is not None:
        relation = "library"
        value = normalize_library(args.query_library)
    elif args.query_license is not None:
        relation = "license"
        value = normalize_identifier(args.query_license, DATASET_LICENSE_ALIASES)
    elif args.query_task is not None:
        relation = "task"
        value = normalize_identifier(args.query_task, TASK_ALIASES)
    else:
        relation = "github"
        value = normalize_github_query(args.query_github)
    if value is None:
        raise ValueError(f"Invalid {relation} query")
    target = f"{RELATIONS[relation]['target_prefix']}{value}"
    print(f"Target: {target}")
    print_results(
        read_results(relation_paths(output_dir, relation)["csv"], target=target),
        args.limit,
    )


def main() -> int:
    args = parse_args()
    try:
        if any(
            value is not None
            for value in (
                args.query_library,
                args.query_license,
                args.query_task,
                args.query_github,
                args.query_source,
            )
        ):
            query(args)
        else:
            generate(args)
    except KeyboardInterrupt:
        print("Interrupted; move incomplete output files before rerunning.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
