#!/usr/bin/env python3
"""Create and query model-to-library edges from saved model-card metadata.

Created: 2026-09-14
Version: v2026.09.14-07
Purpose: Represent model library declarations and recognized library tags as
         ``model -> uses_library -> library`` relationships.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_model_readme_metadata_2026Aug28.jsonl"
DEFAULT_CSV = SCRIPT_DIR / "model_library_edges.csv"
DEFAULT_JSONL = SCRIPT_DIR / "model_library_edges.jsonl"
DEFAULT_DETAILED_CSV = SCRIPT_DIR / "model_library_edges_detailed.csv"
DEFAULT_DETAILED_JSONL = SCRIPT_DIR / "model_library_edges_detailed.jsonl"

EDGE_TYPE = "uses_library"
FIELD_PRIORITY = {
    "library_name": 0,
    "alternative_field": 1,
    "library_tag": 2,
}

# These alternatives were observed in the downloaded card metadata. Generic
# fields such as developmentLibrary are excluded because their values are often
# descriptions rather than one library identifier.
ALTERNATIVE_LIBRARY_FIELDS = {
    "library",
    "libraries",
    "library_tag",
    "library_tags",
    "model_hub_library",
    "libray_name",
    "librairy_name",
    "librar_yname",
    "libraryname",
    "library_name2",
}

# Tags are weaker than library_name. Only recognized library/technology tags
# are accepted, rather than treating every arbitrary card tag as a library.
KNOWN_LIBRARY_TAGS = {
    "adapters", "allennlp", "asteroid", "bertopic", "coreml", "dduf",
    "diffusers", "espnet", "executorch", "fairseq", "fastai", "fasttext",
    "flair", "gguf", "graphcore", "habana", "jax", "joblib", "keras",
    "keras-hub", "litert", "llamafile", "ml-agents", "mlx", "nemo",
    "onnx", "openclip", "openvino", "paddleocr", "paddlenlp",
    "paddlepaddle", "peft", "pyannote.audio", "pytorch", "rust",
    "safetensors", "sample-factory", "scikit-learn", "sentence-transformers",
    "setfit", "spacy", "speechbrain", "stable-baselines3", "stanza",
    "tensorflow", "tf-keras", "timm", "transformers", "transformers.js",
    "tensorboard", "unity-sentis", "univa",
}

LIBRARY_ALIASES = {
    "torch": "pytorch",
    "sentence transformers": "sentence-transformers",
    "sentence_transformers": "sentence-transformers",
    "core ml": "coreml",
    "core-ml": "coreml",
    "scikit_learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "keras_hub": "keras-hub",
    "kerashub": "keras-hub",
    "open_clip": "openclip",
    "tf_keras": "tf-keras",
    "stable_baselines3": "stable-baselines3",
    "pyannote_audio": "pyannote.audio",
    "transformers_js": "transformers.js",
    "llama_cpp": "llama.cpp",
}

LIBRARY_ID = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,79}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or query model -> uses_library -> library edges."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--detailed-csv", type=Path, default=DEFAULT_DETAILED_CSV)
    parser.add_argument(
        "--detailed-jsonl", type=Path, default=DEFAULT_DETAILED_JSONL
    )
    query = parser.add_mutually_exclusive_group()
    query.add_argument(
        "--query-library",
        metavar="LIBRARY",
        help="Count and list models associated with a library.",
    )
    query.add_argument(
        "--query-model",
        metavar="MODEL_ID",
        help="List libraries associated with one model.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum query results to print; 0 prints all (default: 20).",
    )
    return parser.parse_args()


def normalize_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def string_values(value: object) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (list, tuple, set)):
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
    return []


def normalize_library(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower().strip("'\"`")
    if not candidate or "://" in candidate or "\n" in candidate:
        return None
    candidate = LIBRARY_ALIASES.get(candidate, candidate)
    candidate = re.sub(r"[ _]+", "-", candidate)
    candidate = LIBRARY_ALIASES.get(candidate, candidate)
    return candidate if LIBRARY_ID.fullmatch(candidate) else None


def candidate_libraries(metadata: dict) -> dict[str, tuple[str, str]]:
    """Return library -> (evidence, confidence), preferring strong evidence."""
    candidates: dict[str, tuple[str, str]] = {}

    def register(library: str, evidence: str, confidence: str, group: str) -> None:
        current = candidates.get(library)
        if current is None:
            candidates[library] = (evidence, confidence)
            return
        current_group = (
            "library_name"
            if current[0] == "library_name"
            else "library_tag"
            if current[0] == "tags"
            else "alternative_field"
        )
        if FIELD_PRIORITY[group] < FIELD_PRIORITY[current_group]:
            candidates[library] = (evidence, confidence)

    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        if field == "library_name":
            group = "library_name"
            confidence = "declared"
        elif field in ALTERNATIVE_LIBRARY_FIELDS:
            group = "alternative_field"
            confidence = "declared_nonstandard"
        else:
            continue

        for raw_library in string_values(raw_value):
            library = normalize_library(raw_library)
            if library is None:
                continue
            # library_tag fields are custom and receive the same allowlist rule
            # as ordinary tags. Other explicit fields may declare custom tools.
            if field in {"library_tag", "library_tags"} and library not in KNOWN_LIBRARY_TAGS:
                continue
            evidence = "library_name" if group == "library_name" else field
            register(library, evidence, confidence, group)

    for raw_tag in string_values(metadata.get("tags")):
        library = normalize_library(raw_tag)
        if library in KNOWN_LIBRARY_TAGS:
            register(library, "tags", "tag_association", "library_tag")

    return candidates


def output_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    input_path = args.input.expanduser().resolve()
    outputs = tuple(
        path.expanduser().resolve()
        for path in (
            args.csv,
            args.jsonl,
            args.detailed_csv,
            args.detailed_jsonl,
        )
    )
    if not input_path.is_file():
        raise FileNotFoundError(f"Input metadata not found: {input_path}")
    if len({input_path, *outputs}) != 5:
        raise ValueError("Input and output paths must all be different")
    return (input_path, *outputs)


def generate(args: argparse.Namespace) -> None:
    input_path, csv_path, jsonl_path, detailed_csv_path, detailed_jsonl_path = (
        output_paths(args)
    )
    outputs = (csv_path, jsonl_path, detailed_csv_path, detailed_jsonl_path)
    for path in outputs:
        if path.exists():
            raise FileExistsError(
                f"Output already exists: {path}\n"
                "Move it or choose another output path before rerunning."
            )
        path.parent.mkdir(parents=True, exist_ok=True)

    counters: Counter[str] = Counter()
    library_counts: Counter[str] = Counter()
    simple_fields = ["source", "edge_type", "target"]
    detailed_fields = simple_fields + ["evidence", "confidence"]

    with (
        input_path.open(encoding="utf-8") as input_file,
        csv_path.open("x", newline="", encoding="utf-8") as csv_file,
        jsonl_path.open("x", encoding="utf-8") as jsonl_file,
        detailed_csv_path.open("x", newline="", encoding="utf-8") as detailed_csv,
        detailed_jsonl_path.open("x", encoding="utf-8") as detailed_jsonl,
    ):
        csv_writer = csv.DictWriter(csv_file, fieldnames=simple_fields)
        detailed_writer = csv.DictWriter(detailed_csv, fieldnames=detailed_fields)
        csv_writer.writeheader()
        detailed_writer.writeheader()

        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            counters["records"] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                counters["invalid_json"] += 1
                print(f"Skipping invalid JSON at line {line_number}: {exc}")
                continue

            model_id = record.get("modelId")
            metadata = record.get("readme_metadata")
            if not isinstance(model_id, str) or not model_id.strip():
                counters["invalid_model_id"] += 1
                continue
            if not isinstance(metadata, dict) or not metadata:
                continue

            counters["metadata_records"] += 1
            model_id = model_id.strip()
            libraries = candidate_libraries(metadata)
            if not libraries:
                continue

            counters["models_with_library"] += 1
            if len(libraries) > 1:
                counters["models_with_multiple_libraries"] += 1

            for library, (evidence, confidence) in sorted(libraries.items()):
                simple_row = {
                    "source": f"model::{model_id}",
                    "edge_type": EDGE_TYPE,
                    "target": f"library::{library}",
                }
                detailed_row = {
                    **simple_row,
                    "evidence": evidence,
                    "confidence": confidence,
                }
                csv_writer.writerow(simple_row)
                jsonl_file.write(
                    json.dumps(simple_row, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                detailed_writer.writerow(detailed_row)
                detailed_jsonl.write(
                    json.dumps(
                        detailed_row, ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                )
                counters["edges"] += 1
                counters[f"confidence_{confidence}"] += 1
                library_counts[library] += 1

    print("\nModel-library graph")
    print("-------------------")
    print(f"Model records scanned:          {counters['records']:12,}")
    print(f"Records with metadata:          {counters['metadata_records']:12,}")
    print(f"Models with library edges:      {counters['models_with_library']:12,}")
    print(f"Models with multiple libraries: {counters['models_with_multiple_libraries']:12,}")
    print(f"Unique library nodes:           {len(library_counts):12,}")
    print(f"Total unique edges:             {counters['edges']:12,}")
    print(f"Declared-field edges:           {counters['confidence_declared']:12,}")
    print(f"Nonstandard-field edges:        {counters['confidence_declared_nonstandard']:12,}")
    print(f"Tag-association edges:          {counters['confidence_tag_association']:12,}")
    print(f"Invalid model IDs:              {counters['invalid_model_id']:12,}")
    print(f"Invalid JSON lines:             {counters['invalid_json']:12,}")
    print("\nMost common libraries")
    for library, count in library_counts.most_common(20):
        print(f"  {library:28} {count:12,}")
    print(f"\nCSV:            {csv_path}")
    print(f"JSONL:          {jsonl_path}")
    print(f"Detailed CSV:   {detailed_csv_path}")
    print(f"Detailed JSONL: {detailed_jsonl_path}")


def query(args: argparse.Namespace) -> None:
    csv_path = args.csv.expanduser().resolve()
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Edge CSV not found: {csv_path}\nRun the script without a query first."
        )
    if args.limit < 0:
        raise ValueError("--limit cannot be negative")

    results = []
    if args.query_library is not None:
        library = normalize_library(args.query_library)
        if library is None:
            raise ValueError(f"Invalid library query: {args.query_library!r}")
        wanted = f"library::{library}"
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                if row.get("target") == wanted:
                    results.append(row["source"].removeprefix("model::"))
        print(f"Library: {library}")
        print(f"Distinct models: {len(set(results)):,}")
        heading = "Models"
    else:
        model_id = args.query_model.removeprefix("model::").strip()
        wanted = f"model::{model_id}"
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                if row.get("source") == wanted:
                    results.append(row["target"].removeprefix("library::"))
        print(f"Model: {model_id}")
        print(f"Distinct libraries: {len(set(results)):,}")
        heading = "Libraries"

    ordered = sorted(set(results), key=str.lower)
    displayed = ordered if args.limit == 0 else ordered[: args.limit]
    print(f"{heading} shown: {len(displayed):,}/{len(ordered):,}")
    for value in displayed:
        print(value)


def main() -> int:
    args = parse_args()
    try:
        if args.query_library is not None or args.query_model is not None:
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
