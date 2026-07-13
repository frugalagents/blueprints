#!/usr/bin/env python3
"""
Local RAG MCP server — fully offline retrieval-augmented generation.

Adds three tools to the local-inference toolset. Everything runs on-device:
parse -> chunk -> embed (Ollama) -> store (Chroma on disk) -> retrieve ->
answer (Ollama). No cloud calls at any step. Suitable for air-gapped use once
the two Ollama models are pulled.

Pre-reqs (pull BEFORE going air-gapped):
    ollama pull nomic-embed-text     # embeddings
    ollama pull llama3.2:3b          # generation

Deps:
    uv run rag.py     # (inline metadata below handles this)

Config via environment variables:
    LOCAL_LLM_BASE_URL   default: http://localhost:11434     (Ollama root)
    LOCAL_EMBED_MODEL    default: nomic-embed-text
    LOCAL_GEN_MODEL      default: llama3.2:3b
    LOCAL_RAG_STORE      default: ~/.local-rag           (Chroma persist dir)
"""

# /// script
# dependencies = ["mcp[cli]", "httpx", "chromadb", "pypdf", "docling"]
# ///

import os
import glob
import hashlib
import pathlib

import httpx
import chromadb
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.environ.get("LOCAL_EMBED_MODEL", "nomic-embed-text")
GEN_MODEL = os.environ.get("LOCAL_GEN_MODEL", "llama3.2:3b")
STORE_DIR = os.path.expanduser(os.environ.get("LOCAL_RAG_STORE", "~/.local-rag"))

CHUNK_CHARS = 2000   # ~500 tokens
CHUNK_OVERLAP = 250  # ~64 tokens
TOP_K = 5
# File types docling handles best (rich layout, tables, structure).
DOCLING_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".md"}

mcp = FastMCP("local-rag")

_client = chromadb.PersistentClient(path=STORE_DIR)
_collection = _client.get_or_create_collection(
    name="corpus", metadata={"hnsw:space": "cosine"}
)


# ----------------------------- helpers --------------------------------------
def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Ollama's native embeddings endpoint."""
    out = []
    with httpx.Client(timeout=120.0) as client:
        for t in texts:
            r = client.post(
                f"{BASE_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": t},
            )
            r.raise_for_status()
            out.append(r.json()["embedding"])
    return out


def _read_text(path: str) -> str:
    """Extract text from a file.

    Preference order (all local / offline):
      1. docling  — rich structure-aware extraction (tables, layout, headings)
                    emitted as Markdown. Best quality for PDF/DOCX/PPTX/XLSX/HTML.
      2. pypdf    — fallback for PDFs if docling is unavailable or errors.
      3. UTF-8    — plain-text read for everything else.
    """
    p = pathlib.Path(path)
    suffix = p.suffix.lower()

    # 1) docling — structure-aware, exported as Markdown
    if suffix in DOCLING_EXTS:
        try:
            from docling.document_converter import DocumentConverter

            result = DocumentConverter().convert(str(p))
            md = result.document.export_to_markdown()
            if md and md.strip():
                return md
        except Exception:
            pass  # fall through to lighter parsers

    # 2) pypdf — fallback for PDFs
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            return ""

    # 3) plain-text read
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _split_size(text: str) -> list[str]:
    """Fixed-size character split with overlap (fallback for large sections)."""
    text = text.strip()
    if not text:
        return []
    out, start = [], 0
    while start < len(text):
        end = start + CHUNK_CHARS
        out.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return out


def _chunk(text: str) -> list[str]:
    """Structure-aware chunking.

    Docling exports Markdown, so we split on Markdown headings first — each
    section (with its heading kept as context) becomes a chunk. Any section
    still larger than CHUNK_CHARS is size-split with overlap. Falls back to a
    plain size-split when there are no headings (e.g. plain-text files).
    """
    import re

    text = (text or "").strip()
    if not text:
        return []

    lines = text.split("\n")
    heading_re = re.compile(r"^#{1,6}\s+\S")
    has_headings = any(heading_re.match(ln) for ln in lines)

    # No headings → plain size-split
    if not has_headings:
        return _split_size(text)

    # Group lines into heading-led sections
    sections, current = [], []
    for ln in lines:
        if heading_re.match(ln) and current:
            sections.append("\n".join(current).strip())
            current = [ln]
        else:
            current.append(ln)
    if current:
        sections.append("\n".join(current).strip())

    # Emit sections; size-split any that are too large
    chunks = []
    for sec in sections:
        if not sec:
            continue
        if len(sec) <= CHUNK_CHARS:
            chunks.append(sec)
        else:
            chunks.extend(_split_size(sec))
    return chunks


def _gen(prompt: str, system: str | None = None) -> str:
    """Generate a completion via Ollama chat endpoint (local generation)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    with httpx.Client(timeout=180.0) as client:
        r = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={"model": GEN_MODEL, "messages": messages, "temperature": 0.2},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


# ------------------------------- tools --------------------------------------
@mcp.tool()
async def local_rag_index(folder: str, extensions: str = ".txt,.md,.pdf") -> str:
    """Parse, chunk, embed, and store every matching file under a folder.

    Runs fully on-device. Re-indexing a file replaces its previous chunks.

    Args:
        folder: Absolute path to the corpus folder.
        extensions: Comma-separated file extensions to include.
    """
    exts = {e.strip().lower() for e in extensions.split(",")}
    folder = os.path.expanduser(folder)
    files = [
        f for f in glob.glob(os.path.join(folder, "**", "*"), recursive=True)
        if os.path.isfile(f) and pathlib.Path(f).suffix.lower() in exts
    ]
    if not files:
        return f"No files with {sorted(exts)} found under {folder}."

    total_chunks = 0
    for fpath in files:
        file_id = hashlib.md5(fpath.encode()).hexdigest()[:12]
        # drop old chunks for this file (idempotent re-index)
        _collection.delete(where={"file_id": file_id})

        chunks = _chunk(_read_text(fpath))
        if not chunks:
            continue
        embeddings = _embed(chunks)
        _collection.add(
            ids=[f"{file_id}:{i}" for i in range(len(chunks))],
            embeddings=embeddings,
            documents=chunks,
            metadatas=[{"file_id": file_id, "source": fpath, "chunk": i}
                       for i in range(len(chunks))],
        )
        total_chunks += len(chunks)

    return (f"Indexed {len(files)} file(s), {total_chunks} chunk(s) into "
            f"'{STORE_DIR}'. Collection now holds {_collection.count()} chunks.")


@mcp.tool()
async def local_rag_query(question: str, top_k: int = TOP_K) -> str:
    """Retrieve the most relevant chunks for a question (no generation).

    Args:
        question: The search question.
        top_k: Number of chunks to return.
    """
    if _collection.count() == 0:
        return "Index is empty — run local_rag_index first."
    q_emb = _embed([question])[0]
    res = _collection.query(query_embeddings=[q_emb], n_results=top_k)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    if not docs:
        return "No matches."
    lines = []
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        src = os.path.basename(meta.get("source", "?"))
        snippet = doc[:400].replace("\n", " ")
        lines.append(f"[{i}] ({src}, score={1 - dist:.3f})\n{snippet}")
    return "\n\n".join(lines)


@mcp.tool()
async def local_rag_answer(question: str, top_k: int = TOP_K) -> str:
    """Retrieve relevant chunks and generate a grounded answer — fully local.

    Args:
        question: The question to answer from the indexed corpus.
        top_k: Number of chunks to retrieve as context.
    """
    if _collection.count() == 0:
        return "Index is empty — run local_rag_index first."
    q_emb = _embed([question])[0]
    res = _collection.query(query_embeddings=[q_emb], n_results=top_k)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    if not docs:
        return "No relevant context found."

    context = "\n\n".join(
        f"[{i}] (source: {os.path.basename(m.get('source','?'))})\n{d}"
        for i, (d, m) in enumerate(zip(docs, metas), 1)
    )
    system = (
        "You answer strictly from the provided context. If the answer is not "
        "in the context, say so. Cite sources by their [n] markers."
    )
    prompt = f"Context:\n{context}\n\nQuestion: {question}\n\nGrounded answer:"
    answer = _gen(prompt, system=system)
    sources = ", ".join(sorted({os.path.basename(m.get("source", "?")) for m in metas}))
    return f"{answer}\n\n---\nSources: {sources}"




@mcp.tool()
async def local_rag_index_file(file_path: str) -> str:
    """Parse, chunk, embed, and store a single file.

    Use this when the user uploads or points at one specific document
    rather than an entire folder.

    Args:
        file_path: Absolute path to the file to index.
    """
    file_path = os.path.expanduser(file_path)
    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    file_id = hashlib.md5(file_path.encode()).hexdigest()[:12]
    _collection.delete(where={"file_id": file_id})

    text = _read_text(file_path)
    chunks = _chunk(text)
    if not chunks:
        return f"No extractable text from {file_path}."

    embeddings = _embed(chunks)
    _collection.add(
        ids=[f"{file_id}:{i}" for i in range(len(chunks))],
        embeddings=embeddings,
        documents=chunks,
        metadatas=[{"file_id": file_id, "source": file_path, "chunk": i}
                   for i in range(len(chunks))],
    )
    return (f"Indexed '{os.path.basename(file_path)}' → {len(chunks)} chunk(s). "
            f"Collection now holds {_collection.count()} total chunks.")

if __name__ == "__main__":
    mcp.run()  # stdio transport
