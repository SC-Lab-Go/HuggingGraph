#!/usr/bin/env python3
"""Audit and package five Dataset-centered HuggingGraph subgraphs.

Created: 2026-10-02
Version: v2026.10.02-02
Purpose: Reproduce declared Dataset attributes, canonicalize Dataset-GitHub
         links, preserve Dataset-Dataset candidate tiers, and create validation
         evidence without modifying existing graph files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path


CHANGE_ID = "v2026.10.02-02"
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

from model_dataset_attribute_edges import DATASET_EXTRACTORS, RELATIONS  # noqa: E402


RELATION_CONFIG = {
    "library": {
        "baseline": ROOT / "hugginggraph_v3_2026Sep30/edges/05_dataset_library_edges.csv",
        "edge_name": "dataset_library_declared_edges.csv",
        "evidence_name": "dataset_library_declared_evidence.csv",
    },
    "license": {
        "baseline": ROOT / "hugginggraph_v3_2026Sep30/edges/07_dataset_license_edges.csv",
        "edge_name": "dataset_license_declared_edges.csv",
        "evidence_name": "dataset_license_declared_evidence.csv",
    },
    "task": {
        "baseline": ROOT / "hugginggraph_v3_2026Sep30/edges/09_dataset_task_edges.csv",
        "edge_name": "dataset_task_declared_edges.csv",
        "evidence_name": "dataset_task_declared_evidence.csv",
    },
}

SIMPLE_FIELDS = ["source", "edge_type", "target"]
EVIDENCE_FIELDS = SIMPLE_FIELDS + ["evidence", "confidence", "snapshot_status"]
GITHUB_EVIDENCE_FIELDS = SIMPLE_FIELDS + [
    "original_target", "evidence", "confidence", "population",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata",
        type=Path,
        default=ROOT / "all_dataset_readme_metadata_2026Aug28.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "results")
    return parser.parse_args()


def atomic_csv(path: Path, fields, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_simple(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != SIMPLE_FIELDS:
            raise ValueError(f"Unexpected schema in {path}: {reader.fieldnames}")
        return {(row["source"], row["edge_type"], row["target"]) for row in reader}


def graph_stats(edges):
    sources = {edge[0] for edge in edges}
    targets = {edge[2] for edge in edges}
    return {
        "edges": len(edges),
        "source_nodes": len(sources),
        "target_nodes": len(targets),
        "nodes": len(sources | targets),
    }


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_github(value: str):
    prefix = "https://github.com/"
    if not value.lower().startswith(prefix):
        raise ValueError(f"Non-canonical GitHub target: {value}")
    parts = value[len(prefix):].strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Invalid GitHub repository target: {value}")
    return prefix + parts[0].lower() + "/" + parts[1].lower()


def validation_sample(rows, fields, group_fields, seed=20261002, per_group=100):
    groups = defaultdict(list)
    for row in rows:
        group = "|".join(row[field] for field in group_fields)
        groups[group].append(row)
    rng = random.Random(seed)
    output = []
    for group in sorted(groups):
        candidates = groups[group]
        chosen = candidates if len(candidates) <= per_group else rng.sample(candidates, per_group)
        for row in sorted(chosen, key=lambda item: tuple(item[field] for field in fields)):
            output.append({
                **row,
                "sample_group": group,
                "manual_label": "",
                "review_notes": "",
            })
    return output


def copy_new(source: Path, target: Path):
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite: {target}")
    shutil.copyfile(source, target)


def main():
    args = parse_args()
    metadata_path = args.metadata.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not metadata_path.is_file():
        raise FileNotFoundError(metadata_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_dir}")

    edge_sets = {relation: set() for relation in RELATION_CONFIG}
    evidence_rows = {relation: [] for relation in RELATION_CONFIG}
    status_counts = Counter()
    records = 0
    with_metadata = 0

    with metadata_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            records += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {metadata_path}:{line_number}") from exc
            status = str(record.get("status") or "unknown")
            status_counts[status] += 1
            dataset_id = record.get("datasetId")
            metadata = record.get("readme_metadata")
            if not isinstance(dataset_id, str) or not dataset_id.strip():
                continue
            if not isinstance(metadata, dict) or not metadata:
                continue
            with_metadata += 1
            source = "dataset::" + dataset_id.strip()
            for relation in RELATION_CONFIG:
                config = RELATIONS[relation]
                for target, (evidence, confidence) in DATASET_EXTRACTORS[relation](metadata).items():
                    edge = (
                        source,
                        config["dataset_edge_type"],
                        config["target_prefix"] + target,
                    )
                    edge_sets[relation].add(edge)
                    evidence_rows[relation].append(dict(zip(
                        EVIDENCE_FIELDS,
                        (*edge, evidence, confidence, status),
                    )))

    summary = {
        "change_id": CHANGE_ID,
        "snapshot": {
            "metadata_path": str(metadata_path),
            "metadata_sha256": file_sha256(metadata_path),
            "records": records,
            "records_with_metadata": with_metadata,
            "status_counts": dict(status_counts),
        },
        "subgraphs": {},
        "verification": {},
        "outputs": {},
    }

    for relation, config in RELATION_CONFIG.items():
        baseline = read_simple(config["baseline"])
        generated = edge_sets[relation]
        if generated != baseline:
            raise RuntimeError(
                f"{relation} mismatch: missing={len(generated - baseline)}, "
                f"extra={len(baseline - generated)}"
            )
        edge_path = output_dir / config["edge_name"]
        evidence_path = output_dir / config["evidence_name"]
        sample_path = output_dir / f"dataset_{relation}_validation_sample.csv"
        atomic_csv(edge_path, SIMPLE_FIELDS, [
            dict(zip(SIMPLE_FIELDS, edge)) for edge in sorted(generated)
        ])
        ordered_evidence = sorted(
            evidence_rows[relation],
            key=lambda row: tuple(row[field] for field in EVIDENCE_FIELDS),
        )
        atomic_csv(evidence_path, EVIDENCE_FIELDS, ordered_evidence)
        sample = validation_sample(
            ordered_evidence, EVIDENCE_FIELDS, ("confidence",), per_group=100
        )
        atomic_csv(
            sample_path,
            EVIDENCE_FIELDS + ["sample_group", "manual_label", "review_notes"],
            sample,
        )
        confidence = Counter(row["confidence"] for row in ordered_evidence)
        evidence = Counter(row["evidence"] for row in ordered_evidence)
        summary["subgraphs"][relation] = {
            **graph_stats(generated),
            "confidence": dict(confidence),
            "top_evidence": evidence.most_common(25),
            "validation_sample_rows": len(sample),
        }
        summary["verification"][relation] = {
            "reproduced_exactly": True,
            "baseline_edges": len(baseline),
            "missing": 0,
            "extra": 0,
            "duplicates": 0,
        }
        summary["outputs"][relation] = {
            "edges": edge_path.name,
            "evidence": evidence_path.name,
            "validation_sample": sample_path.name,
        }

    github_simple = ROOT / "dataset_github_edges_full.csv"
    github_detailed = ROOT / "dataset_github_edges_full_detailed.csv"
    baseline_github = read_simple(github_simple)
    canonical_edges = set()
    github_evidence = []
    with github_detailed.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        expected = SIMPLE_FIELDS + ["evidence", "confidence", "population"]
        if reader.fieldnames != expected:
            raise ValueError(f"Unexpected schema in {github_detailed}: {reader.fieldnames}")
        for row in reader:
            canonical = canonical_github(row["target"])
            edge = (row["source"], row["edge_type"], canonical)
            canonical_edges.add(edge)
            github_evidence.append({
                "source": row["source"],
                "edge_type": row["edge_type"],
                "target": canonical,
                "original_target": row["target"],
                "evidence": row["evidence"],
                "confidence": row["confidence"],
                "population": row["population"],
            })
    canonical_baseline = {
        (source, edge_type, canonical_github(target))
        for source, edge_type, target in baseline_github
    }
    if canonical_edges != canonical_baseline:
        raise RuntimeError("Detailed and simple Dataset-GitHub projections differ")
    github_edge_path = output_dir / "dataset_github_readme_edges.csv"
    github_evidence_path = output_dir / "dataset_github_readme_evidence.csv"
    github_sample_path = output_dir / "dataset_github_validation_sample.csv"
    atomic_csv(github_edge_path, SIMPLE_FIELDS, [
        dict(zip(SIMPLE_FIELDS, edge)) for edge in sorted(canonical_edges)
    ])
    github_evidence.sort(key=lambda row: tuple(row[field] for field in GITHUB_EVIDENCE_FIELDS))
    atomic_csv(github_evidence_path, GITHUB_EVIDENCE_FIELDS, github_evidence)
    github_sample = validation_sample(
        github_evidence, GITHUB_EVIDENCE_FIELDS, ("confidence",), per_group=100
    )
    atomic_csv(
        github_sample_path,
        GITHUB_EVIDENCE_FIELDS + ["sample_group", "manual_label", "review_notes"],
        github_sample,
    )
    github_stats = graph_stats(canonical_edges)
    github_stats.update({
        "repository_nodes_case_insensitive": len({edge[2] for edge in canonical_edges}),
        "confidence": dict(Counter(row["confidence"] for row in github_evidence)),
        "population": dict(Counter(row["population"] for row in github_evidence)),
        "validation_sample_rows": len(github_sample),
    })
    summary["subgraphs"]["github"] = github_stats
    summary["verification"]["github"] = {
        "simple_detailed_projection_exact": True,
        "baseline_edges": len(baseline_github),
        "canonical_edges": len(canonical_edges),
        "missing": 0,
        "extra": 0,
        "duplicates": 0,
    }
    summary["outputs"]["github"] = {
        "edges": github_edge_path.name,
        "evidence": github_evidence_path.name,
        "validation_sample": github_sample_path.name,
    }

    dataset_dataset_sources = {
        "official_metadata": ROOT / "dataset_dataset_edges_max_coverage.csv",
        "refined_lineage": ROOT / (
            "dataset_dataset_extended_2026Oct02/refined_v3/"
            "dataset_lineage_high_confidence_edges.csv"
        ),
        "candidate_union": ROOT / (
            "dataset_dataset_extended_2026Oct02/refined_v3/"
            "dataset_dataset_expanded_union_pairs.csv"
        ),
        "validation_sample": ROOT / (
            "dataset_dataset_extended_2026Oct02/refined_v3/"
            "validation_sample_lineage.csv"
        ),
    }
    copied = {}
    for label, source in dataset_dataset_sources.items():
        target = output_dir / f"dataset_dataset_{label}.csv"
        copy_new(source, target)
        copied[label] = target.name
    official = read_simple(dataset_dataset_sources["official_metadata"])
    with dataset_dataset_sources["refined_lineage"].open(newline="", encoding="utf-8") as stream:
        refined_rows = list(csv.DictReader(stream))
    refined_typed = {
        (row["source"], row["edge_type"], row["target"])
        for row in refined_rows
    }
    refined_pairs = {(edge[0], edge[2]) for edge in refined_typed}
    official_pairs = {(edge[0], edge[2]) for edge in official}
    union_pairs = official_pairs | refined_pairs
    summary["subgraphs"]["dataset_dataset"] = {
        "official_metadata": graph_stats(official),
        "refined_typed_edges": len(refined_typed),
        "refined_pairs": len(refined_pairs),
        "refined_nodes": len({node for pair in refined_pairs for node in pair}),
        "pair_overlap": len(official_pairs & refined_pairs),
        "new_refined_pairs": len(refined_pairs - official_pairs),
        "candidate_union_pairs": len(union_pairs),
        "candidate_union_nodes": len({node for pair in union_pairs for node in pair}),
        "publication_status": "requires_manual_validation",
    }
    summary["verification"]["dataset_dataset"] = {
        "official_metadata_reproduced_in_prior_evidence_audit": True,
        "official_edges": len(official),
        "validation_sample_rows": 600,
    }
    summary["outputs"]["dataset_dataset"] = copied

    summary_path = output_dir / "summary.json"
    with summary_path.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")

    manifest_rows = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            manifest_rows.append({
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            })
    atomic_csv(output_dir / "manifest.csv", ["file", "bytes", "sha256"], manifest_rows)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
