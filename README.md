<!--
Change history
2026-09-30 v2026.09.30-01
- Released HuggingGraph v3 with six Space relationship families.
- Normalized GitHub targets across model, dataset, and Space populations.
- Backup: README.md.bak.20260930-215839

2026-09-29 v2026.09.29-07
- Documented the eleven-subgraph HuggingGraph v2 release.
- Backup: git_upload_backups/HuggingGraph_v2_release_20260929-175020

2026-09-21 v2026.09.21-03
- Documented the metadata-collection and graph-construction source files.
- Backup: github_remote_backup_20260914-204130/readme_source_update_20260921/README.md.bak.20260921-185150

2026-09-14 v2026.09.14-12
- Documented HuggingGraph v2 as a DOT-only release.
- Backup: README.md.bak.20260914-225339

2026-09-14 v2026.09.14-11
- Documented HuggingGraph v2 and its library, license, task, and GitHub links.
- Preserved the v0 and v1 documentation.
- Backup: README.md.bak.20260914-225125
-->

# HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem

HuggingGraph is a directed, heterogeneous graph of supply-chain relationships
among Hugging Face models, datasets, Spaces, libraries, licenses, tasks, agent
frameworks, and linked GitHub repositories. Version 1 captures model derivation, dataset-to-model
training references, and dataset derivation. Version 2 extends that graph with
model and dataset relationships to libraries, licenses, tasks, and GitHub
repositories. Version 3 adds Space relationships to models, datasets, GitHub
repositories, licenses, tasks, and agent frameworks.

This repository contains artifacts related to the CIKM 2025 paper:

> *HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem*

## Released artifacts

| File | Status | Description |
|---|---|---|
| `HuggingGraph_v0.dot` | Legacy | Original paper-era graph using raw repository IDs and the `label` edge attribute. |
| `HuggingGraph_v1.dot` | Previous | Expanded graph with typed model/dataset IDs and both `label` and `edge_type` attributes. |
| `HuggingGraph_v2.dot` | Previous | Version 2 graph with model and dataset attribute relationships. |
| `HuggingGraph_v3.dot` | Current | Version 3 graph adding six Space relationship families. |
| `subgraph.pdf` | Example | Small visualization suitable for inspection. |

Version 0 remains available for reproducibility. Version 1 is a schema update,
not a byte-compatible replacement for v0. Version 2 preserves every v1 edge
and adds eight model/dataset attribute subgraphs. Version 3 preserves the prior
relationship families, refreshes their normalized inputs, and adds Space nodes.

## Source code

The reproducible metadata-collection and graph-construction code is in the
[`source/`](source/) directory. Install its direct Python dependencies with:

```bash
python -m pip install -r source/requirements.txt
```

Python 3.10 or later is recommended. The directory contains:

### Metadata collection

- `download_model_metadata.py` collects Hugging Face model-card metadata.
- `download_dataset_metadata.py` collects Hugging Face dataset-card metadata.
- `extract_model_github_edges_streaming.py` extracts normalized GitHub links
  from the complete model README crawl.
- `extract_dataset_github_edges_streaming.py` extracts normalized GitHub links
  from the complete dataset README crawl.
- `prepare_hugginggraph_v3_edges.py` reconciles the latest Space population and
  prepares the 17 normalized v3 edge inputs.

### Graph construction

- `model_lineage_edges.py` constructs declared and inferred model-lineage edges.
- `dataset_model_edges.py` constructs validated dataset-to-model training edges.
- `dataset_dataset_edges.py` constructs dataset-lineage edges.
- `model_library_edges.py` constructs model-to-library edges.
- `model_license_task_github_edges.py` constructs model license, task, and GitHub edges.
- `model_dataset_attribute_edges.py` combines model and dataset attribute subgraphs.
- `build_hugginggraph_v2.py` assembles the HuggingGraph v2 artifacts.
- `build_hugginggraph_v3.py` validates and assembles the HuggingGraph v3 DOT artifact.

### Supporting files

- `count_model_types.py` provides model-derivation classification used by `model_lineage_edges.py`.
- `requirements.txt` records the direct runtime dependencies.

The large metadata snapshots and intermediate edge files are inputs or generated
artifacts and are not stored in the `source/` directory.

## HuggingGraph v3 scale

HuggingGraph v3 contains 17 logical relationship families. It adds Space
relationships and uses one normalized GitHub repository identity across model,
dataset, and Space sources.

| Relationship | Unique edges | Unique nodes within subgraph |
|---|---:|---:|
| Model to model | 966,035 | 978,868 |
| Dataset to model | 362,064 | 287,539 |
| Dataset to dataset | 5,217 | 5,974 |
| Model to library | 1,311,386 | 1,214,466 |
| Dataset to library | 17,168 | 16,727 |
| Model to license | 1,090,845 | 1,092,560 |
| Dataset to license | 341,884 | 343,153 |
| Model to task | 577,077 | 541,491 |
| Dataset to task | 327,713 | 217,675 |
| Model to GitHub repository | 962,855 | 729,920 |
| Dataset to GitHub repository | 188,888 | 196,768 |
| Model to Space | 941,121 | 487,287 |
| Dataset to Space | 83,061 | 85,762 |
| Space to GitHub repository | 113,755 | 90,895 |
| Space to license | 420,369 | 420,557 |
| Space to task | 43,223 | 42,229 |
| Space to agent framework | 37,059 | 37,016 |
| **Unified HuggingGraph v3** | **7,789,720** | **See the node-type breakdown below** |

The node total is the global union of all endpoints. Relationship-specific
node counts must not be added because the same node can occur in several
subgraphs.

| Node type | Unique nodes in v3 |
|---|---:|
| Model | 1,888,591 |
| Dataset | 440,013 |
| Space | 712,728 |
| Library | 2,149 |
| License | 5,187 |
| Task | 866 |
| GitHub repository | 96,678 |
| Agent framework | 11 |
| **Total** | **3,146,223** |

The verified Space population contains 1,481,220 repositories: 413,102 have
at least one model or dataset relationship and 1,068,118 do not. Three Space
IDs present in the card snapshot were unavailable during relationship
reconciliation and are excluded from that verified population. Among connected
Space nodes, 59,747 link to 31,148 normalized GitHub repositories through
113,755 unique Space-to-GitHub edges.

## Crawled populations

| Population outcome | Models | Datasets |
|---|---:|---:|
| Readable README/card | 1,975,515 | 698,202 |
| No readable README | 1,005,324 | 286,579 |
| Restricted repository | 48,538 | 38,353 |
| **Total processed** | **3,029,377** | **1,023,134** |

## Node identifiers

Versions 1 through 3 prefix Hugging Face and categorical node IDs with their
entity type. Version 3 uses seven typed prefixes plus canonical GitHub repository URLs:

```text
model::owner/repository
dataset::owner/repository
space::owner/repository
library::library-name
license::license-identifier
task::task-identifier
agent::framework-name
https://github.com/owner/repository
```

Typed IDs prevent a model repository and a dataset repository with the same
`owner/repository` string from collapsing into one graph node. To recover the
underlying identifier, split once on `::` and use the second component.
GitHub repository nodes are instead stored as normalized HTTPS repository URLs
so that they can be followed directly.

## Edge schema

Lineage edges are directed from an upstream artifact to a downstream artifact.
Attribute edges are directed from a model, dataset, or Space to the relevant
artifact or category node.

| Canonical `edge_type` | Source | Target | Meaning |
|---|---|---|---|
| `finetune` | model | model | Target is fine-tuned from source. |
| `adapter` | model | model | Target is an adapter derived from source. |
| `quantized` | model | model | Target is a quantized form of source. |
| `merged` | model | model | Target is a merge containing source. |
| `trained_on` | dataset | model | Source dataset is declared as training data for target model. |
| `derived_from` | dataset | dataset | Target dataset is derived from source dataset. |
| `uses_library` | model or dataset | library | Source declares or is associated with the target library. |
| `has_license` | model, dataset, or Space | license | Source declares or is associated with the target license. |
| `performs_task` | model | task | Model performs or is associated with the target task. |
| `supports_task` | dataset or Space | task | Source supports or is associated with the target task. |
| `links_to_github` | model, dataset, or Space | GitHub repository | Source metadata contains a link to the target repository. |
| `used_by_space` | model or dataset | Space | A Space declares use of the source artifact. |
| `uses_agent_framework` | Space | agent framework | A Space declares or is classified with the target framework. |

Each v1, v2, and v3 DOT edge carries two relationship attributes:

```dot
"model::parent" -> "model::child"
    [label="finetune", edge_type="finetune"];
```

`edge_type` is the canonical attribute. `label` is supplied for software
written for v0. For merge edges only, the values intentionally differ:

```dot
[label="merge", edge_type="merged"]
```

This preserves the v0 term while making `merged` the canonical v1 term.

## Evidence and limitations

HuggingGraph v1 is a maximum-coverage graph. Its edges do not all have the same
evidence strength:

- Model-to-model edges include 943,861 declared and 22,174 inferred edges.
- Dataset-to-model edges include 253,111 references validated against the
  downloaded dataset universe and 108,953 unresolved declared references.
- Dataset-to-dataset edges include 4,212 validated and 1,005 unresolved but
  plausible parent references.

The aggregate DOT file does not encode per-edge confidence or provenance.
Treat inferred and unresolved relationships as candidates for additional
validation rather than equivalent to validated declarations.

Repository metadata is incomplete and user-authored. Absence of an edge does
not prove that no relationship exists. An unresolved ID may be private,
deleted, renamed, misspelled, or absent from the collection snapshot.

### Version 2 attribute evidence

Version 2 derives library, license, and task relationships from parsed
README/model-card and README/dataset-card YAML metadata. GitHub relationships
are extracted from the complete readable README bodies:

- Library edges use standard `library_name` declarations, selected alternative
  fields, and recognized library tags.
- License edges use standard license fields, custom license names, selected
  alternative fields, and recognized license tags.
- Task edges use model `pipeline_tag` values, dataset task categories and IDs,
  selected alternative fields, and recognized task tags.
- GitHub edges canonicalize matching URLs to
  `https://github.com/owner/repository`, removing subpaths, fragments, query
  strings, and case-only duplicates from logical repository accounting.

The v2 DOT file encodes the canonical `edge_type` and a compatibility `label`;
it does not encode confidence or provenance. In the extraction pipeline,
declared fields are treated as stronger evidence than tag-only or embedded
references. In particular, a GitHub URL embedded in a license or other
metadata field does not necessarily identify the source artifact's own code
repository.

The model and dataset GitHub scans processed 1,975,515 and 698,202 readable
README files respectively. Restricted and missing READMEs cannot contribute
README-body links, and repository metadata remains user-authored. The Hub's
`custom_code` filter is not equivalent to a GitHub-link relationship.

### Version 3 Space evidence

Version 3 obtains model and dataset usage from the Space API relationship
expansions. Space license, task, and agent-framework edges come from Space card
metadata. Space-to-GitHub edges are extracted from 1,475,109 readable Space
READMEs. A GitHub link is a declared reference and does not by itself prove a
runtime code dependency. GitHub targets are normalized to lowercase logical
`owner/repository` identities before v3 is assembled.

## Migration and compatibility

Consumers moving from v0 to v1 should:

1. Prefer `edge_type`; fall back to `label` for legacy files.
2. Treat legacy `merge` and canonical `merged` as aliases.
3. Recognize the additional `converted`, `new_version`, and `derived_from`
   relationships.
4. Preserve `model::` and `dataset::` while operating on graph nodes.
5. Remove the type prefix before using an identifier with the Hugging Face API.
6. Account for the fact that v1 contains connected endpoints rather than
   standalone population nodes.

Consumers moving from v1 to v2 should additionally:

1. Recognize `library::`, `license::`, and `task::` node IDs, plus normalized
   `https://github.com/owner/repository` targets.
2. Recognize `uses_library`, `has_license`, `performs_task`, `supports_task`,
   and `links_to_github` edge types.
3. Keep model tasks and dataset tasks semantically distinct.
4. Treat library-tag, task-tag, and embedded GitHub references as weaker than
   explicit declarations.
5. Use a streaming DOT parser or a filtered subgraph when loading the complete
   DOT graph would exceed available memory.

Consumers moving from v2 to v3 should additionally:

1. Recognize `space::` and `agent::` node IDs.
2. Recognize `used_by_space` and `uses_agent_framework` edge types.
3. Permit Space sources for `links_to_github`, `has_license`, and
   `supports_task` relationships.
4. Treat the 59,747 GitHub-linked Spaces as a subset of the 712,728 connected
   Space nodes rather than an additional node population.

## Loading DOT with NetworkX

Python 3.10 or later is recommended.

```bash
pip install networkx pydot
```

```python
from itertools import islice

from networkx.drawing.nx_pydot import read_dot


def unquote(value):
    return str(value).strip('"')


def relationship(attributes):
    value = attributes.get("edge_type") or attributes.get("label") or ""
    value = unquote(value)
    return "merged" if value == "merge" else value


def typed_identifier(node):
    node = unquote(node)
    if "::" not in node:
        return "unknown", node  # v0 raw identifier
    return tuple(node.split("::", 1))


graph = read_dot("HuggingGraph_v3.dot")

print(f"Nodes: {graph.number_of_nodes():,}")
print(f"Edges: {graph.number_of_edges():,}")

for source, target, attributes in islice(graph.edges(data=True), 10):
    source_type, source_id = typed_identifier(source)
    target_type, target_id = typed_identifier(target)
    edge_type = relationship(attributes)
    print(source_type, source_id, edge_type, target_type, target_id)
```

NetworkX and pydot may require substantial memory for the complete v2 graph.
For large-scale analysis, prefer streaming the DOT file or extracting a smaller
subgraph before loading it into an in-memory graph library.

## Visualization

Graphviz can render a small extracted subgraph:

```bash
dot -Tsvg subgraph.dot -o subgraph.svg
dot -Tpdf subgraph.dot -o subgraph.pdf
```

Rendering the complete v2 graph directly to SVG, PDF, or PNG is generally
impractical because of its size. Use a filtered subgraph for visualization.

## Paper context

HuggingGraph supports forward and backward tracing of dependencies, helping
researchers, auditors, and policymakers investigate provenance and inherited
security, bias, and licensing risks.

## Citation

If you use this graph or its figures, please cite the paper:

```bibtex
@inproceedings{rahman2025hugginggraph,
  title={Hugginggraph: Understanding the supply chain of llm ecosystem},
  author={Rahman, Mohammad Shahedur and Gao, Peng and Ji, Yuede},
  booktitle={Proceedings of the 34th ACM International Conference on Information and Knowledge Management},
  pages={5997--6005},
  year={2025}
}
```

## Contact

- [Yuede Ji](https://yuede.github.io)
- [Mohammad Shahedur Rahman](https://mdshahedrahman.github.io)
