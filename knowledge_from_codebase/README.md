# 🧠 Codebase Business Logic Extractor

**Extract business rules, processes, and domain knowledge from any codebase using AI agents.**

Turn thousands of lines of code into structured business knowledge — BDD rules, domain maps, call-flow traces, and an interactive dashboard — powered by Amazon Bedrock and graph analysis.

> Based on the [Codebase-Memory](https://arxiv.org/abs/2603.27277) approach (arXiv:2603.27277).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Repository Source Code                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   1. AST Parser      │  Parse functions, classes,
                    │   (Tree-sitter)      │  methods, endpoints
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   2. Graph Builder   │  Build call graph,
                    │   (NetworkX+SQLite)  │  resolve references
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   3. Community       │  Louvain clustering →
                    │   Detection          │  discover business domains
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   4. LLM Classifier  │  Business vs Technical
                    │   (Amazon Bedrock)   │  via Claude
                    └──────────┬──────────┘
                               │
               ┌───────────────┼───────────────┐
               │               │               │
    ┌──────────▼───┐  ┌───────▼───────┐  ┌───▼──────────┐
    │ 5a. BDD Rule │  │ 5b. Flow      │  │ 5c. Domain   │
    │ Extraction   │  │ Mapping       │  │ Summaries    │
    └──────────┬───┘  └───────┬───────┘  └───┬──────────┘
               │              │              │
               └──────────────┼──────────────┘
                              │
               ┌──────────────▼──────────────┐
               │   6. Output Generation       │
               │   ┌────────┬────────┬──────┐ │
               │   │Dashboard│  JSON  │ BDD  │ │
               │   │  HTML   │ Export │ Specs│ │
               │   └────────┴────────┴──────┘ │
               └──────────────┬──────────────┘
                              │
               ┌──────────────▼──────────────┐
               │   🌐 MCP Server (FastMCP)    │
               │   Query graph from Claude    │
               │   Desktop, Cursor, etc.      │
               └─────────────────────────────┘
```

---

## Quick Start

### 1. Install

```bash
cd 4.CodebaseBusinessExtractor
pip install -r requirements.txt
```

### 2. Configure AWS Credentials

The tool uses **Amazon Bedrock** (Claude) for classification and rule extraction.

```bash
export AWS_PROFILE=your-profile
# or
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

Make sure you have model access enabled for `anthropic.claude-sonnet-4-20250514` in the [Bedrock console](https://console.aws.amazon.com/bedrock/).

### 3. Run

```bash
# Extract business logic from a repository
python main.py extract /path/to/your/repo

# Open the generated dashboard
open output/dashboard.html
```

---

## Usage

### `extract` — Full Pipeline

```bash
# Basic extraction
python main.py extract ./my-project

# Custom config and output directory
python main.py extract ./my-project --config my-config.yaml --output-dir results/

# Structural analysis only (no LLM calls, no AWS costs)
python main.py extract ./my-project --skip-classification

# Verbose mode
python main.py extract ./my-project -v
```

### `query` — Ask Questions

```bash
# Natural language Q&A about the codebase
python main.py query "How does the billing module calculate discounts?"
python main.py query "What happens when a user registers?"
python main.py query "Which functions handle authentication?"
```

### `serve` — MCP Server

```bash
# Start the MCP server (stdio transport)
python main.py serve

# With custom config
python main.py serve --config my-config.yaml
```

### `dashboard` — Regenerate Dashboard

```bash
# Rebuild the HTML dashboard from existing graph data
python main.py dashboard
```

### `stats` — Graph Statistics

```bash
# Print a summary of the knowledge graph
python main.py stats
```

---

## How It Works

### Stage 1: AST Parsing

The parser uses language-specific AST analysis to extract every function, method, class, and endpoint from the codebase. For each, it captures:

- Name, file path, line numbers
- Docstring and inline comments
- Parameters and return types
- Decorators (to identify API endpoints, event handlers, etc.)

### Stage 2: Graph Construction

A directed call graph is built where:
- **Nodes** = functions, methods, classes
- **Edges** = "calls" relationships (resolved via static analysis)

The graph is persisted to SQLite for fast querying.

### Stage 3: Community Detection

The [Louvain algorithm](https://en.wikipedia.org/wiki/Louvain_method) partitions the call graph into clusters of tightly-connected functions. These clusters often correspond to **business domains** (billing, auth, notifications, etc.). Each community is auto-labelled using the most common file paths and function names.

### Stage 4: LLM Classification

Each function is sent to **Amazon Bedrock (Claude)** in batches with its source code, docstring, and graph context. The LLM classifies it as:

| Classification | Description |
|---|---|
| `business` | Implements business rules, domain logic, or user-facing behavior |
| `technical` | Infrastructure, utilities, database access, logging |
| `glue` | Routing, serialization, orchestration between business and technical |

### Stage 5: Knowledge Extraction

Three parallel extraction tasks run on business-classified functions:

- **BDD Rules**: Given/When/Then business rules in structured format
- **Flow Mapping**: End-to-end business flows traced from entry points (API handlers, event handlers) through the call chain
- **Domain Summaries**: High-level descriptions of each discovered business domain

---

## Configuration Reference

```yaml
# config.yaml — full reference

llm:
  model_id: "anthropic.claude-sonnet-4-20250514"  # Bedrock model ID
  region: "us-east-1"                         # AWS region
  max_tokens: 4096                            # Max response tokens
  temperature: 0.0                            # LLM temperature (0 = deterministic)

graph:
  db_path: "output/code_graph.db"             # SQLite database path

parser:
  languages: ["python"]                       # Languages to parse
  exclude_patterns:                           # Directory patterns to skip
    - "__pycache__"
    - "node_modules"
    - ".git"
    - "venv"
    - ".env"
  exclude_files:                              # Files to skip
    - "__init__.py"
    - "setup.py"
    - "conftest.py"
  min_function_lines: 3                       # Skip functions shorter than this

classifier:
  batch_size: 5                               # Functions per LLM batch call
  skip_technical: false                       # Skip obviously technical functions

output:
  dir: "output"                               # Output directory
  generate_dashboard: true                    # Generate HTML dashboard
  generate_bdd: true                          # Generate BDD specs
  export_json: true                           # Export data.json
```

---

## Sample Output

After running the extractor, you'll find:

```
output/
├── code_graph.db           # SQLite knowledge graph
├── data.json               # Full export (functions, rules, flows, domains)
├── dashboard.html          # Interactive HTML dashboard
└── templates/
    └── dashboard_template.html
```

### Dashboard

The interactive dashboard includes 5 tabs:

- **Overview** — Stats cards, domain distribution, classification breakdown
- **Domains** — Expandable domain cards with function listings
- **Business Rules** — BDD-formatted Given/When/Then rule cards
- **Business Flows** — End-to-end flow visualizations with numbered steps
- **All Functions** — Searchable/filterable table of every function

---

## MCP Server

The MCP server exposes the knowledge graph to AI assistants via the [Model Context Protocol](https://modelcontextprotocol.io/).

### Tools Available

| Tool | Description |
|---|---|
| `search_functions` | Search by name, domain, kind, classification |
| `get_callers` | Who calls this function? |
| `get_callees` | What does this function call? |
| `get_business_rules` | BDD rules, optionally filtered by domain |
| `trace_business_flow` | Trace from entry point through call chain |
| `impact_analysis` | Blast radius if a function changes |
| `get_domain_summary` | Overview of one or all business domains |
| `ask_about_codebase` | Natural language Q&A (Bedrock-powered) |

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "codebase-extractor": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/4.CodebaseBusinessExtractor",
      "env": {
        "CBE_DB_PATH": "/path/to/output/code_graph.db"
      }
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "codebase-extractor": {
      "command": "python",
      "args": ["main.py", "serve"],
      "cwd": "/path/to/4.CodebaseBusinessExtractor"
    }
  }
}
```

### VS Code (Copilot)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "codebase-extractor": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/4.CodebaseBusinessExtractor"
    }
  }
}
```

---

## Limitations & Future Work

### Current Limitations

- **Static analysis only** — no runtime / dynamic dispatch resolution
- **Python-first** — JavaScript, Java, and TypeScript parsers are planned
- **Single-repo** — does not trace across microservice boundaries
- **LLM cost** — classification + extraction calls Bedrock; large repos can incur costs

### Planned Improvements

- [ ] Multi-language support (JS/TS, Java, Go)
- [ ] Incremental re-extraction (only re-process changed files)
- [ ] Cross-repo tracing for microservice architectures
- [ ] Test coverage mapping (link business rules to test cases)
- [ ] Embeddings-based semantic search over the knowledge graph
- [ ] GitHub Actions integration for CI/CD pipelines
- [ ] Interactive graph visualization (D3.js force-directed layout)

---

## References

- **Codebase-Memory**: *Exploring Codebase-level Memory in LLM Agents* — [arXiv:2603.27277](https://arxiv.org/abs/2603.27277)
- **Model Context Protocol**: [modelcontextprotocol.io](https://modelcontextprotocol.io/)
- **Amazon Bedrock**: [aws.amazon.com/bedrock](https://aws.amazon.com/bedrock/)
- **Louvain Community Detection**: Blondel et al., 2008 — *Fast unfolding of communities in large networks*

---

## License

MIT
