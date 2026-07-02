"""
prompts — LLM prompt templates for business classification, rule extraction,
flow mapping, and codebase summarisation.

Every constant is a Python format-string ready for ``.format(**kwargs)`` or
``str.format_map()``.  Placeholders use ``{name}`` syntax.
"""

# ---------------------------------------------------------------------------
# 1. CLASSIFICATION_PROMPT
# ---------------------------------------------------------------------------
CLASSIFICATION_PROMPT: str = """\
You are a senior software architect specialising in business-domain analysis.
Your task is to classify a source-code function into exactly ONE of the
following categories:

| Category                   | Description |
|----------------------------|-------------|
| BUSINESS_RULE              | Encodes a concrete business rule, policy, validation, or constraint. |
| BUSINESS_PROCESS           | Orchestrates a multi-step business workflow or use-case. |
| DATA_ACCESS                | Reads from or writes to a database, file, cache, or external store. |
| TECHNICAL_INFRASTRUCTURE   | Framework plumbing, logging, config, error handling, utilities. |
| INTEGRATION                | Calls or is called by an external system, API, or message queue. |

### Context
- **Function name:** {function_name}
- **File path:** {file_path}
- **Kind:** {kind}
- **Language:** {language}
- **Callers (who calls this):** {callers}
- **Callees (what this calls):** {callees}
- **Community / module cluster ID:** {community_id}
- **Docstring / comments:**
```
{docstring}
```

### Source Code
```{language}
{source_code}
```

### Instructions
1. Read the source code carefully.
2. Consider the function's name, docstring, callers, callees, and community
   context to understand its purpose.
3. Classify the function into exactly ONE category.
4. Provide a confidence score between 0.0 and 1.0.
5. Write a concise business-level summary (1-2 sentences).
6. Identify the business domain (e.g. "Payments", "User Management",
   "Inventory", "Authentication", "Reporting", etc.).
7. Explain your reasoning in 1-3 sentences.

Return your answer as a **single JSON object** with these keys — no other text:
```json
{{
  "classification": "<BUSINESS_RULE|BUSINESS_PROCESS|DATA_ACCESS|TECHNICAL_INFRASTRUCTURE|INTEGRATION>",
  "confidence": <float 0.0-1.0>,
  "business_summary": "<1-2 sentence summary>",
  "business_domain": "<domain name>",
  "reasoning": "<1-3 sentence explanation>"
}}
```
"""

# ---------------------------------------------------------------------------
# 2. RULE_EXTRACTION_PROMPT
# ---------------------------------------------------------------------------
RULE_EXTRACTION_PROMPT: str = """\
You are a business analyst extracting formal business rules from source code.

Your output must be in **BDD (Behaviour-Driven Development) format** using
Given / When / Then clauses.

### Function Metadata
- **Function name:** {function_name}
- **Business domain:** {business_domain}
- **Classification:** {classification}
- **Business summary:** {summary}

### Source Code
```
{source_code}
```

### Related Functions (callers / callees / community siblings)
{related_functions}

### Instructions
1. Identify every distinct business rule, validation, constraint, or policy
   encoded in this function.
2. For each rule, express it in BDD format:
   - **Given** — the precondition or initial context.
   - **When** — the triggering action or event.
   - **Then** — the expected outcome or assertion.
3. Reference the exact source lines where each rule is implemented.
4. Assess the confidence (0.0-1.0) that this is a genuine business rule
   (not just a technical guard).
5. Describe the business impact if the rule were removed or inverted.

Return a **JSON array** of rule objects — no other text:
```json
[
  {{
    "rule_name": "<short descriptive name>",
    "given": "<precondition>",
    "when": "<trigger>",
    "then": "<outcome>",
    "source_lines": [<start_line>, <end_line>],
    "source_snippet": "<relevant code snippet>",
    "confidence": <float 0.0-1.0>,
    "business_impact": "<what happens if this rule is removed>"
  }}
]
```

If no business rules are found, return an empty array: `[]`
"""

# ---------------------------------------------------------------------------
# 3. FLOW_MAPPING_PROMPT
# ---------------------------------------------------------------------------
FLOW_MAPPING_PROMPT: str = """\
You are a business process architect.  Given a set of classified functions
within the **{domain_name}** domain, identify end-to-end business flows.

A *business flow* is a sequence of function calls that together implement a
recognisable business process (e.g. "Place Order", "Onboard Customer",
"Process Refund").

### Classified Functions
{functions_with_classifications}

### Instructions
1. Group the functions into coherent business flows.
2. Order the steps logically (entry point → processing → persistence / output).
3. Each flow must reference only functions from the list above.
4. A function may appear in more than one flow.
5. Write a concise, business-level description for each flow.

Return a **JSON array** of flow objects — no other text:
```json
[
  {{
    "flow_name": "<short business process name>",
    "description": "<1-2 sentence business-level description>",
    "steps": [
      "<Step 1 description>",
      "<Step 2 description>"
    ],
    "functions_involved": [
      "<function_name_1>",
      "<function_name_2>"
    ]
  }}
]
```

If no coherent flows can be identified, return an empty array: `[]`
"""

# ---------------------------------------------------------------------------
# 4. CODEBASE_SUMMARY_PROMPT
# ---------------------------------------------------------------------------
CODEBASE_SUMMARY_PROMPT: str = """\
You are a technical writer producing an executive-level business summary of a
software codebase.

### Codebase Statistics
- **Business domains discovered:** {domains}
- **Total functions analysed:** {function_count}
- **Business rules extracted:** {rule_count}

### Instructions
Write 3-5 paragraphs aimed at a non-technical stakeholder.  Cover:
1. **Overview** — What this codebase does at a high level.
2. **Business Domains** — Summarise each domain and its purpose.
3. **Key Business Rules** — Highlight the most critical rules or policies
   encoded in the software.
4. **Integration Points** — Note external systems or services the codebase
   interacts with.
5. **Observations** — Any architectural patterns, risks, or opportunities
   worth flagging.

Return only the plain-text summary paragraphs — no JSON, no markdown headings.
"""
