# AI Agent Blueprints

A collection of production-ready AI agent implementations powered by Amazon Bedrock (Claude) for enterprise automation, knowledge extraction, and intelligent systems.

## 📦 Projects

### 1. [BluePrism2PlayWright](./BluePrism2PlayWright/)
**Automated RPA Migration with Self-Healing AI**

Convert Blue Prism XML processes to Playwright automation scripts with AI-powered repair capabilities.

- **Parser**: Extracts Blue Prism processes into intermediate JSON representation
- **Compiler**: Generates executable Playwright scripts with proper error handling
- **Repair Agent**: Self-healing AI that fixes broken selectors using Amazon Bedrock
- **Runner**: Executes workflows with screenshots and automatic repair attempts

**Key Features:**
- XML → JSON → Playwright pipeline
- AI-powered selector repair using page context
- Screenshot capture at each step
- Configurable repair strategies

**Tech Stack:** Python, Playwright, Amazon Bedrock, Blue Prism XML

---

### 2. [agent_ready_enterprise](./agent_ready_enterprise/)
**Enterprise API Knowledge Graph & Semantic Planning**

Build intelligent knowledge graphs from OpenAPI specifications and enable natural language API interactions.

- **Graph Builder**: Constructs semantic knowledge graphs from OpenAPI specs
- **Hybrid Retrieval**: Combines vector search + graph traversal for context retrieval
- **Planning Engine**: LLM-powered multi-step plan generation and execution
- **Mock Server**: Test plans without hitting production APIs
- **Web UI**: Next.js frontend + Streamlit dashboard

**Key Features:**
- OpenAPI → Knowledge Graph conversion
- Semantic communities and capability clustering
- Plan caching and repair
- Evidence-based answer synthesis
- REST API + MCP server integration

**Tech Stack:** Python, NetworkX, FAISS, FastAPI, Streamlit, Next.js, Amazon Bedrock

---

### 3. [knowledge_from_codebase](./knowledge_from_codebase/)
**Business Logic Extractor from Source Code**

Extract business rules, domain knowledge, and process flows from any codebase using AST parsing and LLM classification.

- **AST Parser**: Tree-sitter based code structure extraction
- **Graph Analysis**: Call graph construction and community detection (Louvain)
- **LLM Classifier**: Business vs Technical code classification
- **Rule Extractor**: BDD-style Given/When/Then business rules
- **Flow Mapper**: End-to-end business flow visualization
- **MCP Server**: Claude Desktop integration for codebase Q&A

**Key Features:**
- Parse Python codebases (JS/TS/Java planned)
- Discover business domains via graph clustering
- Extract business rules in BDD format
- Trace business flows through call chains
- Interactive HTML dashboard
- Natural language codebase queries

**Tech Stack:** Python, Tree-sitter, NetworkX, SQLite, Amazon Bedrock, FastMCP

---

## 🚀 Getting Started

Each project includes:
- ✅ Comprehensive README with setup instructions
- ✅ `requirements.txt` for Python dependencies
- ✅ Configuration templates
- ✅ Example data and fixtures
- ✅ Security best practices

### Prerequisites

1. **Python 3.8+**
2. **AWS Account** with Bedrock access
3. **AWS Credentials** configured:
   ```bash
   export AWS_PROFILE=your-profile
   # or
   export AWS_ACCESS_KEY_ID=...
   export AWS_SECRET_ACCESS_KEY=...
   export AWS_DEFAULT_REGION=us-east-1
   ```
4. **Bedrock Model Access**: Enable Claude Sonnet 4 in the [Bedrock console](https://console.aws.amazon.com/bedrock/)

### Quick Install

```bash
# Clone the repository
git clone git@github.com:frugalagents/blueprints.git
cd blueprints

# Choose a project and install dependencies
cd agent_ready_enterprise  # or BluePrism2PlayWright or knowledge_from_codebase
pip install -r requirements.txt

# Configure and run (see project README for details)
```

---

## 🔒 Security

All projects follow security best practices:
- ✅ No hardcoded credentials
- ✅ Environment variable configuration
- ✅ Comprehensive `.gitignore` patterns
- ✅ Secrets excluded from version control
- ✅ AWS IAM role support

See [SECURITY_CHECK.md](./SECURITY_CHECK.md) for the security audit report.

---

## 📊 Use Cases

### BluePrism2PlayWright
- Migrate legacy RPA processes to modern frameworks
- Modernize Blue Prism workflows
- Create self-healing automation scripts

### agent_ready_enterprise
- Build AI assistants that understand your APIs
- Enable natural language API interactions
- Create semantic knowledge bases from OpenAPI specs
- Intelligent API orchestration and planning

### knowledge_from_codebase
- Extract business knowledge from legacy codebases
- Document undocumented systems
- Discover domain boundaries and business rules
- Create developer onboarding documentation
- Q&A over codebases via Claude Desktop

---

## 🛠️ Architecture Patterns

These blueprints demonstrate:
- **Agentic Planning**: Multi-step decomposition and execution
- **Self-Healing Systems**: AI-powered error detection and repair
- **Knowledge Graphs**: Semantic understanding of structured data
- **Hybrid Retrieval**: Vector + graph + rule-based search
- **LLM Orchestration**: Prompt engineering and structured output
- **MCP Integration**: Model Context Protocol for AI assistants

---

## 📚 Documentation

Each project includes:
- Architecture diagrams
- API documentation
- Configuration references
- Example workflows
- Troubleshooting guides

---

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Follow existing code style
4. Add tests for new features
5. Update documentation
6. Submit a pull request

---

## 📄 License

MIT

---

## 🙋 Support

For issues, questions, or feature requests:
- Open a GitHub issue
- Check project-specific READMEs
- Review SECURITY_CHECK.md for security guidelines

---

## 🏗️ Built With

- [Amazon Bedrock](https://aws.amazon.com/bedrock/) - Foundation models (Claude)
- [Playwright](https://playwright.dev/) - Browser automation
- [NetworkX](https://networkx.org/) - Graph analysis
- [FAISS](https://github.com/facebookresearch/faiss) - Vector search
- [FastAPI](https://fastapi.tiangolo.com/) - API framework
- [Streamlit](https://streamlit.io/) - Data apps
- [Next.js](https://nextjs.org/) - React framework
- [FastMCP](https://github.com/jlowin/fastmcp) - Model Context Protocol

---

**Repository:** https://github.com/frugalagents/blueprints

**Last Updated:** 2026-07-02
