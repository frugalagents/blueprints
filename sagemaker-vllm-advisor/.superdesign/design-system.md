# Model Deployment Advisor — Design System

## Product context

Model Deployment Advisor is a web-based decision workspace for AWS solution architects, ML platform engineers, and customer engineering teams deploying Hugging Face models to Amazon SageMaker with the vLLM model server.

The product must not present an LLM guess as an infrastructure recommendation. It separates:

1. customer-provided workload assumptions,
2. deterministic model-memory and traffic calculations,
3. candidate SageMaker configurations,
4. benchmark evidence,
5. the agent's recommendation and explanation.

The initial product is a single guided workspace. It should feel like an architecture review being completed with a knowledgeable specialist rather than a generic chatbot or cloud dashboard.

## Core user journey

1. **Describe the model**
   - Hugging Face model ID
   - architecture and parameter count
   - precision or quantization
   - model modality
   - model license and remote-code warning

2. **Describe the workload**
   - P50 and P95 input tokens
   - P50 and P95 output tokens
   - peak requests per second
   - concurrency
   - target P95 time-to-first-token
   - target inter-token latency
   - AWS Region, availability target, and cost ceiling

3. **Review sizing analysis**
   - model weight memory
   - estimated runtime overhead
   - KV-cache budget
   - context and concurrency assumptions
   - explicit confidence and unresolved questions

4. **Compare deployment candidates**
   - instance type and GPU topology
   - model precision
   - tensor parallel size
   - proposed vLLM parameters
   - estimated replica range
   - fit status, risks, and rationale

5. **Run or plan benchmarks**
   - production-shaped traffic profile
   - metrics and success gates
   - candidate matrix
   - evidence status: estimated, simulated, or measured

6. **Receive recommendation**
   - recommended baseline
   - lower-cost alternative
   - lower-latency alternative
   - assumptions, evidence, risks, and next action

## Information architecture

### Persistent top bar

- Product name: `Model Deployment Advisor`
- Status badge: `Sizing draft`, `Ready to benchmark`, or `Evidence complete`
- Compact actions: `Save assessment`, `Export`, `New assessment`

### Left rail: assessment progress

A narrow vertical sequence with six numbered stages:

1. Model
2. Workload
3. Constraints
4. Sizing
5. Benchmark
6. Recommendation

Each stage shows incomplete, active, or complete state. The rail also displays the current evidence level.

### Main work area

The active stage occupies the center. For the initial screen, show a guided Model and Workload intake form with realistic values already populated for an example open model. The form should be compact, grouped, and readable—not a long undifferentiated questionnaire.

### Right evidence panel

A fixed-width contextual panel that updates as inputs change:

- model summary,
- memory equation,
- key assumptions,
- missing information,
- current feasibility verdict.

It must clearly distinguish `Customer input`, `Calculated`, and `Needs benchmark`.

### Recommendation workspace

After inputs are complete, the primary canvas becomes a comparison table/cards for three candidates:

- Cost baseline
- Recommended balance
- Latency optimized

The recommended candidate is visually marked but must show why it won. Include a compact architecture strip:

`Client → SageMaker endpoint → vLLM replica(s) → GPU(s) → Hugging Face model`

## Key interaction requirements

- Progressive disclosure: ask only the fields needed for the current decision.
- Inline help explains technical terms in one sentence.
- Inputs update estimates immediately.
- Every recommendation displays its evidence level.
- An expandable “Show calculation” area exposes formulas and assumptions.
- Risk warnings are specific and actionable.
- The user can override any inferred value.
- Primary action labels describe the outcome: `Calculate candidate configurations`, `Create benchmark plan`, `Select recommended baseline`.
- Avoid a free-form chat-first layout. A small “Ask the advisor” drawer may supplement the governed workflow.

## Visual direction

Style source: Mosaic Grid Architecture, adapted from a marketing aesthetic into a practical technical workspace.

The UI should feel precise, calm, architectural, and trustworthy. Use flat surfaces, strong alignment, hairline separators, and generous whitespace. Avoid the visual noise of a typical cloud management console.

### Colors

- Paper background: `#F7F7F5`
- Main surface: `#FFFFFF`
- Forest primary: `#1A3C2B`
- Ink: `#20231F`
- Secondary text: `#62675F`
- Hairline grid: `rgba(58, 58, 56, 0.18)`
- Mint success: `#9EFFBF`
- Gold warning: `#F4D35E`
- Coral risk: `#FF8C69`
- Cool information tint: `#E9F1EE`

Do not use gradients. Do not use purple. Color is reserved for state, evidence, selection, and primary actions.

### Typography

- Headings: Space Grotesk, tight tracking, strong but not oversized in the application workspace.
- Body and controls: General Sans or Inter.
- Technical labels, formulas, parameters, tags, and metadata: JetBrains Mono.
- Page title: 30–36px.
- Section title: 18–22px.
- Body: 14–16px.
- Technical labels: 10–12px, uppercase where useful.

### Geometry

- Border radius: 0–4px. This is a structured engineering tool, not a soft consumer application.
- Borders: 1px hairlines.
- No drop shadows.
- Main layout: 220px progress rail, flexible center, 340px evidence panel.
- Base spacing scale: 4, 8, 12, 16, 24, 32, 48.

## Components

### Evidence badge

Three variants:

- `INPUT` — neutral outlined badge
- `CALCULATED` — forest/mint badge
- `BENCHMARK REQUIRED` — gold badge

Use monospaced 10px uppercase text and a small square indicator.

### Metric field

Label, optional helper, numeric input, unit selector, and validation state. Related P50/P95 fields should appear together.

### Calculation block

Paper or cool-tinted panel with a monospaced formula, substituted values, result, and expandable assumptions.

### Candidate card

Contains:

- candidate purpose,
- instance type,
- GPU count and memory,
- precision,
- tensor parallel setting,
- context limit,
- estimated replicas,
- fit/risk status,
- rationale,
- benchmark state.

The recommended card uses a 3px forest top border and a `RECOMMENDED` label, not a shadow or oversized treatment.

### vLLM configuration block

A compact editable parameter table showing:

- `dtype`
- `quantization`
- `tensor_parallel_size`
- `pipeline_parallel_size`
- `gpu_memory_utilization`
- `max_model_len`
- `max_num_seqs`
- `max_num_batched_tokens`
- `enable_prefix_caching`

Each row includes the proposed value and a plain-language reason.

### Agent explanation

Use a narrow response area with:

- `Decision`
- `Because`
- `Evidence`
- `Risk`
- `Next proof`

Do not use chat bubbles for the primary recommendation.

## Motion

- 120–180ms ease-out for field and panel updates.
- Candidate calculations may reveal from top to bottom with a subtle opacity transition.
- No decorative continuous animation.
- Respect reduced-motion preferences.

## Responsive behavior

Desktop is primary at 1440px. At narrower desktop widths, collapse the evidence panel into a drawer. On tablet, collapse the progress rail into a horizontal stage indicator. Mobile may support reviewing an assessment but is not the primary authoring experience.

## Initial design screen

Create the main `Sizing assessment` workspace in a realistic mid-progress state:

- Stages 1 and 2 completed, stage 3 active.
- Example model: `meta-llama/Llama-3.1-8B-Instruct`.
- BF16 selected.
- P50/P95 input: 1,500 / 6,000 tokens.
- P50/P95 output: 250 / 800 tokens.
- Peak traffic: 3 requests/sec.
- Target P95 TTFT: 1.5 seconds.
- Context policy: 8,192 tokens.
- Evidence panel estimates 16 GB raw weight memory and warns that a 24 GB GPU needs benchmark validation because weights alone do not include KV cache and runtime overhead.
- Primary action: `Calculate candidate configurations`.

The design should communicate that recommendations will be generated from calculations and measured evidence, not from the language model's opinion.
