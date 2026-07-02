# Requirements Document

## Introduction

The SRE Business Knowledge Assistant extends the existing Codebase Business Logic Extractor to bridge the gap between extracted code knowledge and SRE ticket resolution. Today, the system parses Python source code, builds a call graph, classifies functions as business or technical, extracts BDD-style business rules, maps business flows, and exposes everything via an MCP server and HTML dashboard. However, the extracted knowledge is code-centric — organized by functions, call graphs, and classifications — not by the business questions SREs actually receive.

SREs in the HCM (Human Capital Management) domain receive tickets phrased in business language: "How do I submit my time card?", "Why was my benefits enrollment rejected?", "What are the payroll cutoff dates?" The assistant must translate these natural-language business questions into relevant code knowledge and present actionable answers without requiring the SRE to read source code.

This feature adds a semantic query layer, an SRE-oriented knowledge index, and integration points that transform the existing code-centric knowledge graph into an SRE-friendly business knowledge assistant.

## Glossary

- **Knowledge_Index**: A searchable index built from extracted business rules, flows, domain summaries, and function metadata, organized by business concepts rather than code structure
- **Query_Engine**: The component that receives natural-language SRE questions, performs semantic matching against the Knowledge_Index, and assembles context for answer generation
- **Business_Glossary**: A mapping of business terms, synonyms, and acronyms (e.g., "time card" → "timesheet submission", "PTO" → "paid time off" → leave management) used to bridge ticket language and code terminology
- **Answer_Generator**: The LLM-powered component that synthesizes retrieved knowledge into SRE-friendly answers with step-by-step guidance, relevant business rules, and affected system components
- **Knowledge_Article**: A structured document combining business rules, flow steps, domain context, and troubleshooting guidance for a specific business process, stored in the Knowledge_Index
- **Freshness_Tracker**: The component that tracks when the knowledge base was last built, which source files have changed since, and flags stale knowledge
- **SRE_Context**: Metadata attached to each answer indicating confidence level, knowledge freshness, related business rules, and affected code paths — enabling the SRE to assess answer reliability
- **Runbook_Fragment**: A concise, actionable set of steps derived from business flows and rules that an SRE can follow to investigate or resolve a specific type of ticket
- **Embedding_Store**: A vector database or index that stores semantic embeddings of business rules, flow descriptions, and knowledge articles for similarity-based retrieval
- **Pipeline**: The existing extraction pipeline (parse → graph → communities → classify → rules → flows) that produces the raw knowledge from source code

## Requirements

### Requirement 1: Semantic Knowledge Indexing

**User Story:** As an SRE, I want the system to organize extracted business knowledge by business concepts and processes, so that I can find relevant information using business terminology rather than code terminology.

#### Acceptance Criteria

1. WHEN the Pipeline completes extraction, THE Knowledge_Index SHALL generate semantic embeddings for each business rule, business flow, and domain summary using the configured LLM embedding model
2. WHEN the Pipeline completes extraction, THE Knowledge_Index SHALL create Knowledge_Articles that group related business rules, flow steps, and function summaries by business process (e.g., "Timesheet Submission", "Benefits Enrollment", "Payroll Processing")
3. THE Knowledge_Index SHALL store each Knowledge_Article with a title, a plain-language description, associated business rules, associated flow steps, the originating domain, and a list of source function qualified names
4. WHEN a Knowledge_Article is created, THE Knowledge_Index SHALL generate and store a semantic embedding vector for the article title and description combined
5. IF the embedding model is unavailable during indexing, THEN THE Knowledge_Index SHALL log the failure, skip embedding generation for the affected articles, and mark those articles as "embedding_pending"

### Requirement 2: Business Glossary and Term Mapping

**User Story:** As an SRE, I want the system to understand business terminology, synonyms, and HCM-specific acronyms, so that my ticket language maps correctly to the extracted code knowledge.

#### Acceptance Criteria

1. THE Business_Glossary SHALL maintain a configurable mapping of business terms to canonical terms, including synonyms and acronyms (e.g., "time card" → "timesheet", "PTO" → "paid_time_off", "W-2" → "tax_form_w2")
2. WHEN the Pipeline completes extraction, THE Business_Glossary SHALL auto-generate an initial term mapping by extracting business terms from function names, docstrings, business summaries, and BDD rule clauses using the configured LLM
3. THE Business_Glossary SHALL store each term entry with the canonical term, a list of synonyms, the associated domain, and a definition
4. WHEN an SRE submits a query containing a term that matches a Business_Glossary synonym, THE Query_Engine SHALL expand the query to include the canonical term and all related synonyms before performing retrieval
5. THE Business_Glossary SHALL support manual additions and overrides via a YAML configuration file located at a configurable path

### Requirement 3: Natural Language Query Resolution

**User Story:** As an SRE, I want to ask business questions in plain language and receive actionable answers grounded in the extracted code knowledge, so that I can resolve tickets without reading source code.

#### Acceptance Criteria

1. WHEN an SRE submits a natural-language question, THE Query_Engine SHALL perform semantic similarity search against the Embedding_Store to retrieve the top-K most relevant Knowledge_Articles, where K is configurable with a default of 5
2. WHEN an SRE submits a natural-language question, THE Query_Engine SHALL also perform keyword-based search against the existing SQLite FTS5 index on business rules, flow descriptions, and function summaries
3. THE Query_Engine SHALL merge results from semantic search and keyword search, deduplicate by Knowledge_Article identifier, and rank by a weighted combination of semantic similarity score and keyword relevance score
4. WHEN the Query_Engine has assembled retrieved context, THE Answer_Generator SHALL produce an answer that includes: a plain-language explanation of the business process, the relevant business rules in Given/When/Then format, the step-by-step flow if applicable, and a list of affected system components (domains, key functions)
5. THE Answer_Generator SHALL attach SRE_Context to each answer containing: a confidence score between 0.0 and 1.0, the knowledge freshness timestamp, the number of source articles used, and references to the originating business rules and flows
6. IF the Query_Engine retrieves zero relevant articles above the configured similarity threshold, THEN THE Answer_Generator SHALL respond with a message indicating insufficient knowledge and suggest related domains or terms the SRE might try

### Requirement 4: Runbook Generation

**User Story:** As an SRE, I want the system to generate actionable runbook fragments from business flows and rules, so that I have step-by-step guidance for investigating and resolving common ticket types.

#### Acceptance Criteria

1. WHEN the Pipeline completes extraction, THE Answer_Generator SHALL produce a Runbook_Fragment for each identified business flow, containing: a trigger description (what ticket or question activates this runbook), numbered investigation steps derived from the flow steps, relevant business rules that apply at each step, and the system components involved
2. THE Runbook_Fragment SHALL be stored in the Knowledge_Index and be retrievable by semantic search alongside Knowledge_Articles
3. WHEN an SRE query matches a Runbook_Fragment with a similarity score above the configured threshold, THE Answer_Generator SHALL include the runbook steps in the answer, formatted as a numbered checklist
4. IF a business flow spans multiple domains, THEN THE Answer_Generator SHALL generate a cross-domain Runbook_Fragment that references the relevant steps from each domain in execution order

### Requirement 5: Knowledge Freshness Tracking

**User Story:** As an SRE, I want to know how current the knowledge base is and whether any source code has changed since the last extraction, so that I can assess the reliability of the answers I receive.

#### Acceptance Criteria

1. WHEN the Pipeline completes extraction, THE Freshness_Tracker SHALL record the extraction timestamp, the list of source files processed, and a content hash for each source file
2. WHEN an SRE queries the system, THE Freshness_Tracker SHALL compare the current source file hashes against the stored hashes and report the number of files that have changed since the last extraction
3. THE SRE_Context attached to each answer SHALL include the extraction timestamp and a staleness indicator: "fresh" when zero source files have changed, "partially_stale" when fewer than 20 percent of relevant source files have changed, and "stale" when 20 percent or more of relevant source files have changed
4. IF the staleness indicator is "stale", THEN THE Answer_Generator SHALL prepend a warning to the answer stating that the knowledge base may be outdated and recommending a re-extraction
5. WHEN the Freshness_Tracker detects that a source file contributing to a specific Knowledge_Article has changed, THE Freshness_Tracker SHALL mark that Knowledge_Article as "needs_refresh"

### Requirement 6: SRE-Oriented MCP Server Tools

**User Story:** As an SRE using an AI assistant (Claude Desktop, Cursor), I want dedicated MCP tools that answer business questions, retrieve runbooks, and check knowledge freshness, so that I can get SRE-specific assistance through my existing AI workflow.

#### Acceptance Criteria

1. THE MCP_Server SHALL expose an "ask_business_question" tool that accepts a natural-language question string and returns the Answer_Generator response with SRE_Context metadata
2. THE MCP_Server SHALL expose a "get_runbook" tool that accepts a business process name or ticket description and returns the matching Runbook_Fragment with numbered steps
3. THE MCP_Server SHALL expose a "check_knowledge_freshness" tool that accepts no required parameters and returns the extraction timestamp, total source files, changed files count, and overall staleness indicator
4. THE MCP_Server SHALL expose a "list_business_processes" tool that accepts an optional domain filter and returns a list of all Knowledge_Articles with their titles, domains, and associated business rule counts
5. THE MCP_Server SHALL expose a "get_business_glossary" tool that accepts an optional term string and returns matching Business_Glossary entries with canonical terms, synonyms, and definitions
6. WHEN any MCP tool encounters a database connection error, THE MCP_Server SHALL return a structured error response containing the error type, a human-readable message, and a suggested remediation action

### Requirement 7: SRE Dashboard View

**User Story:** As an SRE, I want a dedicated dashboard view that presents business knowledge organized by HCM processes and ticket categories, so that I can browse available knowledge without formulating specific queries.

#### Acceptance Criteria

1. THE Dashboard SHALL include an "SRE Knowledge Base" tab that displays Knowledge_Articles grouped by business domain, with each article showing its title, description, associated rule count, and freshness status
2. THE Dashboard SHALL include a search bar on the SRE Knowledge Base tab that performs real-time filtering of Knowledge_Articles by title, description, and associated business terms
3. WHEN an SRE clicks on a Knowledge_Article in the dashboard, THE Dashboard SHALL expand the article to show: the full plain-language description, all associated business rules in Given/When/Then format, the business flow steps if applicable, and the Runbook_Fragment if one exists
4. THE Dashboard SHALL display a knowledge freshness banner at the top of the SRE Knowledge Base tab showing the last extraction timestamp and the overall staleness indicator with color coding: green for "fresh", yellow for "partially_stale", and red for "stale"

### Requirement 8: Configuration and Extensibility

**User Story:** As a platform engineer, I want to configure the SRE assistant's behavior through the existing config.yaml, so that I can tune retrieval parameters, embedding models, and glossary settings without code changes.

#### Acceptance Criteria

1. THE config.yaml SHALL support an "sre_assistant" section with configurable fields for: embedding model identifier, similarity threshold (default 0.3), top-K retrieval count (default 5), glossary file path, and staleness percentage threshold (default 20)
2. WHEN the "sre_assistant" section is absent from config.yaml, THE system SHALL use the default values for all configurable fields
3. THE Pipeline SHALL include the Knowledge_Index build and Runbook_Fragment generation as additional stages that run after the existing flow mapping stage
4. IF the "sre_assistant.enabled" field is set to false in config.yaml, THEN THE Pipeline SHALL skip the Knowledge_Index build, embedding generation, and runbook generation stages
