#!/usr/bin/env python3
"""Build HuggingGraph v2 from the intact v1 graph and attribute subgraphs.

Created: 2026-09-14
Version: v2026.09.14-10
Purpose: Merge all eleven logical subgraphs into graph-ready CSV, JSONL, and
         v1-compatible DOT artifacts without modifying any source artifact.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_DOT = SCRIPT_DIR.parents[1] / "HuggingGraph" / "HuggingGraph_v1.dot"
DEFAULT_ATTRIBUTE_DIR = SCRIPT_DIR
DEFAULT_OUTPUT_DIR = SCRIPT_DIR.parents[1] / "HuggingGraph"

ATTRIBUTE_FILES = {
    "library": "model_dataset_library_edges.csv",
    "license": "model_dataset_license_edges.csv",
    "task": "model_dataset_task_edges.csv",
    "github": "model_dataset_github_edges.csv",
}

FIELDS = ["source", "edge_type", "target"]
DOT_QUOTED = r'"((?:\\.|[^"\\])*)"'
DOT_EDGE = re.compile(
    rf"^\s*{DOT_QUOTED}\s*->\s*{DOT_QUOTED}\s*"
    rf"\[label={DOT_QUOTED},\s*edge_type={DOT_QUOTED}\];\s*$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build HuggingGraph v2 CSV, JSONL, and DOT artifacts."
    )
    parser.add_argument("--base-dot", type=Path, default=DEFAULT_BASE_DOT)
    parser.add_argument("--attribute-dir", type=Path, default=DEFAULT_ATTRIBUTE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def decode_dot_string(value: str) -> str:
    """Decode a quoted DOT token emitted with JSON-compatible escaping."""
    return json.loads(f'"{value}"')


def dot_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def output_paths(output_dir: Path) -> tuple[Path, Path, Path]:
    return (
        output_dir / "HuggingGraph_v2.csv",
        output_dir / "HuggingGraph_v2.jsonl",
        output_dir / "HuggingGraph_v2.dot",
    )


def validate_paths(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Path], Path, Path, Path]:
    base_dot = args.base_dot.expanduser().resolve()
    attribute_dir = args.attribute_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not base_dot.is_file():
        raise FileNotFoundError(f"Base v1 DOT not found: {base_dot}")
    attributes = {
        name: (attribute_dir / filename).resolve()
        for name, filename in ATTRIBUTE_FILES.items()
    }
    missing = [path for path in attributes.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing attribute inputs:\n" + "\n".join(str(path) for path in missing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path, jsonl_path, dot_path = output_paths(output_dir)
    existing = [path for path in (csv_path, jsonl_path, dot_path) if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing v2 artifacts:\n"
            + "\n".join(str(path) for path in existing)
        )
    inputs = {base_dot, *attributes.values()}
    if any(path in inputs for path in (csv_path, jsonl_path, dot_path)):
        raise ValueError("Input and output paths must differ")
    return base_dot, attributes, csv_path, jsonl_path, dot_path


def v1_edges(path: Path):
    """Yield source, canonical edge type, target, and legacy display label."""
    header_seen = False
    footer_seen = False
    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if line_number == 1 and stripped == "digraph HuggingGraph {":
                header_seen = True
                continue
            if stripped in {'graph [rankdir="LR"];', 'node [shape="box"];'}:
                continue
            if stripped == "}":
                footer_seen = True
                continue
            match = DOT_EDGE.fullmatch(line)
            if match is None:
                raise ValueError(f"Unsupported v1 DOT syntax at line {line_number}")
            source, target, label, edge_type = (
                decode_dot_string(value) for value in match.groups()
            )
            if not source.startswith(("model::", "dataset::")):
                raise ValueError(f"Untyped v1 source at line {line_number}: {source}")
            if not target.startswith(("model::", "dataset::")):
                raise ValueError(f"Untyped v1 target at line {line_number}: {target}")
            yield source, edge_type, target, label
    if not header_seen or not footer_seen:
        raise ValueError("Base DOT is missing its graph header or closing brace")


def logical_subgraph(source: str, target: str, family: str | None = None) -> str:
    if family is not None:
        return f"{source.split('::', 1)[0]}-{family}"
    source_type = source.split("::", 1)[0]
    target_type = target.split("::", 1)[0]
    return f"{source_type}-{target_type}"


def build(
    base_dot: Path,
    attributes: dict[str, Path],
    csv_path: Path,
    jsonl_path: Path,
    dot_path: Path,
) -> Counter[str]:
    counters: Counter[str] = Counter()
    with (
        csv_path.open("x", newline="", encoding="utf-8") as csv_file,
        jsonl_path.open("x", encoding="utf-8") as jsonl_file,
        dot_path.open("x", encoding="utf-8") as dot_file,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()
        dot_file.write(
            'digraph HuggingGraph {\n'
            '  graph [rankdir="LR"];\n'
            '  node [shape="box"];\n'
        )

        def emit(source: str, edge_type: str, target: str, label: str) -> None:
            row = {"source": source, "edge_type": edge_type, "target": target}
            writer.writerow(row)
            jsonl_file.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            dot_file.write(
                f"  {dot_string(source)} -> {dot_string(target)} "
                f"[label={dot_string(label)}, edge_type={dot_string(edge_type)}];\n"
            )
            counters["edges"] += 1

        for source, edge_type, target, label in v1_edges(base_dot):
            emit(source, edge_type, target, label)
            counters[f"subgraph:{logical_subgraph(source, target)}"] += 1
            counters["base_v1_edges"] += 1

        for family, path in attributes.items():
            with path.open(newline="", encoding="utf-8") as input_file:
                reader = csv.DictReader(input_file)
                if reader.fieldnames != FIELDS:
                    raise ValueError(f"Unexpected schema in {path}: {reader.fieldnames}")
                for line_number, row in enumerate(reader, start=2):
                    source = row["source"]
                    edge_type = row["edge_type"]
                    target = row["target"]
                    if not source.startswith(("model::", "dataset::")):
                        raise ValueError(f"Untyped source in {path} line {line_number}")
                    expected_target = f"{family}::"
                    if not target.startswith(expected_target):
                        raise ValueError(
                            f"Unexpected target type in {path} line {line_number}"
                        )
                    emit(source, edge_type, target, edge_type)
                    counters[f"subgraph:{logical_subgraph(source, target, family)}"] += 1
                    counters[f"family:{family}"] += 1

        dot_file.write("}\n")
    return counters


def report(
    counters: Counter[str], csv_path: Path, jsonl_path: Path, dot_path: Path
) -> None:
    print("\nHuggingGraph v2")
    print("---------------")
    for name in (
        "model-model", "dataset-model", "dataset-dataset",
        "model-library", "dataset-library", "model-license",
        "dataset-license", "model-task", "dataset-task", "model-github",
        "dataset-github",
    ):
        print(f"{name:24} {counters[f'subgraph:{name}']:12,}")
    print(f"{'Base v1 edges':24} {counters['base_v1_edges']:12,}")
    print(f"{'Total v2 edges':24} {counters['edges']:12,}")
    print(f"\nCSV:   {csv_path}")
    print(f"JSONL: {jsonl_path}")
    print(f"DOT:   {dot_path}")


def main() -> int:
    try:
        base_dot, attributes, csv_path, jsonl_path, dot_path = validate_paths(
            parse_args()
        )
        counters = build(base_dot, attributes, csv_path, jsonl_path, dot_path)
        report(counters, csv_path, jsonl_path, dot_path)
    except KeyboardInterrupt:
        print("Interrupted; move incomplete v2 outputs before rerunning.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
