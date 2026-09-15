<!--
Change history
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
among Hugging Face models, datasets, libraries, licenses, tasks, and linked
GitHub repositories. Version 1 captures model derivation, dataset-to-model
training references, and dataset derivation. Version 2 extends that graph with
model and dataset relationships to libraries, licenses, tasks, and GitHub
repositories.

This repository contains artifacts related to the CIKM 2025 paper:

> *HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem*

## Released artifacts

| File | Status | Description |
|---|---|---|
| `HuggingGraph_v0.dot` | Legacy | Original paper-era graph using raw repository IDs and the `label` edge attribute. |
| `HuggingGraph_v1.dot` | Previous | Expanded graph with typed model/dataset IDs and both `label` and `edge_type` attributes. |
| `HuggingGraph_v2.dot` | Current | Version 2 graph in v1-compatible DOT syntax. |
| `subgraph.pdf` | Example | Small visualization suitable for inspection. |

Version 0 remains available for reproducibility. Version 1 is a schema update,
not a byte-compatible replacement for v0. Version 2 preserves every v1 edge
and adds eight model/dataset attribute subgraphs.

## HuggingGraph v1 scale

| Relationship | Unique edges | Unique nodes within subgraph |
|---|---:|---:|
| Model to model | 966,035 | 978,868 |
| Dataset to model | 362,064 | 287,539 |
| Dataset to dataset | 5,217 | 5,974 |
| **Merged graph** | **1,333,316** | **1,128,000** |

The merged node count is not the sum of the three subgraph node counts because
models and datasets recur across subgraphs. The merged graph contains 1,062,824
unique model nodes and 65,176 unique dataset nodes.

Only nodes participating in at least one v1 edge are represented. The complete
crawled populations were 3,029,377 models and 1,023,134 datasets.

## HuggingGraph v2 scale

HuggingGraph v2 contains eleven logical subgraphs. Model-side and dataset-side
attribute relationships are reported separately even though each attribute
pair is stored together in the corresponding combined edge file used to build
v2.

| Relationship | Unique edges | Unique nodes within subgraph |
|---|---:|---:|
| Model to model | 966,035 | 978,868 |
| Dataset to model | 362,064 | 287,539 |
| Dataset to dataset | 5,217 | 5,974 |
| Model to library | 1,310,095 | 1,213,566 |
| Dataset to library | 17,168 | 16,727 |
| Model to license | 1,089,530 | 1,091,486 |
| Dataset to license | 341,884 | 343,153 |
| Model to task | 575,630 | 540,231 |
| Dataset to task | 327,713 | 217,675 |
| Model to GitHub repository | 7,091 | 7,622 |
| Dataset to GitHub repository | 1,303 | 1,570 |
| **Unified HuggingGraph v2** | **5,003,730** | **See node-type breakdown below** |

HuggingGraph v2 contains 2,221,012 unique nodes after global deduplication. 
This is the union of all edge endpoints, not the sum of the eleven subgraph node counts. 
The detailed breakdown by node type is shown below. The same model or dataset can participate in several
subgraphs, and models and datasets can share library, license, task, or GitHub
repository targets.

| Node type | Unique nodes in v2 |
|---|---:|
| Model | 1,820,947 |
| Dataset | 390,814 |
| Library | 2,130 |
| License | 5,120 |
| Task | 847 |
| GitHub repository | 1,154 |
| **Total** | **2,221,012** |

## Node identifiers

Versions 1 and 2 prefix every internal node ID with its entity type. Version 2
uses six prefixes:

```text
model::owner/repository
dataset::owner/repository
library::library-name
license::license-identifier
task::task-identifier
github::owner/repository
```

Typed IDs prevent a model repository and a dataset repository with the same
`owner/repository` string from collapsing into one graph node. To recover the
underlying identifier, split once on `::` and use the second component.

## Edge schema

Lineage edges are directed from an upstream artifact to a downstream artifact.
Attribute edges are directed from a model or dataset to its library, license,
task, or linked GitHub repository.

| Canonical `edge_type` | Source | Target | Meaning |
|---|---|---|---|
| `finetune` | model | model | Target is fine-tuned from source. |
| `adapter` | model | model | Target is an adapter derived from source. |
| `quantized` | model | model | Target is a quantized form of source. |
| `merged` | model | model | Target is a merge containing source. |
| `converted` | model | model | Target is a format conversion of source. |
| `new_version` | model | model | Target is a declared newer version of source. |
| `trained_on` | dataset | model | Source dataset is declared as training data for target model. |
| `derived_from` | dataset | dataset | Target dataset is derived from source dataset. |
| `uses_library` | model or dataset | library | Source declares or is associated with the target library. |
| `has_license` | model or dataset | license | Source declares or is associated with the target license. |
| `performs_task` | model | task | Model performs or is associated with the target task. |
| `supports_task` | dataset | task | Dataset supports or is associated with the target task. |
| `links_to_github` | model or dataset | GitHub repository | Source metadata contains a link to the target repository. |

Each v1 and v2 DOT edge carries two relationship attributes:

```dot
"model::parent" -> "model::child"
    [label="finetune", edge_type="finetune"];
```

`edge_type` is the canonical v1/v2 attribute. `label` is supplied for software
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

Version 2 derives its new relationships from parsed README/model-card and
README/dataset-card YAML metadata:

- Library edges use standard `library_name` declarations, selected alternative
  fields, and recognized library tags.
- License edges use standard license fields, custom license names, selected
  alternative fields, and recognized license tags.
- Task edges use model `pipeline_tag` values, dataset task categories and IDs,
  selected alternative fields, and recognized task tags.
- GitHub edges canonicalize matching URLs to `github::owner/repository`.

The v2 DOT file encodes the canonical `edge_type` and a compatibility `label`;
it does not encode confidence or provenance. In the extraction pipeline,
declared fields are treated as stronger evidence than tag-only or embedded
references. In particular, a GitHub URL embedded in a license or other
metadata field does not necessarily identify the source artifact's own code
repository.

The downloaded snapshots contain parsed card metadata rather than the complete
README body or every tag automatically computed by the live Hugging Face Hub.
Consequently, README-body-only GitHub links and some live Hub filters are not
fully represented. The Hub's `custom_code` filter is not equivalent to a
GitHub-link relationship.

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

1. Recognize `library::`, `license::`, `task::`, and `github::` node IDs.
2. Recognize `uses_library`, `has_license`, `performs_task`, `supports_task`,
   and `links_to_github` edge types.
3. Keep model tasks and dataset tasks semantically distinct.
4. Treat library-tag, task-tag, and embedded GitHub references as weaker than
   explicit declarations.
5. Use a streaming DOT parser or a filtered subgraph when loading the complete
   DOT graph would exceed available memory.

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


graph = read_dot("HuggingGraph_v2.dot")

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
