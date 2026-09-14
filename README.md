# HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem

HuggingGraph is a directed, heterogeneous graph of supply-chain relationships
among Hugging Face models and datasets. It captures model derivation,
dataset-to-model training references, and dataset derivation.

This repository contains artifacts related to the CIKM 2025 paper:

> *HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem*

## Released artifacts

| File | Status | Description |
|---|---|---|
| `HuggingGraph_v0.dot` | Legacy | Original paper-era graph using raw repository IDs and the `label` edge attribute. |
| `HuggingGraph_v1.dot` | Current | Expanded graph with typed node IDs and both `label` and `edge_type` attributes. |
| `subgraph.pdf` | Example | Small visualization suitable for inspection. |

Version 0 remains available for reproducibility. Version 1 is a schema update,
not a byte-compatible replacement.

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

## Node identifiers

Version 1 prefixes every internal DOT node ID with its entity type:

```text
model::owner/repository
dataset::owner/repository
```

Typed IDs prevent a model repository and a dataset repository with the same
`owner/repository` string from collapsing into one graph node. To recover the
Hugging Face repository ID, split once on `::` and use the second component.

## Edge schema

All edges are directed from upstream artifact to downstream artifact.

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

Each v1 DOT edge carries two relationship attributes:

```dot
"model::parent" -> "model::child"
    [label="finetune", edge_type="finetune"];
```

`edge_type` is the canonical v1 attribute. `label` is supplied for software
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

## Migration from v0

Consumers moving from v0 to v1 should:

1. Prefer `edge_type`; fall back to `label` for legacy files.
2. Treat legacy `merge` and canonical `merged` as aliases.
3. Recognize the additional `converted`, `new_version`, and `derived_from`
   relationships.
4. Preserve `model::` and `dataset::` while operating on graph nodes.
5. Remove the type prefix before using an identifier with the Hugging Face API.
6. Account for the fact that v1 contains connected endpoints rather than
   standalone population nodes.

## Loading v0 or v1 with NetworkX

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


graph = read_dot("HuggingGraph_v1.dot")

print(f"Nodes: {graph.number_of_nodes():,}")
print(f"Edges: {graph.number_of_edges():,}")

for source, target, attributes in islice(graph.edges(data=True), 10):
    source_type, source_id = typed_identifier(source)
    target_type, target_id = typed_identifier(target)
    edge_type = relationship(attributes)
    print(source_type, source_id, edge_type, target_type, target_id)
```

NetworkX and pydot may require substantial memory for the complete v1 graph.
For large-scale analysis, prefer streaming the DOT file or extracting a smaller
subgraph before loading it into an in-memory graph library.

## Visualization

Graphviz can render a small extracted subgraph:

```bash
dot -Tsvg subgraph.dot -o subgraph.svg
dot -Tpdf subgraph.dot -o subgraph.pdf
```

Rendering the complete v1 graph directly to SVG, PDF, or PNG is generally
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
