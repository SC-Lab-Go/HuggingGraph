#!/usr/bin/env python3
"""Build Space-license, Space-task, and Space-agent-framework subgraphs.

Created: 2026-09-24
Version: v2026.09.24-01
Purpose: Convert explicit Space-card attributes into graph-ready relationship
         files while reusing HuggingGraph license/task normalization.

The builder creates new artifacts only. Generic agent category tags map to
``task::agents``; only recognized framework tags create ``agent::`` nodes.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from model_license_task_github_edges import candidate_licenses, candidate_tasks


CHANGE_ID = "v2026.09.24-01"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "all_space_card_metadata_2026Sep24.jsonl"
SIMPLE_FIELDS = ["source", "edge_type", "target"]
DETAILED_FIELDS = SIMPLE_FIELDS + ["evidence", "confidence"]

AGENT_CATEGORY_TAGS = {
    "agent", "agents", "ai-agent", "ai-agents", "agentic", "agentic-ai",
    "multi-agent", "smolagent", "smolagents", "langchain", "langgraph",
    "crewai", "llamaindex", "llama-index", "pydantic-ai", "autogen",
    "haystack", "google-adk", "agno", "openai-agents", "openai-agents-sdk",
}

AGENT_FRAMEWORK_ALIASES = {
    "smolagent": "smolagents",
    "smolagents": "smolagents",
    "langchain": "langchain",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "llamaindex": "llamaindex",
    "llama-index": "llamaindex",
    "pydantic-ai": "pydantic-ai",
    "autogen": "autogen",
    "haystack": "haystack",
    "google-adk": "google-adk",
    "agno": "agno",
    "openai-agents": "openai-agents",
    "openai-agents-sdk": "openai-agents",
}

FAMILIES = {
    "license": ("has_license", "license::"),
    "task": ("supports_task", "task::"),
    "agent": ("uses_agent_framework", "agent::"),
}

CONFIDENCE_PRIORITY = {
    "declared": 0,
    "declared_nonstandard": 1,
    "tag_association": 2,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Space license, task, and agent-framework subgraphs."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR)
    return parser.parse_args()


def string_tags(metadata: dict) -> set[str]:
    value = metadata.get("tags")
    values = value if isinstance(value, (list, tuple)) else [value]
    return {
        item.strip().lower()
        for item in values
        if isinstance(item, str) and item.strip()
    }


def agent_candidates(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates = {}
    for tag in string_tags(metadata):
        framework = AGENT_FRAMEWORK_ALIASES.get(tag)
        if framework is not None:
            candidates[framework] = ("tags", "tag_association")
    return candidates


def task_candidates(metadata: dict) -> dict[str, tuple[str, str]]:
    candidates = candidate_tasks(metadata)
    tags = string_tags(metadata)
    if tags & AGENT_CATEGORY_TAGS:
        existing = candidates.get("agents")
        proposed = ("tags", "tag_association")
        if existing is None or CONFIDENCE_PRIORITY.get(proposed[1], 99) < CONFIDENCE_PRIORITY.get(existing[1], 99):
            candidates["agents"] = proposed
    return candidates


def output_paths(output_dir: Path, family: str) -> dict[str, Path]:
    stem = f"space_{family}_edges"
    return {
        "csv": output_dir / f"{stem}.csv",
        "jsonl": output_dir / f"{stem}.jsonl",
        "detailed_csv": output_dir / f"{stem}_detailed.csv",
        "detailed_jsonl": output_dir / f"{stem}_detailed.jsonl",
    }


def emit_family(
    output_dir: Path,
    family: str,
    edges: dict[tuple[str, str, str], tuple[str, str]],
) -> None:
    paths = output_paths(output_dir, family)
    existing = [path for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite:\n" + "\n".join(map(str, existing)))
    with (
        paths["csv"].open("x", newline="", encoding="utf-8") as csv_file,
        paths["jsonl"].open("x", encoding="utf-8") as jsonl_file,
        paths["detailed_csv"].open("x", newline="", encoding="utf-8") as detailed_csv,
        paths["detailed_jsonl"].open("x", encoding="utf-8") as detailed_jsonl,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=SIMPLE_FIELDS)
        detailed_writer = csv.DictWriter(detailed_csv, fieldnames=DETAILED_FIELDS)
        writer.writeheader()
        detailed_writer.writeheader()
        for key in sorted(edges):
            source, edge_type, target = key
            evidence, confidence = edges[key]
            simple = {"source": source, "edge_type": edge_type, "target": target}
            detailed = {**simple, "evidence": evidence, "confidence": confidence}
            writer.writerow(simple)
            detailed_writer.writerow(detailed)
            jsonl_file.write(json.dumps(simple, ensure_ascii=False, separators=(",", ":")) + "\n")
            detailed_jsonl.write(json.dumps(detailed, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    for family in FAMILIES:
        existing = [p for p in output_paths(output_dir, family).values() if p.exists()]
        if existing:
            raise FileExistsError("Refusing to overwrite:\n" + "\n".join(map(str, existing)))

    edges: dict[str, dict[tuple[str, str, str], tuple[str, str]]] = {
        family: {} for family in FAMILIES
    }
    records = metadata_records = invalid = 0
    with input_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON line {line_number}: {exc}") from exc
            records += 1
            space_id = record.get("spaceId")
            metadata = record.get("card_metadata")
            if not isinstance(space_id, str) or not space_id.strip():
                invalid += 1
                continue
            if not isinstance(metadata, dict) or not metadata:
                continue
            metadata_records += 1
            source = f"space::{space_id.strip()}"
            candidates_by_family = {
                "license": candidate_licenses(metadata),
                "task": task_candidates(metadata),
                "agent": agent_candidates(metadata),
            }
            for family, candidates in candidates_by_family.items():
                edge_type, target_prefix = FAMILIES[family]
                for value, detail in candidates.items():
                    key = (source, edge_type, f"{target_prefix}{value}")
                    previous = edges[family].get(key)
                    if previous is None or CONFIDENCE_PRIORITY.get(detail[1], 99) < CONFIDENCE_PRIORITY.get(previous[1], 99):
                        edges[family][key] = detail

    for family in FAMILIES:
        emit_family(output_dir, family, edges[family])

    print(f"Space records:          {records:12,}")
    print(f"Cards with metadata:    {metadata_records:12,}")
    for family in FAMILIES:
        sources = {edge[0] for edge in edges[family]}
        targets = {edge[2] for edge in edges[family]}
        print(f"Space-{family} edges:   {len(edges[family]):12,}")
        print(f"  source Spaces:        {len(sources):12,}")
        print(f"  target nodes:         {len(targets):12,}")
    print(f"Invalid Space records:  {invalid:12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
