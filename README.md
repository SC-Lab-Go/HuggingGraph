# HuggingGraph: Understanding the Supply Chain of LLM Ecosystem

---

## 📖 Overview

**HuggingGraph** is a heavy-tailed, heterogeneous graph that captures **Supply Chain Relationships** across Large Language Models (LLMs) and datasets. It is constructed from Hugging Face metadata, repository cross-links, and textual analysis, modeling how **datasets, base models, fine-tunes, adapters, quantized models, and merges** interconnect in the AI supply chain.

This repository contains the artifacts released with our **CIKM 2025** paper:

> *HuggingGraph: Understanding the Supply Chain of the LLM Ecosystem*

---

## 📂 Repository Contents

* **`HuggingGraph.dot`** – Full graph in Graphviz `.dot` format.  
* **`subgraph.pdf`** – A sample subgraph figure (as shown in the paper), highlighting the supply chain relationship for a representative model.  
* **`README.md`** – Documentation and usage instructions.  

---

## ⚡ Getting Started

To work with HuggingGraph, you will need:

### 1. Install Graphviz  
Graphviz is required to render the `.dot` graph into PDF, PNG, or SVG formats.  

```bash
# Ubuntu / Debian
sudo apt-get install graphviz

# macOS (Homebrew)
brew install graphviz

# Windows (Chocolatey)
choco install graphviz
````

Verify installation:

```bash
dot -V
```

---

### 2. Set Up Python Environment

We recommend Python **3.10+**. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate   # Linux / macOS
venv\Scripts\activate      # Windows
```

Install dependencies:

```bash
pip install networkx pydot
```

---

## 🚀 Usage

### 1. Access the Graph

The repository now directly provides the graph as a **Graphviz DOT file**:

* **`HuggingGraph.dot`** → the full constructed graph of the LLM ecosystem
* **`subgraph.pdf`** → a smaller excerpt for quick visualization and presentations

No extraction step is needed — you can work with the `.dot` file directly.

---

### 2. Visualize with Graphviz

You can render the `.dot` file into different formats (e.g., **SVG**, **PNG**, **PDF**) using [Graphviz](https://graphviz.org/).

**Render the graph:**

```bash
# Render to SVG
dot -Tsvg HuggingGraph.dot -o HuggingGraph.svg

# Render to PDF
dot -Tpdf HuggingGraph.dot -o HuggingGraph.pdf

# Render to PNG
dot -Tpng HuggingGraph.dot -o HuggingGraph.png
```

⚠️ Directly rendering the full graph may be impractical due to its size.
Start with **subgraphs** or use the provided `subgraph.pdf` for illustration.

---

### 3. Programmatic Exploration (Python / NetworkX)

You can load and analyze the graph with [NetworkX](https://networkx.org/):

```python
import networkx as nx
from networkx.drawing.nx_pydot import read_dot

# Load the graph
G = read_dot("HuggingGraph.dot")

# Print basic statistics
print(f"Nodes: {G.number_of_nodes()}")
print(f"Edges: {G.number_of_edges()}")

# Example: list first 10 nodes
print(list(G.nodes())[:10])
```
---

### 4. Working with Subgraphs

Because the full graph is very large, it is often more practical to work with smaller **subgraphs**.

Example: extract the first 100 nodes as a subgraph:

```python
subset_nodes = list(G.nodes())[:100]
H = G.subgraph(subset_nodes)

# Export to DOT format
nx.nx_pydot.write_dot(H, "subgraph.dot")
```

Then render with Graphviz as before:

```bash
dot -Tpdf subgraph.dot -o subgraph.pdf
```

Or simply open the provided **`subgraph.pdf`** for a ready-to-use illustration.

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

For questions, updates, and related work, visit:<br>
👉 [Yuede Ji](https://yuede.github.io) <br>
👉 [Mohammad Shahedur Rahman](https://mdshahedrahman.github.io)

