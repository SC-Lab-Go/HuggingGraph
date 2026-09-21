#!/usr/bin/env python3
"""Create and query model-license, model-task, and model-GitHub edges.

Created: 2026-09-14
Version: v2026.09.14-08
Purpose: Extract three typed relationship sets from saved Hugging Face
         model-card metadata while preserving evidence strength.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_model_readme_metadata_2026Aug28.jsonl"
SIMPLE_FIELDS = ["source", "edge_type", "target"]
DETAILED_FIELDS = SIMPLE_FIELDS + ["evidence", "confidence"]

RELATIONS = {
    "license": {
        "edge_type": "has_license",
        "target_prefix": "license::",
        "stem": "model_license_edges",
    },
    "task": {
        "edge_type": "performs_task",
        "target_prefix": "task::",
        "stem": "model_task_edges",
    },
    "github": {
        "edge_type": "links_to_github",
        "target_prefix": "github::",
        "stem": "model_github_edges",
    },
}

PRIORITY = {
    "standard": 0,
    "alternative": 1,
    "tag": 2,
    "embedded": 2,
}

ALTERNATIVE_LICENSE_FIELDS = {
    "licenses",
    "additional_licenses",
    "adapter_license",
    "repo_license",
    "anemll_license",
    "sdlicense",
    "name_license",
}

KNOWN_LICENSE_TAGS = {
    "afl-3.0", "apache-2.0", "artistic-2.0", "bsl-1.0", "bsd",
    "bsd-2-clause", "bsd-3-clause", "bsd-3-clause-clear", "c-uda",
    "cc", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0",
    "cc-by-nc-2.0", "cc-by-nc-3.0", "cc-by-nc-4.0",
    "cc-by-nc-nd-3.0", "cc-by-nc-nd-4.0", "cc-by-nc-sa-2.0",
    "cc-by-nc-sa-3.0", "cc-by-nc-sa-4.0", "cc-by-nd-3.0",
    "cc-by-nd-4.0", "cc-by-sa-3.0", "cc-by-sa-4.0", "cc0-1.0",
    "ecl-2.0", "gpl", "gpl-2.0", "gpl-3.0", "isc", "lgpl",
    "lgpl-2.1", "lgpl-3.0", "mit", "mpl-2.0", "ms-pl", "ncsa",
    "odc-by", "odbl", "openrail", "openrail++", "openrail++-m",
    "other", "pddl", "postgresql", "unlicense", "wtfpl", "zlib",
}

LICENSE_ALIASES = {
    "apache 2": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "apache-2": "apache-2.0",
    "apache2": "apache-2.0",
    "bsd 2 clause": "bsd-2-clause",
    "bsd 3 clause": "bsd-3-clause",
    "cc by 4.0": "cc-by-4.0",
    "cc-by": "cc-by-4.0",
    "creative commons attribution 4.0": "cc-by-4.0",
    "mit license": "mit",
}

ALTERNATIVE_TASK_FIELDS = {
    "task_categories",
    "tasks",
    "task",
    "task_ids",
    "pipeline_tags",
    "validated_tasks",
    "finetuning_task",
    "training_task",
    "task_type",
    "pipeline",
    "finetuned_task",
}

# Only these tags are interpreted as task evidence. Explicit task fields may
# still contain custom task identifiers not present in this list.
KNOWN_TASK_TAGS = {
    "audio-classification", "audio-to-audio", "automatic-speech-recognition",
    "conversational", "depth-estimation", "document-question-answering",
    "feature-extraction", "fill-mask", "image-classification",
    "image-feature-extraction", "image-segmentation", "image-text-to-text",
    "image-to-3d", "image-to-image", "image-to-text", "image-to-video",
    "keypoint-detection", "mask-generation", "object-detection",
    "question-answering", "reinforcement-learning", "robotics",
    "sentence-similarity", "summarization", "table-question-answering",
    "tabular-classification", "tabular-regression", "text-classification",
    "text-generation", "text-ranking", "text-to-3d", "text-to-audio",
    "text-to-image", "text-to-speech", "text-to-video",
    "text2text-generation", "time-series-forecasting", "token-classification",
    "translation", "unconditional-image-generation", "video-classification",
    "video-to-video", "visual-question-answering",
    "zero-shot-classification", "zero-shot-image-classification",
    "zero-shot-object-detection",
}

TASK_ALIASES = {
    "asr": "automatic-speech-recognition",
    "image generation": "text-to-image",
    "named entity recognition": "token-classification",
    "ner": "token-classification",
    "sentiment analysis": "text-classification",
    "summarisation": "summarization",
    "text to text": "text2text-generation",
    "text-to-text": "text2text-generation",
    "text-to-video-synthesis": "text-to-video",
}

DIRECT_GITHUB_FIELDS = {
    "github", "github_url", "github_repo", "repo_url", "repository",
    "repo_link", "source_repo", "source_repos", "source_code", "code",
    "code_url", "finetune_code", "code_base", "newest_code",
    "original_code",
}

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,159}$")
GITHUB_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
GITHUB_RAW_URL = re.compile(
    r"https?://raw\.githubusercontent\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
GITHUB_SSH = re.compile(
    r"git@github\.com:(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or query model-license, model-task, and GitHub edges."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    query = parser.add_mutually_exclusive_group()
    query.add_argument("--query-license", metavar="LICENSE")
    query.add_argument("--query-task", metavar="TASK")
    query.add_argument(
        "--query-github",
        metavar="OWNER/REPOSITORY",
        help="Accepts owner/repository or a GitHub URL.",
    )
    query.add_argument(
        "--query-model",
        metavar="MODEL_ID",
        help="List this model's licenses, tasks, and GitHub repositories.",
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


def task_values(value: object) -> list[str]:
    values = string_values(value)
    if isinstance(value, dict):
        # Standard task dictionaries commonly contain {name, type}. Prefer the
        # machine-readable type and use name only when no type is available.
        raw = value.get("type") or value.get("id") or value.get("name")
        values.extend(string_values(raw))
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                raw = item.get("type") or item.get("id") or item.get("name")
                values.extend(string_values(raw))
    return values


def normalize_identifier(value: object, aliases: dict[str, str]) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower().strip("'\"`")
    if not candidate or "://" in candidate or "\n" in candidate:
        return None
    candidate = aliases.get(candidate, candidate)
    candidate = re.sub(r"[ _]+", "-", candidate)
    candidate = aliases.get(candidate, candidate)
    candidate = re.sub(r"-+", "-", candidate).strip("-")
    return candidate if IDENTIFIER.fullmatch(candidate) else None


def register(
    candidates: dict[str, tuple[str, str, str]],
    value: str,
    evidence: str,
    confidence: str,
    group: str,
) -> None:
    current = candidates.get(value)
    if current is None or PRIORITY[group] < PRIORITY[current[2]]:
        candidates[value] = (evidence, confidence, group)


def candidate_licenses(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates: dict[str, tuple[str, str, str]] = {}
    specific_names = {
        value
        for raw in string_values(metadata.get("license_name"))
        if (value := normalize_identifier(raw, LICENSE_ALIASES)) is not None
    }

    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        if field == "license":
            group, confidence = "standard", "declared"
        elif field == "license_name":
            group, confidence = "standard", "declared"
        elif field in ALTERNATIVE_LICENSE_FIELDS:
            group, confidence = "alternative", "declared_nonstandard"
        else:
            continue
        for raw in string_values(raw_value):
            license_id = normalize_identifier(raw, LICENSE_ALIASES)
            if license_id is None:
                continue
            # license_name is the specific identity when the standard field is
            # the generic value "other".
            if field == "license" and license_id == "other" and specific_names:
                continue
            register(candidates, license_id, field, confidence, group)

    for raw_tag in string_values(metadata.get("tags")):
        license_id = normalize_identifier(raw_tag, LICENSE_ALIASES)
        if license_id in KNOWN_LICENSE_TAGS:
            register(
                candidates,
                license_id,
                "tags",
                "tag_association",
                "tag",
            )
    return {key: (value[0], value[1]) for key, value in candidates.items()}


def candidate_tasks(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates: dict[str, tuple[str, str, str]] = {}
    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        if field == "pipeline_tag":
            group, confidence = "standard", "declared"
        elif field in ALTERNATIVE_TASK_FIELDS:
            group, confidence = "alternative", "declared_nonstandard"
        else:
            continue
        for raw in task_values(raw_value):
            task = normalize_identifier(raw, TASK_ALIASES)
            if task is not None:
                register(candidates, task, field, confidence, group)

    for raw_tag in string_values(metadata.get("tags")):
        task = normalize_identifier(raw_tag, TASK_ALIASES)
        if task in KNOWN_TASK_TAGS:
            register(candidates, task, "tags", "tag_association", "tag")
    return {key: (value[0], value[1]) for key, value in candidates.items()}


def nested_strings(value: object, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{normalize_field_name(key)}" if path else normalize_field_name(key)
            yield from nested_strings(item, child)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from nested_strings(item, path)


def github_repositories(value: str) -> set[str]:
    repositories = set()
    for pattern in (GITHUB_URL, GITHUB_RAW_URL, GITHUB_SSH):
        for match in pattern.finditer(value):
            owner = match.group("owner").lower()
            repo = match.group("repo").rstrip(".,;:)]}").removesuffix(".git").lower()
            if repo:
                repositories.add(f"{owner}/{repo}")
    return repositories


def normalize_github_query(value: str) -> str | None:
    matches = github_repositories(value)
    if len(matches) == 1:
        return next(iter(matches))
    candidate = value.strip().lower().removeprefix("github::").strip("/")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,38}/[a-z0-9._-]+", candidate):
        return candidate.removesuffix(".git")
    return None


def candidate_github(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates: dict[str, tuple[str, str, str]] = {}
    for raw_field, raw_value in metadata.items():
        field = normalize_field_name(raw_field)
        for path, text in nested_strings(raw_value, field):
            for repository in github_repositories(text):
                if field in DIRECT_GITHUB_FIELDS:
                    group, confidence = "standard", "declared_link"
                else:
                    group, confidence = "embedded", "embedded_reference"
                register(candidates, repository, path, confidence, group)
    return {key: (value[0], value[1]) for key, value in candidates.items()}


EXTRACTORS: dict[str, Callable[[dict], dict[str, tuple[str, str]]]] = {
    "license": candidate_licenses,
    "task": candidate_tasks,
    "github": candidate_github,
}


def relation_paths(output_dir: Path, relation: str) -> dict[str, Path]:
    stem = RELATIONS[relation]["stem"]
    return {
        "csv": output_dir / f"{stem}.csv",
        "jsonl": output_dir / f"{stem}.jsonl",
        "detailed_csv": output_dir / f"{stem}_detailed.csv",
        "detailed_jsonl": output_dir / f"{stem}_detailed.jsonl",
    }


def validate_generation_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input metadata not found: {input_path}")
    outputs = [
        path.resolve()
        for relation in RELATIONS
        for path in relation_paths(output_dir, relation).values()
    ]
    if input_path in outputs or len(set(outputs)) != len(outputs):
        raise ValueError("Input and output paths must all be different")
    existing = [path for path in outputs if path.exists()]
    if existing:
        formatted = "\n".join(str(path) for path in existing)
        raise FileExistsError(
            "Refusing to overwrite existing output files:\n" + formatted
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    return input_path, output_dir


def generate(args: argparse.Namespace) -> None:
    input_path, output_dir = validate_generation_paths(args)
    counters = {relation: Counter() for relation in RELATIONS}
    target_counts = {relation: Counter() for relation in RELATIONS}

    with ExitStack() as stack:
        handles: dict[str, dict[str, object]] = {}
        writers: dict[str, dict[str, csv.DictWriter]] = {}
        for relation in RELATIONS:
            paths = relation_paths(output_dir, relation)
            relation_handles = {
                "csv": stack.enter_context(paths["csv"].open("x", newline="", encoding="utf-8")),
                "jsonl": stack.enter_context(paths["jsonl"].open("x", encoding="utf-8")),
                "detailed_csv": stack.enter_context(
                    paths["detailed_csv"].open("x", newline="", encoding="utf-8")
                ),
                "detailed_jsonl": stack.enter_context(
                    paths["detailed_jsonl"].open("x", encoding="utf-8")
                ),
            }
            handles[relation] = relation_handles
            writers[relation] = {
                "csv": csv.DictWriter(relation_handles["csv"], fieldnames=SIMPLE_FIELDS),
                "detailed_csv": csv.DictWriter(
                    relation_handles["detailed_csv"], fieldnames=DETAILED_FIELDS
                ),
            }
            writers[relation]["csv"].writeheader()
            writers[relation]["detailed_csv"].writeheader()

        with input_path.open(encoding="utf-8") as input_file:
            for line_number, line in enumerate(input_file, start=1):
                if not line.strip():
                    continue
                for relation in RELATIONS:
                    counters[relation]["records"] += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    for relation in RELATIONS:
                        counters[relation]["invalid_json"] += 1
                    print(f"Skipping invalid JSON at line {line_number}: {exc}")
                    continue

                model_id = record.get("modelId")
                metadata = record.get("readme_metadata")
                if not isinstance(model_id, str) or not model_id.strip():
                    for relation in RELATIONS:
                        counters[relation]["invalid_model_id"] += 1
                    continue
                if not isinstance(metadata, dict) or not metadata:
                    continue

                model_id = model_id.strip()
                for relation, extractor in EXTRACTORS.items():
                    counters[relation]["metadata_records"] += 1
                    candidates = extractor(metadata)
                    if not candidates:
                        continue
                    counters[relation]["models_with_edges"] += 1
                    if len(candidates) > 1:
                        counters[relation]["models_with_multiple"] += 1

                    config = RELATIONS[relation]
                    for target, (evidence, confidence) in sorted(candidates.items()):
                        simple_row = {
                            "source": f"model::{model_id}",
                            "edge_type": config["edge_type"],
                            "target": f"{config['target_prefix']}{target}",
                        }
                        detailed_row = {
                            **simple_row,
                            "evidence": evidence,
                            "confidence": confidence,
                        }
                        writers[relation]["csv"].writerow(simple_row)
                        handles[relation]["jsonl"].write(
                            json.dumps(simple_row, ensure_ascii=False, separators=(",", ":"))
                            + "\n"
                        )
                        writers[relation]["detailed_csv"].writerow(detailed_row)
                        handles[relation]["detailed_jsonl"].write(
                            json.dumps(detailed_row, ensure_ascii=False, separators=(",", ":"))
                            + "\n"
                        )
                        counters[relation]["edges"] += 1
                        counters[relation][f"confidence:{confidence}"] += 1
                        target_counts[relation][target] += 1

    for relation in RELATIONS:
        title = relation.capitalize()
        stats = counters[relation]
        print(f"\n## Model-{title} subgraph")
        print(f"Records scanned:             {stats['records']:12,}")
        print(f"Records with metadata:       {stats['metadata_records']:12,}")
        print(f"Models with edges:           {stats['models_with_edges']:12,}")
        print(f"Models with multiple values: {stats['models_with_multiple']:12,}")
        print(f"Unique {relation} nodes:      {len(target_counts[relation]):12,}")
        print(f"Total unique edges:          {stats['edges']:12,}")
        for key in sorted(stats):
            if key.startswith("confidence:"):
                print(f"  {key.removeprefix('confidence:'):25} {stats[key]:12,}")
        print(f"Invalid model IDs:           {stats['invalid_model_id']:12,}")
        print(f"Invalid JSON lines:          {stats['invalid_json']:12,}")
        print(f"Most common {relation} values:")
        for value, count in target_counts[relation].most_common(15):
            print(f"  {value:35} {count:12,}")
        paths = relation_paths(output_dir, relation)
        print(f"CSV:            {paths['csv']}")
        print(f"JSONL:          {paths['jsonl']}")
        print(f"Detailed CSV:   {paths['detailed_csv']}")
        print(f"Detailed JSONL: {paths['detailed_jsonl']}")


def read_query_results(
    csv_path: Path,
    source: str | None = None,
    target: str | None = None,
) -> list[str]:
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Edge CSV not found: {csv_path}\nRun the script without a query first."
        )
    results = []
    with csv_path.open(newline="", encoding="utf-8") as input_file:
        for row in csv.DictReader(input_file):
            if source is not None and row.get("source") == source:
                results.append(row["target"].split("::", 1)[-1])
            elif target is not None and row.get("target") == target:
                results.append(row["source"].removeprefix("model::"))
    return sorted(set(results), key=str.lower)


def print_results(heading: str, results: list[str], limit: int) -> None:
    shown = results if limit == 0 else results[:limit]
    print(f"Distinct results: {len(results):,}")
    print(f"{heading} shown: {len(shown):,}/{len(results):,}")
    for result in shown:
        print(result)


def query(args: argparse.Namespace) -> None:
    if args.limit < 0:
        raise ValueError("--limit cannot be negative")
    output_dir = args.output_dir.expanduser().resolve()

    if args.query_model is not None:
        model_id = args.query_model.removeprefix("model::").strip()
        if not model_id:
            raise ValueError("Model query cannot be empty")
        print(f"Model: {model_id}")
        for relation in RELATIONS:
            results = read_query_results(
                relation_paths(output_dir, relation)["csv"],
                source=f"model::{model_id}",
            )
            print(f"\n{relation.capitalize()} values")
            print_results(relation.capitalize(), results, args.limit)
        return

    if args.query_license is not None:
        relation, raw = "license", args.query_license
        value = normalize_identifier(raw, LICENSE_ALIASES)
    elif args.query_task is not None:
        relation, raw = "task", args.query_task
        value = normalize_identifier(raw, TASK_ALIASES)
    else:
        relation, raw = "github", args.query_github
        value = normalize_github_query(raw)
    if value is None:
        raise ValueError(f"Invalid {relation} query: {raw!r}")

    config = RELATIONS[relation]
    results = read_query_results(
        relation_paths(output_dir, relation)["csv"],
        target=f"{config['target_prefix']}{value}",
    )
    print(f"{relation.capitalize()}: {value}")
    print_results("Models", results, args.limit)


def main() -> int:
    args = parse_args()
    try:
        if any(
            value is not None
            for value in (
                args.query_license,
                args.query_task,
                args.query_github,
                args.query_model,
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
