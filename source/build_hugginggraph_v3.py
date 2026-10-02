#!/usr/bin/env python3
"""Build the count-verified HuggingGraph v3 DOT release.

Created: 2026-09-30
Version: v2026.10.02-03
Purpose: Assemble the eleven v2 relationship families and six Space families.

Change history:
- 2026-10-02 v2026.10.02-03: updated v3 validation constants for the
  October 2 Dataset relationship analysis.
  Backup: build_hugginggraph_v3.py.bak.20261002-195338
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


FIELDS = ["source", "edge_type", "target"]
SUBGRAPHS = (
    ("model-model", "01_model_model_edges.csv", "model", "model", 966_035),
    ("model-dataset", "02_model_dataset_edges.csv", "dataset", "model", 362_064),
    ("dataset-dataset", "03_dataset_dataset_edges.csv", "dataset", "dataset", 12_541),
    ("model-library", "04_model_library_edges.csv", "model", "library", 1_311_386),
    ("dataset-library", "05_dataset_library_edges.csv", "dataset", "library", 16_864),
    ("model-license", "06_model_license_edges.csv", "model", "license", 1_090_845),
    ("dataset-license", "07_dataset_license_edges.csv", "dataset", "license", 331_317),
    ("model-task", "08_model_task_edges.csv", "model", "task", 577_077),
    ("dataset-task", "09_dataset_task_edges.csv", "dataset", "task", 317_412),
    ("model-github", "10_model_github_edges.csv", "model", "github", 962_855),
    ("dataset-github", "11_dataset_github_edges.csv", "dataset", "github", 188_888),
    ("model-space", "12_model_space_edges.csv", "model", "space", 941_121),
    ("dataset-space", "13_dataset_space_edges.csv", "dataset", "space", 83_061),
    ("space-github", "14_space_github_edges.csv", "space", "github", 113_755),
    ("space-license", "15_space_license_edges.csv", "space", "license", 420_369),
    ("space-task", "16_space_task_edges.csv", "space", "task", 43_223),
    ("space-agent", "17_space_agent_edges.csv", "space", "agent", 37_059),
)
EXPECTED_NODES = {
    "model": 1_888_591,
    "dataset": 431_324,
    "space": 712_728,
    "library": 2_148,
    "license": 4_888,
    "task": 839,
    "github": 96_678,
    "agent": 11,
}
EXPECTED_EDGES = 7_775_872
EXPECTED_TOTAL_NODES = 3_137_207


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("HuggingGraph_v3.dot"))
    return parser.parse_args()


def node_type(node: str) -> str:
    if node.startswith("https://github.com/"):
        return "github"
    if "::" not in node:
        raise ValueError(f"Untyped node: {node!r}")
    return node.split("::", 1)[0]


def validate_node(node: str, expected_type: str, context: str) -> None:
    actual = node_type(node)
    if actual != expected_type:
        raise ValueError(
            f"Unexpected {actual} node in {context}; expected {expected_type}: {node!r}"
        )


def dot_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build(edge_dir: Path, output: Path) -> tuple[Counter[str], dict[str, set[str]]]:
    edge_dir = edge_dir.expanduser().resolve()
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")

    counters: Counter[str] = Counter()
    nodes = {kind: set() for kind in EXPECTED_NODES}
    with output.open("x", encoding="utf-8") as dot_file:
        dot_file.write(
            'digraph HuggingGraph {\n'
            '  graph [rankdir="LR"];\n'
            '  node [shape="box"];\n'
        )
        for name, filename, source_type, target_type, expected_edges in SUBGRAPHS:
            path = edge_dir / filename
            if not path.is_file():
                raise FileNotFoundError(path)
            with path.open(newline="", encoding="utf-8") as input_file:
                reader = csv.DictReader(input_file)
                if reader.fieldnames != FIELDS:
                    raise ValueError(f"Unexpected schema in {path}: {reader.fieldnames}")
                for line_number, row in enumerate(reader, start=2):
                    source = row["source"]
                    target = row["target"]
                    edge_type = row["edge_type"]
                    context = f"{filename}:{line_number}"
                    validate_node(source, source_type, context)
                    validate_node(target, target_type, context)
                    label = "merge" if edge_type == "merged" else edge_type
                    dot_file.write(
                        f"  {dot_string(source)} -> {dot_string(target)} "
                        f"[label={dot_string(label)}, edge_type={dot_string(edge_type)}];\n"
                    )
                    nodes[source_type].add(source)
                    nodes[target_type].add(target)
                    counters[name] += 1
                    counters["edges"] += 1
            if counters[name] != expected_edges:
                raise ValueError(
                    f"Unexpected {name} count: {counters[name]:,} != {expected_edges:,}"
                )
        dot_file.write("}\n")

    actual_nodes = {kind: len(values) for kind, values in nodes.items()}
    if counters["edges"] != EXPECTED_EDGES:
        raise ValueError(f"Unexpected edge total: {counters['edges']:,}")
    if actual_nodes != EXPECTED_NODES:
        raise ValueError(f"Unexpected node counts: {actual_nodes} != {EXPECTED_NODES}")
    if sum(actual_nodes.values()) != EXPECTED_TOTAL_NODES:
        raise ValueError("Unexpected global node total")
    return counters, nodes


def main() -> int:
    args = parse_args()
    counters, nodes = build(args.edge_dir, args.output)
    print("HuggingGraph v3")
    print("---------------")
    for name, _filename, _source, _target, _expected in SUBGRAPHS:
        print(f"{name:24} {counters[name]:12,}")
    print(f"{'Total edges':24} {counters['edges']:12,}")
    for kind in EXPECTED_NODES:
        print(f"{kind + ' nodes':24} {len(nodes[kind]):12,}")
    print(f"{'Total nodes':24} {sum(map(len, nodes.values())):12,}")
    print(f"DOT: {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
