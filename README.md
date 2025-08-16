# HuggingGraph: Understanding the Supply Chain of LLM Ecosystem

---

## 📖 Overview

**HuggingGraph** is a heavy-tailed, heterogeneous graph that captures **lineage, dependencies, and risks** across Large Language Models (LLMs) and datasets.
It is constructed from Hugging Face metadata, repository cross-links, and textual analysis, modeling how **datasets, base models, fine-tunes, adapters, quantized models, and merges** interconnect in the AI supply chain.

This repository contains the artifacts released with our **CIKM 2025** paper:

> *HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem*

---

## 📂 Repository Contents

* **`HuggingGraph.zip`** – Full graph in Graphviz `.dot` format (compressed).
* **`subgraph.pdf`** – A sample subgraph figure (as shown in the paper), highlighting forward and backward dependencies for a representative model.

---

## 🚀 Usage

### 1. Extract the Graph

```bash
unzip HuggingGraph.zip -d data
# Produces: data/HuggingGraph.dot
```

### 2. Visualize (Graphviz)

```bash
dot -Tsvg data/HuggingGraph.dot -o HuggingGraph.svg
```

⚠️ The full graph is very large — direct rendering is impractical. Start with **subgraphs** or use the provided `subgraph.pdf` for illustration.

### 3. Programmatic Exploration (Python / NetworkX)

```python
import networkx as nx
from networkx.drawing.nx_pydot import read_dot

G = read_dot("data/HuggingGraph.dot")
print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
```

---

## 📊 Graph Schema

* **Node types**:

  * `dataset`, `base`, `finetune`, `adapter`, `quantization`, `merge`

* **Edge semantics**:

  * **Model → Model**: derivations (fine-tune, adapter, quantization, merge)
  * **Dataset → Model / Model → Dataset**: training or usage links
  * **Dataset → Dataset**: subset or variant relationships

---

## 📑 Paper Context

HuggingGraph enables **systematic analysis of the LLM ecosystem**:

* **Scale**: over 400K nodes and 460K edges.
* **Findings**:

  * Ecosystem is **hub-dominated** — a few datasets (e.g., *The Pile*) and base models (e.g., *LLaMA*) influence thousands of downstream artifacts.
  * **Daily churn** — fine-tunes and variants emerge at high velocity, reshaping the graph continuously.
  * **Risks** — poor provenance, missing metadata, and hidden dataset reuse introduce vulnerabilities in **security, bias, and licensing**.

HuggingGraph supports **forward and backward tracing** of dependencies, helping researchers, auditors, and policymakers validate provenance and detect inherited risks.

---

## 📎 Citation

If you use this dataset or figures, please cite the paper:

```bibtex
# Citation details will be updated soon

```

---

## 📬 Contact  

For questions, updates, and related work, visit:  
👉 <a href="https://yuede.github.io" target="_blank">https://yuede.github.io</a>; <a href="https://mdshahedrahman.github.io" target="_blank">https://mdshahedrahman.github.io</a>  

---
