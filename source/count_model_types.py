#!/usr/bin/env python3
"""Count model types from saved Hugging Face README metadata.

Change history:
2026-09-04 v2026.09.04-03
- Recognize legacy finetune:<value> tags as fine-tuning evidence.
- Default a remaining legacy base_model:<model-id> tag to fine-tune.
- Backup: count_model_types.py.bak.20260904-151244
2026-09-04 v2026.09.04-02
- Classify a legacy base_model:<model-id> tag accompanied by a merge tag as
  a heuristic merge only after the existing classification rules are checked.
- Backup: count_model_types.py.bak.20260904-150719
2026-09-04 v2026.09.04-01
- Default models with a declared base_model but no recognized relationship to
  finetune, while preserving all existing explicit and tag-based rules.
- Backup: count_model_types.py.bak.20260904-145808
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ADAPTER_TAGS = {
    "adapter", "peft", "lora", "adalora", "ia3",
    "loha", "lokr", "oft", "boft", "vera",
    "prompt-tuning", "prefix-tuning", "p-tuning",
}

MERGE_TAGS = {
    "merge", "merged", "mergekit", "mergekit-moe",
    "ties", "ties-merging", "dare", "dare-ties",
    "slerp", "linear-merge", "task-arithmetic",
}

FINETUNE_TAGS = {
    "finetune", "fine-tune", "fine-tuned", "finetuned",
    "fine-tuning", "sft", "instruction-tuning",
    "instruction-tuned", "dpo", "orpo", "kto", "rlhf",
}

QUANTIZED_TAGS = {
    "quantized", "quantization", "gguf", "gptq", "awq",
    "aqlm", "exl2", "hqq", "quanto", "bitsandbytes",
    "bnb", "compressed-tensors", "eetq", "autoround",
    "spqr", "vptq", "torchao", "int4", "int8", "fp8",
}


# Normalize alternative spellings to the five output classes.
RELATION_ALIASES = {
    # Fine-tuned
    "finetune": "finetune",
    "fine-tune": "finetune",
    "fine-tuned": "finetune",
    "finetuned": "finetune",
    "fine_tune": "finetune",
    "fine_tuned": "finetune",

    # Adapter
    "adapter": "adapter",
    "adapters": "adapter",
    "peft": "adapter",

    # Quantized
    "quantized": "quantized",
    "quantization": "quantized",
    "quantize": "quantized",

    # Merge
    "merge": "merged",
    "merged": "merged",
}


def normalize_text(value: Any) -> str:
    """Convert a metadata value to normalized lowercase text."""
    return str(value).strip().lower().replace("_", "-")


def normalize_tags(value: Any) -> set[str]:
    """Return normalized tags regardless of whether tags is a string or list."""
    if value is None:
        return set()

    if isinstance(value, str):
        return {normalize_text(value)}

    if isinstance(value, (list, tuple, set)):
        return {
            normalize_text(tag)
            for tag in value
            if tag is not None and str(tag).strip()
        }

    return {normalize_text(value)}


def normalize_base_models(value: Any) -> list[str]:
    """Convert base_model metadata into a list of model IDs."""
    if value is None:
        return []

    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []

    if isinstance(value, (list, tuple, set)):
        return [
            str(model_id).strip()
            for model_id in value
            if model_id is not None and str(model_id).strip()
        ]

    return [str(value).strip()] if str(value).strip() else []


def explicit_relationship(metadata: dict[str, Any]) -> str | None:
    """
    Read the explicit relationship first.

    Hugging Face's official field is base_model_relation.
    base_model_relationship is also accepted in case it appears in the data.
    """
    relationship = metadata.get("base_model_relation")

    if relationship is None:
        relationship = metadata.get("base_model_relationship")

    if relationship is None:
        return None

    # The standard field is normally a string, but handle a one-value list too.
    if isinstance(relationship, (list, tuple, set)):
        normalized_values = {
            normalize_text(value)
            for value in relationship
            if value is not None and str(value).strip()
        }

        resolved = {
            RELATION_ALIASES[value]
            for value in normalized_values
            if value in RELATION_ALIASES
        }

        return next(iter(resolved)) if len(resolved) == 1 else None

    normalized = normalize_text(relationship)
    return RELATION_ALIASES.get(normalized)


def indexed_relationship(tags: set[str]) -> str | None:
    """
    Read authoritative indexed tags such as:

        base_model:finetune:organization/model
        base_model:adapter:organization/model
    """
    relationships = set()

    for tag in tags:
        if not tag.startswith("base-model:") and not tag.startswith("base_model:"):
            continue

        parts = tag.replace("base_model:", "base-model:", 1).split(":", 2)

        if len(parts) < 2:
            continue

        relationship = RELATION_ALIASES.get(normalize_text(parts[1]))

        if relationship:
            relationships.add(relationship)

    return next(iter(relationships)) if len(relationships) == 1 else None


def classify_model(metadata: dict[str, Any]) -> tuple[str, str]:
    """
    Return (model_type, reason).

    Classification priority:
      1. Explicit base_model_relation/base_model_relationship
      2. Indexed base_model:<relationship>:<model> tags
      3. Merge condition
      4. Quantization tags
      5. Adapter tags
      6. Fine-tuning tags
      7. Declared base_model fallback to fine-tune
      8. Legacy base_model:<model> tag plus merge tag (heuristic)
      9. Remaining legacy base_model:<model> tag fallback to fine-tune
      10. Base model
    """
    tags = normalize_tags(metadata.get("tags"))
    base_models = normalize_base_models(metadata.get("base_model"))

    # 1. Explicit metadata relationship has highest priority.
    relationship = explicit_relationship(metadata)

    if relationship:
        return relationship, "explicit_base_model_relation"

    # 2. Check Hugging Face's indexed relationship tags.
    relationship = indexed_relationship(tags)

    if relationship:
        return relationship, "indexed_base_model_tag"

    has_indexed_base_tag = any(
        tag.startswith("base_model:") or tag.startswith("base-model:")
        for tag in tags
    )

    # 3. Merge: at least two parents and a merge-related tag.
    if len(set(base_models)) >= 2 and tags.intersection(MERGE_TAGS):
        return "merged", "multiple_base_models_and_merge_tag"

    # 4. A quantized artifact can also carry fine-tuning tags.
    if tags.intersection(QUANTIZED_TAGS):
        return "quantized", "quantization_tag"

    # 5. Adapter models are also technically fine-tuned, so check adapters first.
    if tags.intersection(ADAPTER_TAGS):
        return "adapter", "adapter_tag"

    # 6. General fine-tuning indicators.
    has_legacy_finetune_tag = any(
        tag.startswith((
            "finetune:",
            "fine-tune:",
            "fine-tuned:",
            "finetuned:",
            "fine-tuning:",
        ))
        for tag in tags
    )

    if tags.intersection(FINETUNE_TAGS) or has_legacy_finetune_tag:
        return "finetune", "finetune_tag"

    # 7. Approved fallback: a declared parent with no recognized relationship
    # or qualifying tag is treated as a fine-tuned model.
    if base_models:
        return "finetune", "base_model_present_default_finetune"

    # 8. Legacy metadata sometimes encodes only base_model:<model-id>. When a
    # merge tag is also present, retain the otherwise unresolved model as a
    # heuristic merge without overriding any established classification rule.
    if has_indexed_base_tag and tags.intersection(MERGE_TAGS):
        return "merged", "legacy_indexed_base_model_and_merge_tag"

    # 9. Approved fallback for legacy base_model:<model-id> tags with no
    # recognized relationship or higher-priority classification evidence.
    if has_indexed_base_tag:
        return "finetune", "legacy_base_model_tag_default_finetune"

    # 10. No relationship and no base_model means treat it as a base model.
    if not base_models and not has_indexed_base_tag:
        return "base", "no_base_model_relationship"

    return "unclassified", "unresolved_model_metadata"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Count model types from Hugging Face README metadata."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=Path("models_with_metadata.jsonl"),
    )
    args = parser.parse_args()

    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    invalid_lines = 0
    total = 0

    with args.input.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid_lines += 1
                print(f"Invalid JSON at line {line_number}: {exc}")
                continue

            metadata = record.get("readme_metadata")

            if not isinstance(metadata, dict):
                counts["unclassified"] += 1
                reasons["missing_metadata_object"] += 1
                total += 1
                continue

            model_type, reason = classify_model(metadata)
            counts[model_type] += 1
            reasons[reason] += 1
            total += 1

    print("\nModel type counts")
    print("-----------------")

    categories = [
        "base",
        "finetune",
        "adapter",
        "quantized",
        "merged",
        "unclassified",
    ]

    for category in categories:
        count = counts[category]
        percentage = (count / total * 100) if total else 0
        print(f"{category:15} {count:12,}  {percentage:7.2f}%")

    print("-----------------")
    print(f"{'total':15} {total:12,}")
    print(f"{'invalid JSON':15} {invalid_lines:12,}")

    print("\nClassification reasons")
    print("----------------------")

    for reason, count in reasons.most_common():
        print(f"{reason:45} {count:12,}")


if __name__ == "__main__":
    main()
