#!/usr/bin/env python3
"""Create validated dataset-to-model ``trained_on`` edges.

The training relationship is declared by model-card metadata (the standard
``datasets`` field or ``dataset:``/``datasets:`` tags). The downloaded dataset
metadata supplies the authoritative dataset-ID universe used to validate and
canonicalize each source. Dataset-card fields that merely mention a model are
not treated as training evidence because they may describe generation,
evaluation, or provenance instead.

Created: 2026-09-14
Version: v2026.09.14-04

Change history:
2026-09-14 v2026.09.14-04
- Validate and canonicalize dataset sources against downloaded dataset metadata.
- Resolve case variants, Hub URLs, path suffixes, and unique bare names.
- Add configurable paths and detailed coverage accounting.
- Write new validated outputs by default, preserving previous files.
- Backup: dataset_model_edges.py.bak.20260914-163220
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_METADATA = SCRIPT_DIR / "all_model_readme_metadata_2026Aug28.jsonl"
DEFAULT_DATASET_METADATA = SCRIPT_DIR / "all_dataset_readme_metadata_2026Aug28.jsonl"
DEFAULT_CSV = SCRIPT_DIR / "dataset_model_edges_validated.csv"
DEFAULT_JSONL = SCRIPT_DIR / "dataset_model_edges_validated.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create dataset -> trained_on -> model edges from model-card "
            "declarations, validated against downloaded dataset metadata."
        )
    )
    parser.add_argument("--model-metadata", type=Path, default=DEFAULT_MODEL_METADATA)
    parser.add_argument("--dataset-metadata", type=Path, default=DEFAULT_DATASET_METADATA)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    return parser.parse_args()


def validated_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    model_path = args.model_metadata.expanduser().resolve()
    dataset_path = args.dataset_metadata.expanduser().resolve()
    csv_path = args.csv.expanduser().resolve()
    jsonl_path = args.jsonl.expanduser().resolve()

    for path, label in ((model_path, "model metadata"), (dataset_path, "dataset metadata")):
        if not path.is_file():
            raise FileNotFoundError(f"{label.title()} file not found: {path}")
    if len({model_path, dataset_path, csv_path, jsonl_path}) != 4:
        raise ValueError("Input and output paths must all be different")
    for path in (csv_path, jsonl_path):
        if path.exists():
            raise FileExistsError(
                f"Output already exists: {path}\n"
                "Choose another path with --csv/--jsonl, or move the old "
                "output before rerunning."
            )
    return model_path, dataset_path, csv_path, jsonl_path


def scalar_strings(value: object) -> tuple[list[str], int]:
    """Return nonempty string values and a count of invalid members."""
    if isinstance(value, str):
        cleaned = value.strip()
        return ([cleaned] if cleaned else []), 0
    if isinstance(value, (list, tuple, set)):
        strings = []
        invalid = 0
        for item in value:
            if isinstance(item, str) and item.strip():
                strings.append(item.strip())
            elif item not in (None, ""):
                invalid += 1
        return strings, invalid
    if value in (None, ""):
        return [], 0
    return [], 1


def dataset_tags(value: object) -> tuple[list[str], int]:
    """Extract values from ``dataset:`` and ``datasets:`` card tags."""
    tags, invalid = scalar_strings(value)
    datasets = []
    for tag in tags:
        normalized = tag.lower()
        if normalized.startswith("dataset:"):
            dataset_id = tag.split(":", 1)[1].strip()
        elif normalized.startswith("datasets:"):
            dataset_id = tag.split(":", 1)[1].strip()
        else:
            continue
        if dataset_id:
            datasets.append(dataset_id)
    return datasets, invalid


def load_dataset_universe(
    path: Path,
) -> tuple[set[str], dict[str, str], dict[str, str], Counter[str], int]:
    """Load canonical dataset IDs plus safe lookup indexes."""
    exact_ids: set[str] = set()
    lowercase_ids: dict[str, str] = {}
    # A value of None marks a basename shared by multiple dataset IDs.
    basename_candidates: dict[str, str | None] = {}
    statuses: Counter[str] = Counter()
    invalid_json = 0

    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid_json += 1
                print(f"Skipping invalid dataset JSON at line {line_number}: {exc}")
                continue
            statuses[str(record.get("status", "unknown"))] += 1
            dataset_id = record.get("datasetId")
            if not isinstance(dataset_id, str) or not dataset_id.strip():
                continue
            canonical = dataset_id.strip()
            exact_ids.add(canonical)
            lowercase_ids.setdefault(canonical.lower(), canonical)
            basename = canonical.rsplit("/", 1)[-1].lower()
            if basename not in basename_candidates:
                basename_candidates[basename] = canonical
            elif basename_candidates[basename] != canonical:
                basename_candidates[basename] = None

    unique_basenames = {
        basename: canonical
        for basename, canonical in basename_candidates.items()
        if canonical is not None
    }
    return exact_ids, lowercase_ids, unique_basenames, statuses, invalid_json


def clean_dataset_reference(value: str) -> tuple[str, bool]:
    """Return a trimmed dataset reference and whether it came from a URL."""
    candidate = value.strip().strip("'\"")
    parsed = urlparse(candidate)
    if not (parsed.scheme or parsed.netloc):
        return candidate.strip("/"), False
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co",
    }:
        return "", True
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if parts and parts[0].lower() == "datasets":
        parts = parts[1:]
    if len(parts) < 2:
        return "", True
    return "/".join(parts), True


def resolve_dataset_id(
    value: str,
    exact_ids: set[str],
    lowercase_ids: dict[str, str],
    unique_basenames: dict[str, str],
) -> tuple[str | None, str]:
    """Resolve one card value to a canonical downloaded dataset ID."""
    candidate, was_url = clean_dataset_reference(value)
    if not candidate:
        return None, "unresolved"
    if candidate in exact_ids:
        return candidate, "url" if was_url else "exact"
    canonical = lowercase_ids.get(candidate.lower())
    if canonical is not None:
        return canonical, "url" if was_url else "case_normalized"

    # Some cards append a config or path to owner/repository. Strip it only
    # when the resulting owner/repository exists in the downloaded universe.
    parts = candidate.split("/")
    if len(parts) > 2:
        canonical = lowercase_ids.get("/".join(parts[:2]).lower())
        if canonical is not None:
            return canonical, "suffix_removed"

    # Resolve a bare repository name only when exactly one downloaded dataset
    # has that basename. Ambiguous bare names remain unresolved.
    if "/" not in candidate:
        canonical = unique_basenames.get(candidate.lower())
        if canonical is not None:
            return canonical, "unique_bare_name"
    return None, "unresolved"


def write_edges(
    model_path: Path,
    csv_path: Path,
    jsonl_path: Path,
    exact_ids: set[str],
    lowercase_ids: dict[str, str],
    unique_basenames: dict[str, str],
) -> Counter[str]:
    counters: Counter[str] = Counter()
    seen_edges: set[tuple[str, str, str]] = set()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        model_path.open(encoding="utf-8") as input_file,
        csv_path.open("x", encoding="utf-8", newline="") as csv_file,
        jsonl_path.open("x", encoding="utf-8") as jsonl_file,
    ):
        csv_writer = csv.DictWriter(
            csv_file, fieldnames=["source", "edge_type", "target"]
        )
        csv_writer.writeheader()

        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            counters["model_records"] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                counters["invalid_model_json"] += 1
                print(f"Skipping invalid model JSON at line {line_number}: {exc}")
                continue

            target = record.get("modelId")
            metadata = record.get("readme_metadata")
            if not isinstance(target, str) or not target.strip():
                counters["invalid_model_ids"] += 1
                continue
            if not isinstance(metadata, dict) or not metadata:
                continue

            target = target.strip()
            counters["models_with_metadata"] += 1
            candidates: dict[str, str] = {}
            raw_datasets = metadata.get("datasets")
            if raw_datasets is not None:
                values, invalid = scalar_strings(raw_datasets)
                counters["invalid_dataset_values"] += invalid
                for value in values:
                    candidates.setdefault(value, "datasets_field")

            tag_values, invalid_tags = dataset_tags(metadata.get("tags"))
            counters["invalid_tag_values"] += invalid_tags
            for value in tag_values:
                candidates.setdefault(value, "dataset_tag")

            resolved_for_model: dict[str, str] = {}
            for raw_source, evidence in candidates.items():
                counters["candidate_references"] += 1
                source, resolution = resolve_dataset_id(
                    raw_source, exact_ids, lowercase_ids, unique_basenames
                )
                counters[f"resolution_{resolution}"] += 1
                if source is not None:
                    # Prefer the standard field if field and tag resolve to
                    # the same dataset for this model.
                    resolved_for_model.setdefault(source, evidence)

            model_has_edge = False
            for source, evidence in resolved_for_model.items():
                edge = (source, "trained_on", target)
                if edge in seen_edges:
                    counters["duplicate_edges"] += 1
                    continue
                seen_edges.add(edge)
                model_has_edge = True
                row = {"source": source, "edge_type": "trained_on", "target": target}
                csv_writer.writerow(row)
                jsonl_file.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                counters[f"edges_{evidence}"] += 1
            if model_has_edge:
                counters["models_with_edges"] += 1

    counters["unique_edges"] = len(seen_edges)
    return counters


def print_report(
    statuses: Counter[str],
    dataset_ids: int,
    invalid_dataset_json: int,
    counters: Counter[str],
    csv_path: Path,
    jsonl_path: Path,
) -> None:
    print("\nDataset universe\n----------------")
    print(f"Unique dataset IDs:             {dataset_ids:12,}")
    print(f"With dataset-card metadata:     {statuses.get('with_metadata', 0):12,}")
    print(f"Empty dataset-card metadata:    {statuses.get('no_metadata', 0):12,}")
    print(
        "Not returned by datasets API:   "
        f"{statuses.get('not_returned_by_datasets_api', 0):12,}"
    )
    print(f"Invalid dataset JSON lines:     {invalid_dataset_json:12,}")

    print("\nModel-card relationship extraction\n----------------------------------")
    print(f"Model records scanned:           {counters['model_records']:12,}")
    print(f"Models with metadata:            {counters['models_with_metadata']:12,}")
    print(f"Candidate dataset references:    {counters['candidate_references']:12,}")
    print(f"  Exact dataset IDs:             {counters['resolution_exact']:12,}")
    print(f"  Hugging Face URLs:             {counters['resolution_url']:12,}")
    print(f"  Case-normalized IDs:           {counters['resolution_case_normalized']:12,}")
    print(f"  Config/path suffix removed:    {counters['resolution_suffix_removed']:12,}")
    print(f"  Unique bare-name matches:      {counters['resolution_unique_bare_name']:12,}")
    print(f"  Unresolved references omitted: {counters['resolution_unresolved']:12,}")
    print(f"Models with validated edges:     {counters['models_with_edges']:12,}")
    print(f"Dataset-field edges:             {counters['edges_datasets_field']:12,}")
    print(f"Tag-only dataset edges:          {counters['edges_dataset_tag']:12,}")
    print(f"Total unique edges:              {counters['unique_edges']:12,}")
    print(f"Duplicate edges skipped:         {counters['duplicate_edges']:12,}")
    print(f"Invalid dataset values:          {counters['invalid_dataset_values']:12,}")
    print(f"Invalid tag members:             {counters['invalid_tag_values']:12,}")
    print(f"Invalid model IDs:               {counters['invalid_model_ids']:12,}")
    print(f"Invalid model JSON lines:        {counters['invalid_model_json']:12,}")
    print(f"\nCSV:   {csv_path}\nJSONL: {jsonl_path}")


def main() -> int:
    try:
        model_path, dataset_path, csv_path, jsonl_path = validated_paths(parse_args())
        exact_ids, lowercase_ids, unique_basenames, statuses, invalid_dataset_json = (
            load_dataset_universe(dataset_path)
        )
        counters = write_edges(
            model_path, csv_path, jsonl_path,
            exact_ids, lowercase_ids, unique_basenames,
        )
        print_report(
            statuses, len(exact_ids), invalid_dataset_json,
            counters, csv_path, jsonl_path,
        )
    except KeyboardInterrupt:
        print("Interrupted; incomplete outputs must be moved before rerunning.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
