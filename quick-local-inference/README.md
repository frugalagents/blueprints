# Local Inference & RAG — MCP Servers for Amazon Quick

Fully offline AI tools registered as MCP servers in Amazon Quick.
Everything runs on-device — suitable for air-gapped environments.

## What's included

| Server | Tools | Purpose |
|--------|-------|---------|
| `server.py` | `local_infer`, `local_list_models` | Chat completions via local model |
| `rag.py` | `local_rag_index`, `local_rag_query`, `local_rag_answer` | Parse → chunk → embed → retrieve → answer |

## Pre-requisites

```bash
# 1. Install Ollama
brew install ollama
ollama serve

# 2. Pull models (do this BEFORE going air-gapped)
ollama pull llama3.2:3b          # generation
ollama pull nomic-embed-text     # embeddings (RAG)
```

## Quick start

Both servers use inline `# /// script` metadata, so `uv` handles deps automatically:

```bash
# Test server.py
uv run server.py

# Test rag.py
uv run rag.py
```

Or install manually:

```bash
pip install -r requirements.txt
python server.py
python rag.py
```

## Register in Amazon Quick

Settings → Capabilities → Connections → Advanced Integrations → **+ Add MCP / Skill**

### server.py (local inference)

- **Command:** `uv`
- **Args:** `run /full/path/to/server.py`
- **Env:**
  - `LOCAL_LLM_BASE_URL` = `http://localhost:11434/v1` (Ollama default)
  - `LOCAL_LLM_MODEL` = `llama3.2:3b`
  - `LOCAL_LLM_API_KEY` = `ollama`

### rag.py (local RAG)

- **Command:** `uv`
- **Args:** `run /full/path/to/rag.py`
- **Env:**
  - `LOCAL_LLM_BASE_URL` = `http://localhost:11434` (note: no `/v1`)
  - `LOCAL_EMBED_MODEL` = `nomic-embed-text`
  - `LOCAL_GEN_MODEL` = `llama3.2:3b`
  - `LOCAL_RAG_STORE` = `~/.local-rag` (Chroma persist dir)

## Usage (via Quick chat)

```
"List my local models"
"Run this through my local model: <text>"
"Index this folder: /path/to/corpus"
"Answer from my corpus: what is the policy on X?"
```

## How RAG works

```
files → docling parse (structure-aware Markdown)
      → smart chunk (split on headings, size-split overflow)
      → embed (nomic-embed-text via Ollama)
      → store (Chroma on disk)
      → retrieve (cosine similarity, top-k)
      → answer (llama3.2:3b, grounded with citations)
```

**Parsing priority:** docling (PDF/DOCX/PPTX/XLSX/HTML/MD) → pypdf fallback → UTF-8 read.

**Chunking:** structure-aware — splits on Markdown headings from docling output. Sections exceeding 2000 chars are size-split with 250-char overlap. Preamble before the first heading is preserved.

## Air-gapped notes

- Pull both Ollama models **before** disconnecting.
- Run docling once on a sample file before going offline (it downloads layout models on first use).
- Chroma persists to disk at `LOCAL_RAG_STORE` — no network needed after first run.
- Quick must run in **Local Processing** mode (Settings → Customization → Local Processing) to orchestrate tools without Bedrock.

## Config reference

| Variable | Default | Used by |
|----------|---------|---------|
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434` | both |
| `LOCAL_LLM_MODEL` | `llama3.2:3b` | server.py |
| `LOCAL_LLM_API_KEY` | `ollama` | server.py |
| `LOCAL_EMBED_MODEL` | `nomic-embed-text` | rag.py |
| `LOCAL_GEN_MODEL` | `llama3.2:3b` | rag.py |
| `LOCAL_RAG_STORE` | `~/.local-rag` | rag.py |
