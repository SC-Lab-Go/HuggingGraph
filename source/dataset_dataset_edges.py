#!/usr/bin/env python3
"""Create dataset -> derived_from -> dataset lineage edges."""

import csv
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent

INPUT_PATH = ROOT / "all_dataset_readme_metadata_2026Aug28.jsonl"

VALIDATED_CSV = ROOT / "dataset_dataset_edges_validated.csv"
VALIDATED_JSONL = ROOT / "dataset_dataset_edges_validated.jsonl"

UNRESOLVED_CSV = ROOT / "dataset_dataset_edges_unresolved.csv"
UNRESOLVED_JSONL = ROOT / "dataset_dataset_edges_unresolved.jsonl"

MAX_COVERAGE_CSV = ROOT / "dataset_dataset_edges_max_coverage.csv"
MAX_COVERAGE_JSONL = ROOT / "dataset_dataset_edges_max_coverage.jsonl"


# Exact metadata fields that can identify an upstream/parent dataset.
# Field names are normalized to lowercase with punctuation replaced by "_".
PARENT_FIELDS = {
    "source_dataset",
    "source_datasets",
    "source_dataasets",       # observed misspelling
    "derived_from",
    "parent_dataset",
    "parent_datasets",
    "base_dataset",
    "base_datasets",
    "dataset_source",
    "dataset_sources",
    "original_dataset",
    "original_dataset_id",
    "original_source",
    "prov_wasderivedfrom",
    "datasets",
}

# Relevant keys when a parent field contains dictionaries.
REFERENCE_KEYS = {
    "id",
    "dataset",
    "dataset_id",
    "datasetid",
    "repo_id",
    "repository",
    "name",
    "path",
    "url",
}

ID_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def normalize_field_name(value):
    """Normalize different spellings of a metadata field."""
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value).strip().lower(),
    ).strip("_")


def reference_strings(value):
    """Extract possible dataset references from strings, lists, and dictionaries."""
    if isinstance(value, str):
        value = value.strip()
        if value:
            yield value
        return

    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from reference_strings(item)
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if normalize_field_name(key) in REFERENCE_KEYS:
                yield from reference_strings(item)


def clean_reference(value):
    """Clean a dataset ID or Hugging Face dataset URL."""
    candidate = value.strip().strip("'\"`")

    for prefix in ("dataset:", "datasets:"):
        if candidate.lower().startswith(prefix):
            candidate = candidate.split(":", 1)[1].strip()
            break

    parsed = urlparse(candidate)

    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"}:
            return None

        if parsed.netloc.lower() not in {
            "huggingface.co",
            "www.huggingface.co",
            "hf.co",
            "www.hf.co",
        }:
            return None

        parts = [
            unquote(part)
            for part in parsed.path.split("/")
            if part
        ]

        if parts and parts[0].lower() == "datasets":
            parts = parts[1:]

        if len(parts) < 2:
            return None

        candidate = "/".join(parts)

    return candidate.strip("/")


def plausible_hf_dataset_id(value):
    """Check whether a value has a plausible owner/dataset form."""
    if not isinstance(value, str):
        return False

    if len(value) > 200 or value.count("/") != 1:
        return False

    owner, repository = value.split("/", 1)

    for part in (owner, repository):
        if not part or not ID_PART.fullmatch(part):
            return False
        if part[-1] in ".-":
            return False
        if "--" in part or ".." in part:
            return False

    return not repository.endswith(".git")


def load_dataset_universe(path):
    """Load canonical dataset IDs and indexes for safe normalization."""
    exact_ids = set()
    lowercase_ids = {}
    basename_candidates = {}

    status_counts = {}
    records_scanned = 0
    invalid_json = 0

    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue

            records_scanned += 1

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid_json += 1
                print(
                    f"Skipping invalid JSON at line {line_number}: {exc}"
                )
                continue

            status = str(record.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1

            dataset_id = record.get("datasetId")

            if not isinstance(dataset_id, str) or not dataset_id.strip():
                continue

            dataset_id = dataset_id.strip()
            exact_ids.add(dataset_id)
            lowercase_ids.setdefault(dataset_id.lower(), dataset_id)

            basename = dataset_id.rsplit("/", 1)[-1].lower()

            if basename not in basename_candidates:
                basename_candidates[basename] = dataset_id
            elif basename_candidates[basename] != dataset_id:
                # None means the bare name is ambiguous.
                basename_candidates[basename] = None

    unique_basenames = {
        basename: dataset_id
        for basename, dataset_id in basename_candidates.items()
        if dataset_id is not None
    }

    return (
        exact_ids,
        lowercase_ids,
        unique_basenames,
        status_counts,
        records_scanned,
        invalid_json,
    )


def resolve_dataset_reference(
    value,
    exact_ids,
    lowercase_ids,
    unique_basenames,
):
    """
    Return (source, category).

    category:
      validated  - matched downloaded dataset universe
      unresolved - plausible owner/dataset ID absent from universe
      rejected   - descriptive, ambiguous, malformed, or external value
    """
    candidate = clean_reference(value)

    if not candidate:
        return None, "rejected"

    if candidate in exact_ids:
        return candidate, "validated"

    canonical = lowercase_ids.get(candidate.lower())

    if canonical is not None:
        return canonical, "validated"

    # Handle owner/dataset/config or owner/dataset/path.
    parts = candidate.split("/")

    if len(parts) > 2:
        possible_repository = "/".join(parts[:2])
        canonical = lowercase_ids.get(possible_repository.lower())

        if canonical is not None:
            return canonical, "validated"

        # Do not retain arbitrary multi-component paths.
        return None, "rejected"

    # Resolve a bare name only when exactly one downloaded dataset has it.
    if "/" not in candidate:
        canonical = unique_basenames.get(candidate.lower())

        if canonical is not None:
            return canonical, "validated"

        # Values such as "original", "wikipedia", descriptions, filenames,
        # and ambiguous dataset names are not treated as graph nodes.
        return None, "rejected"

    # Maximum-coverage tier: preserve a plausible owner/dataset reference
    # even if it was absent from the downloaded snapshot.
    if plausible_hf_dataset_id(candidate):
        return candidate, "unresolved"

    return None, "rejected"


def extract_edges(
    path,
    exact_ids,
    lowercase_ids,
    unique_basenames,
):
    validated_edges = set()
    unresolved_edges = set()

    counters = {
        "records_scanned": 0,
        "records_with_metadata": 0,
        "candidate_values": 0,
        "validated_candidates": 0,
        "unresolved_candidates": 0,
        "rejected_candidates": 0,
        "self_edges": 0,
        "invalid_json": 0,
    }

    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue

            counters["records_scanned"] += 1

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                counters["invalid_json"] += 1
                print(
                    f"Skipping invalid JSON at line {line_number}: {exc}"
                )
                continue

            target = record.get("datasetId")
            metadata = record.get("readme_metadata")

            if not isinstance(target, str) or not target.strip():
                continue

            if not isinstance(metadata, dict) or not metadata:
                continue

            target = target.strip()
            counters["records_with_metadata"] += 1

            candidates = set()

            for raw_field, value in metadata.items():
                field = normalize_field_name(raw_field)

                if field not in PARENT_FIELDS:
                    continue

                for candidate in reference_strings(value):
                    candidates.add(candidate)

            for candidate in candidates:
                counters["candidate_values"] += 1

                source, category = resolve_dataset_reference(
                    candidate,
                    exact_ids,
                    lowercase_ids,
                    unique_basenames,
                )

                if source is None:
                    counters["rejected_candidates"] += 1
                    continue

                if source.lower() == target.lower():
                    counters["self_edges"] += 1
                    continue

                edge = (source, "derived_from", target)

                if category == "validated":
                    validated_edges.add(edge)
                    counters["validated_candidates"] += 1
                else:
                    unresolved_edges.add(edge)
                    counters["unresolved_candidates"] += 1

    # If an edge appears in both categories, prefer validated.
    unresolved_edges.difference_update(validated_edges)

    return validated_edges, unresolved_edges, counters


def write_outputs(edges, csv_path, jsonl_path):
    """Write identical edge sets to CSV and JSONL."""
    for output_path in (csv_path, jsonl_path):
        if output_path.exists():
            raise FileExistsError(
                f"Output already exists: {output_path}\n"
                "Move or rename it before running the script again."
            )

    ordered_edges = sorted(edges)

    with (
        csv_path.open("x", encoding="utf-8", newline="") as csv_file,
        jsonl_path.open("x", encoding="utf-8") as jsonl_file,
    ):
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["source", "edge_type", "target"],
        )
        writer.writeheader()

        for source, edge_type, target in ordered_edges:
            row = {
                "source": source,
                "edge_type": edge_type,
                "target": target,
            }

            writer.writerow(row)
            jsonl_file.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )


def endpoint_counts(edges):
    sources = {source for source, _, _ in edges}
    targets = {target for _, _, target in edges}
    return len(sources), len(targets)


def main():
    (
        exact_ids,
        lowercase_ids,
        unique_basenames,
        status_counts,
        universe_records,
        universe_invalid_json,
    ) = load_dataset_universe(INPUT_PATH)

    validated_edges, unresolved_edges, counters = extract_edges(
        INPUT_PATH,
        exact_ids,
        lowercase_ids,
        unique_basenames,
    )

    maximum_coverage_edges = validated_edges | unresolved_edges

    write_outputs(
        validated_edges,
        VALIDATED_CSV,
        VALIDATED_JSONL,
    )
    write_outputs(
        unresolved_edges,
        UNRESOLVED_CSV,
        UNRESOLVED_JSONL,
    )
    write_outputs(
        maximum_coverage_edges,
        MAX_COVERAGE_CSV,
        MAX_COVERAGE_JSONL,
    )

    validated_sources, validated_targets = endpoint_counts(
        validated_edges
    )
    unresolved_sources, unresolved_targets = endpoint_counts(
        unresolved_edges
    )
    maximum_sources, maximum_targets = endpoint_counts(
        maximum_coverage_edges
    )

    print()
    print("Dataset universe")
    print("----------------")
    print(f"Records scanned:                {universe_records:,}")
    print(f"Unique dataset IDs:             {len(exact_ids):,}")
    print(
        "With metadata:                 "
        f"{status_counts.get('with_metadata', 0):,}"
    )
    print(
        "Empty metadata:                "
        f"{status_counts.get('no_metadata', 0):,}"
    )
    print(
        "Not returned by API:           "
        f"{status_counts.get('not_returned_by_datasets_api', 0):,}"
    )
    print(f"Invalid JSON lines:             {universe_invalid_json:,}")

    print()
    print("Dataset lineage")
    print("---------------")
    print(f"Metadata records examined:      {counters['records_scanned']:,}")
    print(f"Records with metadata:          {counters['records_with_metadata']:,}")
    print(f"Candidate parent values:        {counters['candidate_values']:,}")
    print(f"Rejected descriptive values:    {counters['rejected_candidates']:,}")
    print(f"Self-edges skipped:             {counters['self_edges']:,}")

    print()
    print("Validated")
    print(f"  Edges:                        {len(validated_edges):,}")
    print(f"  Unique parent datasets:       {validated_sources:,}")
    print(f"  Unique child datasets:        {validated_targets:,}")

    print()
    print("Unresolved but plausible")
    print(f"  Edges:                        {len(unresolved_edges):,}")
    print(f"  Unique parent strings:        {unresolved_sources:,}")
    print(f"  Unique child datasets:        {unresolved_targets:,}")

    print()
    print("Maximum coverage")
    print(f"  Edges:                        {len(maximum_coverage_edges):,}")
    print(f"  Unique parent identifiers:    {maximum_sources:,}")
    print(f"  Unique child datasets:        {maximum_targets:,}")

    print()
    print(f"Validated CSV:     {VALIDATED_CSV}")
    print(f"Validated JSONL:   {VALIDATED_JSONL}")
    print(f"Unresolved CSV:    {UNRESOLVED_CSV}")
    print(f"Unresolved JSONL:  {UNRESOLVED_JSONL}")
    print(f"Maximum CSV:       {MAX_COVERAGE_CSV}")
    print(f"Maximum JSONL:     {MAX_COVERAGE_JSONL}")


if __name__ == "__main__":
    main()