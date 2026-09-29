"""Create model-lineage edges from saved Hugging Face card metadata.

Two outputs are produced and deliberately kept separate:

model_lineage_edges.csv / .jsonl
    Declared lineage only. Schema is unchanged: source, edge_type, target.
    Every edge here comes from a field or tag that names a parent model
    explicitly.

model_lineage_edges_inferred.csv / .jsonl
    Weaker evidence, never mixed into the declared output. Schema is
    source, edge_type, target, evidence, confidence.

Change history:
2026-09-08 v2026.09.08-05
- Add the separate inferred-edge output described above.
- Mine five additional explicit parent fields into the declared output:
  base\\_model (markdown-escaped key), basemodel, source_model,
  duplicated_from and teacher_model.
- Add four inference layers, written only to the inferred output:
  ambiguous parent-ish fields, model IDs embedded in free-form card tags,
  same-owner license_link references whose name is contained in the child
  name, and same-owner repository-name suffix stripping.
- Record declared new_version successor relationships in the inferred output
  with edge_type new_version. Direction is older -> newer, which is the
  opposite of a parent edge, so these are never placed in the declared file.
- Consume trailing quantization shorthands such as q4_k_m as single
  repository-name tokens.
- Read models_without_metadata.jsonl and recover same-owner parents from
  repository names, the only signal those records carry.
- Validate every newly mined or inferred parent ID against the full crawled
  model-ID universe in all_huggingface_models_2026Aug28.csv.
- Backup: model_lineage_edges.py.bak.20260908-215638
2026-09-08 v2026.09.08-04
- Add validated parent IDs from an explicit allowlist of alternative fields.
- Normalize three observed misspellings of base_model_relationship.
- Backup: model_lineage_edges.py.bak.20260908-212730
2026-09-08 v2026.09.08-03
- Recover parent IDs encoded in base_model/base-model tags.
- Preserve tag-specific relationship types for tag-only parents.
- Backup: model_lineage_edges.py.bak.20260908-211848
"""

import csv
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


# Directory containing this script, independent of the current working directory.
root = Path(__file__).resolve().parent
input_path = root / "models_with_metadata.jsonl"
no_metadata_path = root / "models_without_metadata.jsonl"
universe_path = root / "all_huggingface_models_2026Aug28.csv"
csv_path = root / "model_lineage_edges.csv"
jsonl_path = root / "model_lineage_edges.jsonl"
inferred_csv_path = root / "model_lineage_edges_inferred.csv"
inferred_jsonl_path = root / "model_lineage_edges_inferred.jsonl"

# Reuse the repository's existing model-type classification logic.
classifier_path = root / "count_model_types.py"
spec = importlib.util.spec_from_file_location(
    "count_model_types",
    classifier_path,
)
classifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classifier)

valid_edge_types = {
    "finetune",
    "adapter",
    "quantized",
    "merged",
}

# Format-only conversions are not quantization, so they are reported with
# their own type and only ever appear in the inferred output.
conversion_edge_type = "converted"
successor_edge_type = "new_version"

# Applied when a field names a parent but no relationship is recognizable.
# This follows rule 7 in count_model_types.py, which already defaults a
# declared parent with no recognized relationship to fine-tune.
default_edge_type = "finetune"

alternative_parent_fields = {
    "Base Model": None,
    "Base model": None,
    "Base_model": None,
    "base model": None,
    "base-model": None,
    "base_models": None,
    "derived_from": "finetune",
    "finetuned_from": "finetune",
    "parent_model": None,
    "original_model": None,
    "real_base_model": None,
    "base_model_original": None,
    "base_model_sources": None,
}

# Additional fields that name a parent explicitly. Observed only on records
# with no base_model field, so they extend the declared output.
additional_parent_fields = {
    "base\\_model": None,
    "basemodel": None,
    "source_model": None,
    "duplicated_from": None,
    "teacher_model": "finetune",
}

# Fields that sometimes name a parent and sometimes name something else.
# Never written to the declared output.
weak_parent_fields = (
    "model",
    "models",
    "model_id",
    "model_link",
    "repo",
    "host_model",
    "prior",
    "lm_studio",
)

misspelled_relationship_fields = (
    "base_model_ralation",
    "base_model_realtion",
    "base_model_relatin",
)

# Card tags that are namespaced index entries rather than model references.
ignored_tag_prefixes = (
    "base_model:",
    "base-model:",
    "dataset:",
    "datasets:",
    "license:",
    "arxiv:",
    "doi:",
    "region:",
    "language:",
)

quantization_tokens = {
    "gguf", "awq", "gptq", "exl2", "exl3", "exllama", "aqlm", "hqq", "quanto",
    "bnb", "eetq", "spqr", "vptq", "torchao", "autoround", "imatrix", "imat",
    "i1", "int2", "int3", "int4", "int8", "fp4", "fp8", "nf4", "nvfp4",
    "mxfp4", "w4a16", "w8a8", "quantized", "quantization", "quant", "quants",
    "gptqmodel", "smashed",
}

adapter_tokens = {
    "lora", "qlora", "loras", "adapter", "adapters", "peft", "dora",
    "adalora", "loha", "lokr", "ia3", "oft", "boft", "vera",
}

merge_tokens = {
    "merge", "merged", "mergekit", "slerp", "ties", "dare", "linear-merge",
    "task-arithmetic",
}

conversion_tokens = {
    "onnx", "openvino", "ov", "ct2", "ctranslate2", "coreml", "tflite",
    "trt", "tensorrt", "neuron", "mlx", "safetensors", "bf16", "fp16",
    "ggml", "gguf-imat",
}

derivation_tokens = (
    quantization_tokens | adapter_tokens | merge_tokens | conversion_tokens
)

model_id_part = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
quantization_shorthand = re.compile(r"^(?:iq|q)\d(?:[_-][a-z0-9]+)*$")
bit_width_token = re.compile(r"^\d+bit$")
quantization_block = re.compile(
    r"[-_.](i?q\d(?:[_-][A-Za-z0-9]{1,3})+)$",
    re.IGNORECASE,
)
model_reference = re.compile(
    r"(?:https?://(?:www\.)?(?:huggingface\.co|hf\.co)/)?"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9._-]{1,96})"
)


def normalize_model_id(value):
    """Return a validated owner/repository ID, including from an HF URL."""
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate:
        return None

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

        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0].lower() in {
            "datasets",
            "spaces",
            "organizations",
        }:
            return None
        candidate = "/".join(parts[:2])

    if len(candidate) > 96 or candidate.count("/") != 1:
        return None

    namespace, repository = candidate.split("/", 1)
    for part in (namespace, repository):
        if (
            not model_id_part.fullmatch(part)
            or part[-1] in ".-"
            or "--" in part
            or ".." in part
        ):
            return None

    if repository.endswith(".git"):
        return None

    return candidate


def load_model_id_universe(path):
    """Load every crawled model ID, keyed by lowercase form."""
    universe = {}

    if not path.exists():
        return universe

    with path.open(encoding="utf-8") as handle:
        handle.readline()
        for line in handle:
            model_id = line.strip()
            if model_id:
                universe.setdefault(model_id.lower(), model_id)

    return universe


def resolve_known_id(value, universe):
    """Return the canonical crawled ID for a candidate parent reference."""
    candidate = normalize_model_id(value)

    if candidate is None:
        return None

    if not universe:
        return candidate

    return universe.get(candidate.lower())


def referenced_ids(value, universe, depth=0):
    """Return canonical IDs for every model reference inside a value."""
    found = []

    if depth > 6:
        return found

    if isinstance(value, str):
        for match in model_reference.finditer(value):
            resolved = resolve_known_id(
                match.group(1).rstrip("./-"),
                universe,
            )
            if resolved is not None:
                found.append(resolved)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.extend(referenced_ids(item, universe, depth + 1))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(referenced_ids(item, universe, depth + 1))

    return found


def alternative_parent_values(field, value):
    """Extract candidate strings only from the approved field structures."""
    if field != "base_model_sources":
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return [item for item in value if isinstance(item, str)]
        return []

    items = value if isinstance(value, (list, tuple, set)) else [value]
    candidates = []

    for item in items:
        if isinstance(item, str):
            candidates.append(item)
            continue
        if not isinstance(item, dict):
            continue

        for key in ("modelId", "model_id", "repo_id", "name", "repo_url", "url"):
            candidate = item.get(key)
            if isinstance(candidate, str):
                candidates.append(candidate)

    return candidates


def normalized_relationship_metadata(metadata):
    """Map observed relationship-key misspellings to the accepted alias."""
    if metadata.get("base_model_relation") is not None or metadata.get(
        "base_model_relationship"
    ) is not None:
        return metadata, False

    for field in misspelled_relationship_fields:
        if metadata.get(field) is not None:
            normalized = dict(metadata)
            normalized["base_model_relationship"] = metadata[field]
            return normalized, True

    return metadata, False


def tagged_parent_edges(metadata, fallback_edge_type):
    """Return parent/type pairs declared by indexed or legacy base-model tags."""
    tags = metadata.get("tags")

    if isinstance(tags, str):
        tags = [tags]
    elif not isinstance(tags, (list, tuple, set)):
        return [], 0

    edges = []
    malformed = 0

    for raw_tag in tags:
        if not isinstance(raw_tag, str):
            continue

        tag = raw_tag.strip()
        normalized_tag = tag.lower()

        if not normalized_tag.startswith(("base_model:", "base-model:")):
            continue

        payload = tag.split(":", 1)[1].strip()

        if not payload:
            malformed += 1
            continue

        relationship_text, separator, parent_text = payload.partition(":")
        relationship = classifier.RELATION_ALIASES.get(
            classifier.normalize_text(relationship_text)
        )

        if separator and relationship:
            source = parent_text.strip()
            edge_type = relationship
        else:
            # Legacy form: base_model:<model-id>.
            source = payload
            edge_type = fallback_edge_type

        if not source or edge_type not in valid_edge_types:
            malformed += 1
            continue

        edges.append((source, edge_type))

    return edges, malformed


def is_derivation_token(token):
    """Report whether a repository-name token marks a derived artifact."""
    return (
        token in derivation_tokens
        or quantization_shorthand.match(token) is not None
        or bit_width_token.match(token) is not None
    )


def name_candidates(repository):
    """Strip trailing derivation tokens, preserving the original separators."""
    stripped = []
    candidates = []
    remaining = repository

    while True:
        # A quantization shorthand such as q4_k_m contains separators of its
        # own, so it is consumed as one token before the word split.
        block = quantization_block.search(remaining)

        if block is not None:
            stripped.append(block.group(1).lower())
            remaining = remaining[: block.start()]
            candidates.append(remaining)
            continue

        pieces = re.split(r"([-_.])", remaining)
        words = pieces[0::2]
        separators = pieces[1::2]

        if len(words) < 2 or not is_derivation_token(words[-1].lower()):
            break

        stripped.append(words[-1].lower())
        name = words[0]

        for index in range(1, len(words) - 1):
            name += separators[index - 1] + words[index]

        remaining = name
        candidates.append(remaining)

    if not stripped:
        return [], []

    return candidates, stripped


def name_edge_type(tokens):
    """Choose an edge type from the stripped repository-name tokens."""
    if any(
        token in quantization_tokens
        or quantization_shorthand.match(token)
        or bit_width_token.match(token)
        for token in tokens
    ):
        return "quantized"
    if any(token in adapter_tokens for token in tokens):
        return "adapter"
    if any(token in merge_tokens for token in tokens):
        return "merged"
    if any(token in conversion_tokens for token in tokens):
        return conversion_edge_type
    return None


def same_owner_name_parent(model_id, universe):
    """Infer a same-owner parent from the child's repository name."""
    if not universe or "/" not in model_id:
        return None, None

    owner, repository = model_id.split("/", 1)
    candidates, tokens = name_candidates(repository)

    if not candidates:
        return None, None

    edge_type = name_edge_type(tokens)

    if edge_type is None:
        return None, None

    for candidate in candidates:
        resolved = universe.get(f"{owner}/{candidate}".lower())
        if resolved is not None and resolved != model_id:
            return resolved, edge_type

    return None, None


def comparable_name(text):
    """Reduce a repository name to comparable alphanumeric characters."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def license_link_parent(model_id, metadata, universe):
    """Return a same-owner license_link target contained in the child name."""
    if "/" not in model_id:
        return None

    owner, repository = model_id.split("/", 1)
    child = comparable_name(repository)

    for field in metadata:
        normalized_field = str(field).lower().replace("-", "_")

        if not normalized_field.startswith("license_link"):
            continue

        for reference in referenced_ids(metadata[field], universe):
            reference_owner, _, reference_name = reference.partition("/")

            if reference_owner.lower() != owner.lower():
                continue
            if reference == model_id:
                continue

            token = comparable_name(reference_name)

            if token and token != child and token in child:
                return reference

    return None


def tag_embedded_parents(metadata, universe):
    """Return model IDs embedded in free-form card tags."""
    tags = metadata.get("tags")

    if isinstance(tags, str):
        tags = [tags]
    elif not isinstance(tags, (list, tuple, set)):
        return []

    found = []

    for raw_tag in tags:
        if not isinstance(raw_tag, str):
            continue

        tag = raw_tag.strip()

        if tag.lower().startswith(ignored_tag_prefixes):
            continue

        found.extend(referenced_ids(tag, universe))

    return found


class EdgeWriter:
    """Write one edge row to a CSV file and a JSON Lines file together."""

    def __init__(self, csv_file, jsonl_file, fieldnames):
        self.fieldnames = fieldnames
        self.csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        self.csv_writer.writeheader()
        self.jsonl_file = jsonl_file

    def write(self, row):
        self.csv_writer.writerow(row)
        self.jsonl_file.write(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


universe = load_model_id_universe(universe_path)

seen_edges = set()
declared_edges_written = 0
tag_edges_written = 0
alternative_edges_written = 0
additional_edges_written = 0
skipped_self_edges = 0
skipped_duplicate_edges = 0
malformed_parent_tags = 0
invalid_alternative_parent_values = 0
normalized_relationship_records = 0

inferred_counts = {
    "weak_field": 0,
    "tag_embedded_id": 0,
    "license_link_sameowner": 0,
    "name_heuristic_sameowner": 0,
    "name_heuristic_no_metadata": 0,
    "new_version_field": 0,
}
no_metadata_records = 0
parentless_records = 0

inferred_fieldnames = ["source", "edge_type", "target", "evidence", "confidence"]


def register_edge(source, edge_type, target):
    """Validate and deduplicate one edge, reporting whether to write it."""
    global skipped_self_edges, skipped_duplicate_edges

    if source == target:
        skipped_self_edges += 1
        return False

    edge = (source, edge_type, target)

    if edge in seen_edges:
        skipped_duplicate_edges += 1
        return False

    seen_edges.add(edge)
    return True


with (
    input_path.open(encoding="utf-8") as input_file,
    csv_path.open("w", encoding="utf-8", newline="") as csv_file,
    jsonl_path.open("w", encoding="utf-8") as jsonl_file,
    inferred_csv_path.open("w", encoding="utf-8", newline="") as inferred_csv_file,
    inferred_jsonl_path.open("w", encoding="utf-8") as inferred_jsonl_file,
):
    declared_writer = EdgeWriter(
        csv_file,
        jsonl_file,
        ["source", "edge_type", "target"],
    )
    inferred_writer = EdgeWriter(
        inferred_csv_file,
        inferred_jsonl_file,
        inferred_fieldnames,
    )

    def write_inferred(source, edge_type, target, evidence, confidence):
        """Record one inferred edge with its provenance."""
        if edge_type not in valid_edge_types | {
            conversion_edge_type,
            successor_edge_type,
        }:
            return

        if not register_edge(source, edge_type, target):
            return

        inferred_counts[evidence] = inferred_counts.get(evidence, 0) + 1
        inferred_writer.write(
            {
                "source": source,
                "edge_type": edge_type,
                "target": target,
                "evidence": evidence,
                "confidence": confidence,
            }
        )

    for line_number, line in enumerate(input_file, start=1):
        if not line.strip():
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Skipping invalid JSON on line {line_number}: {exc}")
            continue

        target = record.get("modelId")
        metadata = record.get("readme_metadata")

        if not isinstance(target, str) or not isinstance(metadata, dict):
            continue

        target = target.strip()
        if not target:
            continue

        classification_metadata, relationship_was_normalized = (
            normalized_relationship_metadata(metadata)
        )
        normalized_relationship_records += int(relationship_was_normalized)

        declared_parents = classifier.normalize_base_models(
            classification_metadata.get("base_model")
        )
        fallback_edge_type, _reason = classifier.classify_model(
            classification_metadata
        )

        # A source may be declared in base_model, in a base-model tag, or both.
        # Prefer the ordinary metadata field when both forms name the same source.
        parent_edges = {}

        if fallback_edge_type in valid_edge_types:
            for source in declared_parents:
                source = source.strip()
                if source:
                    parent_edges[source] = (fallback_edge_type, "base_model")

        tagged_edges, malformed = tagged_parent_edges(
            classification_metadata,
            fallback_edge_type,
        )
        malformed_parent_tags += malformed

        for source, edge_type in tagged_edges:
            parent_edges.setdefault(source, (edge_type, "tag"))

        for field, forced_edge_type in alternative_parent_fields.items():
            if field not in classification_metadata:
                continue

            candidates = alternative_parent_values(
                field,
                classification_metadata[field],
            )

            for candidate in candidates:
                source = normalize_model_id(candidate)
                edge_type = forced_edge_type or fallback_edge_type

                if source is None or edge_type not in valid_edge_types:
                    invalid_alternative_parent_values += 1
                    continue

                parent_edges.setdefault(
                    source,
                    (edge_type, f"alternative:{field}"),
                )

        # Additional explicit parent fields. A parent is accepted only when the
        # named model exists in the crawled ID universe.
        for field, forced_edge_type in additional_parent_fields.items():
            if field not in classification_metadata:
                continue

            for candidate in alternative_parent_values(
                field,
                classification_metadata[field],
            ):
                source = resolve_known_id(candidate, universe)

                if source is None:
                    invalid_alternative_parent_values += 1
                    continue

                edge_type = forced_edge_type or fallback_edge_type

                if edge_type not in valid_edge_types:
                    edge_type = default_edge_type

                parent_edges.setdefault(
                    source,
                    (edge_type, f"additional:{field}"),
                )

        for source, (edge_type, evidence) in parent_edges.items():
            if not register_edge(source, edge_type, target):
                continue

            if evidence == "base_model":
                declared_edges_written += 1
            elif evidence == "tag":
                tag_edges_written += 1
            elif evidence.startswith("additional:"):
                additional_edges_written += 1
            else:
                alternative_edges_written += 1

            declared_writer.write(
                {
                    "source": source,
                    "edge_type": edge_type,
                    "target": target,
                }
            )

        # Declared successor relationships. Direction is older -> newer, so
        # these are kept out of the parent-lineage output.
        for successor in referenced_ids(
            classification_metadata.get("new_version"),
            universe,
        ):
            write_inferred(
                target,
                successor_edge_type,
                successor,
                "new_version_field",
                "declared_successor",
            )

        if parent_edges:
            continue

        # No declared parent: fall back to the inference layers, in order of
        # decreasing evidence strength.
        parentless_records += 1

        weak_edge_type = (
            fallback_edge_type
            if fallback_edge_type in valid_edge_types
            else default_edge_type
        )

        for field in weak_parent_fields:
            if field not in classification_metadata:
                continue

            for source in referenced_ids(
                classification_metadata[field],
                universe,
            ):
                write_inferred(
                    source,
                    weak_edge_type,
                    target,
                    "weak_field",
                    "declared_weak",
                )

        license_source = license_link_parent(target, classification_metadata, universe)

        if license_source is not None:
            _, license_tokens = name_candidates(target.split("/", 1)[1])
            license_edge_type = (
                name_edge_type(license_tokens)
                or (
                    fallback_edge_type
                    if fallback_edge_type in valid_edge_types
                    else default_edge_type
                )
            )
            write_inferred(
                license_source,
                license_edge_type,
                target,
                "license_link_sameowner",
                "inferred_high",
            )

        for source in tag_embedded_parents(classification_metadata, universe):
            write_inferred(
                source,
                weak_edge_type,
                target,
                "tag_embedded_id",
                "inferred_medium",
            )

        name_source, name_type = same_owner_name_parent(target, universe)

        if name_source is not None:
            write_inferred(
                name_source,
                name_type,
                target,
                "name_heuristic_sameowner",
                "inferred_high",
            )

    # Records with no card metadata at all carry only their repository name.
    if no_metadata_path.exists():
        with no_metadata_path.open(encoding="utf-8") as no_metadata_file:
            for line_number, line in enumerate(no_metadata_file, start=1):
                if not line.strip():
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"Skipping invalid JSON on no-metadata line {line_number}: {exc}")
                    continue

                target = record.get("modelId")

                if not isinstance(target, str) or not target.strip():
                    continue

                target = target.strip()
                no_metadata_records += 1

                name_source, name_type = same_owner_name_parent(target, universe)

                if name_source is not None:
                    write_inferred(
                        name_source,
                        name_type,
                        target,
                        "name_heuristic_no_metadata",
                        "inferred_high",
                    )

declared_total = (
    declared_edges_written
    + tag_edges_written
    + alternative_edges_written
    + additional_edges_written
)
inferred_total = sum(inferred_counts.values())

if not universe:
    print(f"WARNING: {universe_path.name} not found; inference layers disabled")

print(f"Model IDs in crawled universe: {len(universe):,}")
print()
print(f"Declared edges: {declared_total:,}")
print(f"  From base_model fields:      {declared_edges_written:,}")
print(f"  From tag-only parents:       {tag_edges_written:,}")
print(f"  From alternative fields:     {alternative_edges_written:,}")
print(f"  From additional fields:      {additional_edges_written:,}")
print()
print(f"Inferred edges: {inferred_total:,}")
print(f"  Weak parent fields:          {inferred_counts['weak_field']:,}")
print(f"  Tag-embedded model IDs:      {inferred_counts['tag_embedded_id']:,}")
print(f"  license_link same owner:     {inferred_counts['license_link_sameowner']:,}")
print(f"  Name, with metadata:         {inferred_counts['name_heuristic_sameowner']:,}")
print(f"  Name, no metadata:           {inferred_counts['name_heuristic_no_metadata']:,}")
print(f"  Declared new_version:        {inferred_counts['new_version_field']:,}")
print()
print(f"Total unique edges:            {len(seen_edges):,}")
print(f"Parentless metadata records:   {parentless_records:,}")
print(f"No-metadata records scanned:   {no_metadata_records:,}")
print(f"Skipped self-edges:            {skipped_self_edges:,}")
print(f"Skipped duplicate edges:       {skipped_duplicate_edges:,}")
print(f"Malformed parent tags:         {malformed_parent_tags:,}")
print(f"Invalid alternative values:    {invalid_alternative_parent_values:,}")
print(f"Normalized relationship keys:  {normalized_relationship_records:,}")
print()
print(f"Declared CSV:   {csv_path}")
print(f"Declared JSONL: {jsonl_path}")
print(f"Inferred CSV:   {inferred_csv_path}")
print(f"Inferred JSONL: {inferred_jsonl_path}")
