#!/usr/bin/env python3
"""Build HuggingGraph v2 from the intact v1 graph and attribute subgraphs.

Created: 2026-09-14
Version: v2026.09.29-07
Purpose: Merge the intact v1 graph and eight current attribute subgraphs into
         the release HuggingGraph v2 DOT artifact.

Change history:
2026-09-29 v2026.09.29-07
- Read the eleven-subgraph snapshot and validate every relationship count.
- Support canonical HTTPS GitHub repository targets.
- Build the DOT-only v2 release without changing v0 or v1.
- Backup: build_hugginggraph_v2.py.bak.20260929-175020
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_DOT = SCRIPT_DIR / "HuggingGraph_v1.dot"
DEFAULT_EDGE_DIR = SCRIPT_DIR / "edges"
DEFAULT_OUTPUT_DOT = SCRIPT_DIR / "HuggingGraph_v2.dot"

# logical subgraph: (file, expected target prefix, expected edge count)
ATTRIBUTE_FILES = {
    "model-library": ("04_model_library_edges.csv", "library::", 1_311_386),
    "dataset-library": ("05_dataset_library_edges.csv", "library::", 17_168),
    "model-license": ("06_model_license_edges.csv", "license::", 1_090_845),
    "dataset-license": ("07_dataset_license_edges.csv", "license::", 341_884),
    "model-task": ("08_model_task_edges.csv", "task::", 577_077),
    "dataset-task": ("09_dataset_task_edges.csv", "task::", 327_713),
    "model-github": ("10_model_github_edges.csv", "https://github.com/", 962_855),
    "dataset-github": ("11_dataset_github_edges.csv", "https://github.com/", 188_888),
}

FIELDS = ["source", "edge_type", "target"]
DOT_QUOTED = r'"((?:\\.|[^"\\])*)"'
DOT_EDGE = re.compile(
    rf"^\s*{DOT_QUOTED}\s*->\s*{DOT_QUOTED}\s*"
    rf"\[label={DOT_QUOTED},\s*edge_type={DOT_QUOTED}\];\s*$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the count-verified HuggingGraph v2 DOT artifact."
    )
    parser.add_argument("--base-dot", type=Path, default=DEFAULT_BASE_DOT)
    parser.add_argument("--edge-dir", type=Path, default=DEFAULT_EDGE_DIR)
    parser.add_argument("--output-dot", type=Path, default=DEFAULT_OUTPUT_DOT)
    return parser.parse_args()


def decode_dot_string(value: str) -> str:
    """Decode a quoted DOT token emitted with JSON-compatible escaping."""
    return json.loads(f'"{value}"')


def dot_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def validate_paths(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, tuple[Path, str, int]], Path]:
    base_dot = args.base_dot.expanduser().resolve()
    edge_dir = args.edge_dir.expanduser().resolve()
    output_dot = args.output_dot.expanduser().resolve()
    if not base_dot.is_file():
        raise FileNotFoundError(f"Base v1 DOT not found: {base_dot}")
    attributes = {
        name: ((edge_dir / filename).resolve(), prefix, expected)
        for name, (filename, prefix, expected) in ATTRIBUTE_FILES.items()
    }
    missing = [path for path, _prefix, _expected in attributes.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing attribute inputs:\n" + "\n".join(str(path) for path in missing)
        )
    output_dot.parent.mkdir(parents=True, exist_ok=True)
    if output_dot.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_dot}")
    inputs = {base_dot, *(item[0] for item in attributes.values())}
    if output_dot in inputs:
        raise ValueError("Input and output paths must differ")
    return base_dot, attributes, output_dot


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
    attributes: dict[str, tuple[Path, str, int]],
    dot_path: Path,
) -> Counter[str]:
    counters: Counter[str] = Counter()
    with dot_path.open("x", encoding="utf-8") as dot_file:
        dot_file.write(
            'digraph HuggingGraph {\n'
            '  graph [rankdir="LR"];\n'
            '  node [shape="box"];\n'
        )

        def emit(source: str, edge_type: str, target: str, label: str) -> None:
            dot_file.write(
                f"  {dot_string(source)} -> {dot_string(target)} "
                f"[label={dot_string(label)}, edge_type={dot_string(edge_type)}];\n"
            )
            counters["edges"] += 1

        for source, edge_type, target, label in v1_edges(base_dot):
            emit(source, edge_type, target, label)
            counters[f"subgraph:{logical_subgraph(source, target)}"] += 1
            counters["base_v1_edges"] += 1

        for subgraph, (path, target_prefix, expected_count) in attributes.items():
            source_prefix = subgraph.split("-", 1)[0] + "::"
            before = counters["edges"]
            with path.open(newline="", encoding="utf-8") as input_file:
                reader = csv.DictReader(input_file)
                if reader.fieldnames != FIELDS:
                    raise ValueError(f"Unexpected schema in {path}: {reader.fieldnames}")
                for line_number, row in enumerate(reader, start=2):
                    source = row["source"]
                    edge_type = row["edge_type"]
                    target = row["target"]
                    if not source.startswith(source_prefix):
                        raise ValueError(f"Untyped source in {path} line {line_number}")
                    if not target.startswith(target_prefix):
                        raise ValueError(
                            f"Unexpected target type in {path} line {line_number}"
                        )
                    emit(source, edge_type, target, edge_type)
                    counters[f"subgraph:{subgraph}"] += 1
            actual_count = counters["edges"] - before
            if actual_count != expected_count:
                raise ValueError(
                    f"Unexpected {subgraph} count: {actual_count:,} != {expected_count:,}"
                )

        dot_file.write("}\n")
    return counters


def report(
    counters: Counter[str], dot_path: Path
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
    print(f"DOT:   {dot_path}")


def main() -> int:
    try:
        base_dot, attributes, dot_path = validate_paths(parse_args())
        counters = build(base_dot, attributes, dot_path)
        report(counters, dot_path)
    except KeyboardInterrupt:
        print("Interrupted; move incomplete v2 outputs before rerunning.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
