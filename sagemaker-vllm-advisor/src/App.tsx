import { useEffect, useMemo, useState } from "react";
import { runAdvisor } from "./engine/advisor";
import type {
  AssessmentInput,
  Candidate,
  EvidenceKind,
  Precision
} from "./engine/types";
import {
  loadHuggingFaceModel,
  searchHuggingFaceModels,
  type HuggingFaceModelSummary
} from "./services/huggingface";
import {
  analyzeAssessment,
  askAdvisor,
  createDeploymentPlan,
  type AgentAnswer,
  type DeploymentPlan
} from "./services/advisorApi";

const DEFAULT_INPUT: AssessmentInput = {
  modelId: "meta-llama/Llama-3.1-8B-Instruct",
  parameterBillions: 8.03,
  layers: 32,
  hiddenSize: 4096,
  attentionHeads: 32,
  kvHeads: 8,
  precision: "BF16",
  p50InputTokens: 1500,
  p95InputTokens: 6000,
  p50OutputTokens: 250,
  p95OutputTokens: 800,
  peakRequestsPerSecond: 3,
  targetTtftSeconds: 1.5,
  maxModelLength: 8192,
  region: "us-east-1",
  availability: "multi-az",
  monthlyCostCeiling: undefined,
  sharedPromptPrefix: true
};

const PRESETS: Record<string, Partial<AssessmentInput>> = {
  llama8b: {
    modelId: "meta-llama/Llama-3.1-8B-Instruct",
    parameterBillions: 8.03,
    layers: 32,
    hiddenSize: 4096,
    attentionHeads: 32,
    kvHeads: 8
  },
  mistral7b: {
    modelId: "mistralai/Mistral-7B-Instruct-v0.3",
    parameterBillions: 7.25,
    layers: 32,
    hiddenSize: 4096,
    attentionHeads: 32,
    kvHeads: 8
  },
  llama70b: {
    modelId: "meta-llama/Llama-3.1-70B-Instruct",
    parameterBillions: 70.6,
    layers: 80,
    hiddenSize: 8192,
    attentionHeads: 64,
    kvHeads: 8
  }
};

function EvidenceBadge({ kind }: { kind: EvidenceKind }) {
  const labels: Record<EvidenceKind, string> = {
    input: "Input",
    calculated: "Calculated",
    heuristic: "Planning estimate",
    benchmark: "Benchmark required",
    aws: "Live AWS"
  };
  return <span className={`evidence evidence--${kind}`}>{labels[kind]}</span>;
}

function NumericField({
  label,
  value,
  unit,
  onChange,
  min = 0,
  step = 1
}: {
  label: string;
  value: number;
  unit?: string;
  onChange: (value: number) => void;
  min?: number;
  step?: number;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <div className="numeric-input">
        <input
          type="number"
          min={min}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        {unit && <small>{unit}</small>}
      </div>
    </label>
  );
}

function CandidateCard({
  candidate,
  label,
  selected,
  onSelect
}: {
  candidate: Candidate;
  label: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <article className={`candidate ${selected ? "candidate--selected" : ""}`}>
      <div className="candidate__topline">
        <span>{label}</span>
        <span className={`fit fit--${candidate.fit}`}>{candidate.fit}</span>
      </div>
      <h3>{candidate.instance.instanceType}</h3>
      <p className="candidate__gpu">
        {candidate.instance.gpuCount}× {candidate.instance.gpuName} ·{" "}
        {candidate.instance.memoryPerGpuGb} GB/GPU
      </p>
      <dl className="candidate__metrics">
        <div>
          <dt>Tensor parallel</dt>
          <dd>{candidate.tensorParallelSize}</dd>
        </div>
        <div>
          <dt>KV-cache budget</dt>
          <dd>{candidate.availableKvCacheGb.toFixed(1)} GB</dd>
        </div>
        <div>
          <dt>Estimated concurrency</dt>
          <dd>{candidate.estimatedConcurrentSequences}</dd>
        </div>
        <div>
          <dt>Planning replicas</dt>
          <dd>{candidate.estimatedReplicas}</dd>
        </div>
        <div>
          <dt>Endpoint quota</dt>
          <dd>
            {candidate.liveAws?.quotaStatus === "verified"
              ? candidate.liveAws.endpointQuota
              : "Not verified"}
          </dd>
        </div>
        <div>
          <dt>Monthly estimate</dt>
          <dd>
            {candidate.liveAws?.monthlyPriceUsd != null
              ? `$${candidate.liveAws.monthlyPriceUsd.toLocaleString()}`
              : "Unavailable"}
          </dd>
        </div>
      </dl>
      <p className="candidate__warning">{candidate.warnings[0]}</p>
      <button type="button" className="secondary-button" onClick={onSelect}>
        {selected ? "Configuration selected" : "Review configuration"}
      </button>
    </article>
  );
}

function App() {
  const [input, setInput] = useState(DEFAULT_INPUT);
  const [hasCalculated, setHasCalculated] = useState(false);
  const [hasBenchmarkPlan, setHasBenchmarkPlan] = useState(false);
  const [saveStatus, setSaveStatus] = useState("Save assessment");
  const [modelMatches, setModelMatches] = useState<HuggingFaceModelSummary[]>([]);
  const [modelSearchStatus, setModelSearchStatus] = useState("");
  const [modelSearchOpen, setModelSearchOpen] = useState(false);
  const [modelQuery, setModelQuery] = useState("");
  const [loadedArchitecture, setLoadedArchitecture] = useState("LlamaForCausalLM");
  const [modelRevision, setModelRevision] = useState<string>();
  const [serverResult, setServerResult] = useState<ReturnType<
    typeof runAdvisor
  > | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [agentQuestion, setAgentQuestion] = useState(
    "Explain the recommended instance, vLLM settings, and what must be benchmarked."
  );
  const [agentAnswer, setAgentAnswer] = useState<AgentAnswer | null>(null);
  const [isAskingAgent, setIsAskingAgent] = useState(false);
  const [deploymentPlan, setDeploymentPlan] = useState<DeploymentPlan | null>(
    null
  );
  const previewResult = useMemo(() => runAdvisor(input), [input]);
  const result = serverResult ?? previewResult;
  const [selectedType, setSelectedType] = useState<string>("");
  const selected =
    result.candidates.find(
      (candidate) => candidate.instance.instanceType === selectedType
    ) ?? result.recommendation;

  const update = <K extends keyof AssessmentInput>(
    key: K,
    value: AssessmentInput[K]
  ) => {
    setInput((current) => ({ ...current, [key]: value }));
    setServerResult(null);
    setHasCalculated(false);
    setDeploymentPlan(null);
  };

  const applyPreset = (preset: string) => {
    const presetValues = PRESETS[preset];
    setInput((current) => ({ ...current, ...presetValues }));
    setHasCalculated(false);
    setHasBenchmarkPlan(false);
    setServerResult(null);
    setDeploymentPlan(null);
    setModelSearchOpen(false);
    setModelMatches([]);
    setModelQuery("");
    setModelSearchStatus("Preset values loaded. Use Load metadata to refresh from Hugging Face.");
  };

  useEffect(() => {
    const query = modelQuery.trim();
    if (query.length < 2) {
      setModelMatches([]);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setModelSearchStatus("Searching Hugging Face…");
      setModelSearchOpen(true);
      try {
        const matches = await searchHuggingFaceModels(query, controller.signal);
        setModelMatches(matches);
        setModelSearchStatus(
          matches.length
            ? `${matches.length} public text-generation models found. Select one to load its metadata.`
            : "No public text-generation models matched"
        );
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setModelSearchStatus((error as Error).message);
          setModelMatches([]);
        }
      }
    }, 300);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [modelQuery]);

  const loadModel = async (modelId = input.modelId) => {
    setModelSearchStatus("Loading model configuration…");
    setModelSearchOpen(false);
    setModelMatches([]);
    setModelQuery("");
    try {
      const metadata = await loadHuggingFaceModel(modelId);
      setInput((current) => ({ ...current, ...metadata.patch }));
      setLoadedArchitecture(metadata.architecture);
      setModelRevision(metadata.revision);
      setHasCalculated(false);
      setHasBenchmarkPlan(false);
      setServerResult(null);
      setDeploymentPlan(null);
      setModelSearchStatus(
        `${metadata.id} loaded from Hugging Face (${metadata.architecture})`
      );
    } catch (error) {
      setModelSearchStatus((error as Error).message);
    }
  };

  const saveAssessment = () => {
    localStorage.setItem(
      "model-deployment-advisor-assessment",
      JSON.stringify({ input, selectedType, savedAt: new Date().toISOString() })
    );
    setSaveStatus("Saved locally");
    window.setTimeout(() => setSaveStatus("Save assessment"), 1800);
  };

  const calculateCandidates = async () => {
    setIsAnalyzing(true);
    setAnalysisStatus("Reading AWS quotas and endpoint pricing…");
    setDeploymentPlan(null);
    try {
      const liveResult = await analyzeAssessment(input);
      setServerResult(liveResult);
      setHasCalculated(true);
      setHasBenchmarkPlan(false);
      setSelectedType(liveResult.recommendation?.instance.instanceType ?? "");
      setAnalysisStatus(
        liveResult.liveAwsEnabled
          ? "Sizing complete with live AWS account evidence."
          : "Sizing complete; live AWS evidence was unavailable."
      );
    } catch (error) {
      setServerResult(null);
      setHasCalculated(true);
      setSelectedType(previewResult.recommendation?.instance.instanceType ?? "");
      setAnalysisStatus(
        `Backend unavailable; showing local planning preview. ${(error as Error).message}`
      );
    } finally {
      setIsAnalyzing(false);
    }
  };

  const runAgent = async () => {
    if (!agentQuestion.trim()) return;
    setIsAskingAgent(true);
    setAgentAnswer(null);
    try {
      setAgentAnswer(await askAdvisor(agentQuestion, input));
    } catch (error) {
      setAgentAnswer({
        answer: `The Bedrock advisor could not answer: ${(error as Error).message}`,
        modelId: "unavailable",
        trace: [],
        toolsUsed: []
      });
    } finally {
      setIsAskingAgent(false);
    }
  };

  const prepareDeploymentPlan = async () => {
    if (!selected) return;
    try {
      setDeploymentPlan(
        await createDeploymentPlan(input, selected, modelRevision)
      );
    } catch (error) {
      setAnalysisStatus(`Deployment plan failed: ${(error as Error).message}`);
    }
  };

  const recommendationCards = result.recommendation
    ? [
        {
          label: "Recommended balance",
          candidate: result.recommendation
        },
        {
          label: "Lower cost",
          candidate: result.lowerCostAlternative!
        },
        {
          label: "Lower latency",
          candidate: result.lowerLatencyAlternative!
        }
      ].filter(
        (entry, index, all) =>
          all.findIndex(
            (candidate) =>
              candidate.candidate.instance.instanceType ===
              entry.candidate.instance.instanceType
          ) === index
      )
    : [];

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark">M</span>
          <strong>Model Deployment Advisor</strong>
          <span className="status-badge">
            <i /> {hasCalculated ? "Candidates ready" : "Sizing draft"}
          </span>
        </div>
        <div className="topbar__actions">
          <button
            type="button"
            className="ghost-button"
            onClick={saveAssessment}
          >
            {saveStatus}
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={() => {
              setInput(DEFAULT_INPUT);
              setHasCalculated(false);
              setHasBenchmarkPlan(false);
              setSelectedType("");
              setServerResult(null);
              setAgentAnswer(null);
              setDeploymentPlan(null);
              setAnalysisStatus("");
            }}
          >
            New assessment
          </button>
        </div>
      </header>

      <div className="workspace">
        <aside className="progress-rail">
          <div>
            <p className="eyebrow">Assessment progress</p>
            <ol>
              {[
                ["01", "Model", true],
                ["02", "Workload", true],
                ["03", "Constraints", true],
                ["04", "Sizing", hasCalculated],
                ["05", "Benchmark", hasBenchmarkPlan],
                ["06", "Recommendation", false]
              ].map(([number, label, complete], index) => (
                <li
                  className={
                    hasBenchmarkPlan
                      ? index === 5
                        ? "active"
                        : complete
                          ? "complete"
                          : ""
                      : hasCalculated
                      ? index === 4
                        ? "active"
                        : complete
                          ? "complete"
                          : ""
                      : index === 2
                        ? "active"
                        : complete
                          ? "complete"
                          : ""
                  }
                  key={String(label)}
                >
                  <span>{complete ? "✓" : number}</span>
                  <div>
                    <strong>{label}</strong>
                    <small>
                      {complete ? "Complete" : index === (hasCalculated ? 4 : 2) ? "Active" : "Pending"}
                    </small>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <div className="rail-evidence">
            <p className="eyebrow">Current evidence</p>
            <strong>
              <i /> Inputs + calculations
            </strong>
            <small>No measured benchmark evidence yet.</small>
          </div>
        </aside>

        <main className="assessment">
          <div className="assessment__heading">
            <div>
              <p className="eyebrow">Assessment / guided sizing</p>
              <h1>SageMaker deployment assessment</h1>
              <p>
                Create an evidence-backed vLLM configuration shortlist from
                model shape and production workload constraints.
              </p>
            </div>
            <EvidenceBadge kind="input" />
          </div>

          <section className="form-section">
            <div className="section-heading">
              <div>
                <h2>Model</h2>
                <p>Choose a known profile or edit the architecture fields.</p>
              </div>
              <EvidenceBadge kind="input" />
            </div>
            <div className="preset-row">
              <button type="button" onClick={() => applyPreset("llama8b")}>
                Llama 3.1 8B
              </button>
              <button type="button" onClick={() => applyPreset("mistral7b")}>
                Mistral 7B
              </button>
              <button type="button" onClick={() => applyPreset("llama70b")}>
                Llama 3.1 70B
              </button>
              <span>or search any public Hugging Face text-generation model</span>
            </div>
            <div className="field-grid field-grid--model">
              <div className="field field--wide model-search">
                <span>Search Hugging Face models</span>
                <div className="model-search__input">
                  <input
                    aria-label="Search Hugging Face models"
                    autoComplete="off"
                    placeholder="Type a name, e.g. qwen, mistral, phi"
                    value={modelQuery}
                    onFocus={() => setModelSearchOpen(true)}
                    onChange={(event) => {
                      setModelQuery(event.target.value);
                      setModelSearchOpen(true);
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => setModelSearchOpen(true)}
                  >
                    Search
                  </button>
                </div>
                {modelSearchOpen && modelMatches.length > 0 && (
                  <div className="model-results" role="listbox">
                    {modelMatches.map((model) => (
                      <button
                        type="button"
                        role="option"
                        key={model.id}
                        onClick={() => loadModel(model.id)}
                      >
                        <span>
                          <strong>{model.id}</strong>
                          <small>
                            {model.downloads.toLocaleString()} downloads
                            {model.gated ? " · gated" : ""}
                          </small>
                        </span>
                        <i>Load</i>
                      </button>
                    ))}
                  </div>
                )}
                <small
                  className={`model-search__status ${
                    modelSearchStatus.includes("could not") ||
                    modelSearchStatus.includes("failed") ||
                    modelSearchStatus.includes("requires")
                      ? "model-search__status--error"
                      : ""
                  }`}
                  aria-live="polite"
                >
                  {modelSearchStatus ||
                    "Type a model name to search, then select a result to load it."}
                </small>
              </div>
              <div className="field field--wide model-search">
                <span>Selected model ID</span>
                <div className="model-search__input">
                  <input
                    aria-label="Hugging Face model ID"
                    autoComplete="off"
                    value={input.modelId}
                    onChange={(event) => update("modelId", event.target.value)}
                  />
                  <button type="button" onClick={() => loadModel()}>
                    Load metadata
                  </button>
                </div>
                <small className="model-search__status">
                  Selecting a search result fills this automatically. You can
                  also type an exact ID like Qwen/Qwen2.5-7B-Instruct.
                </small>
              </div>
              <label className="field">
                <span>Precision</span>
                <select
                  value={input.precision}
                  onChange={(event) =>
                    update("precision", event.target.value as Precision)
                  }
                >
                  {["BF16", "FP16", "FP8", "INT8", "INT4"].map((precision) => (
                    <option key={precision}>{precision}</option>
                  ))}
                </select>
              </label>
              <NumericField
                label="Parameters"
                value={input.parameterBillions}
                unit="billion"
                step={0.1}
                onChange={(value) => update("parameterBillions", value)}
              />
              <NumericField
                label="Layers"
                value={input.layers}
                onChange={(value) => update("layers", value)}
              />
              <NumericField
                label="Hidden size"
                value={input.hiddenSize}
                onChange={(value) => update("hiddenSize", value)}
              />
              <NumericField
                label="Attention heads"
                value={input.attentionHeads}
                onChange={(value) => update("attentionHeads", value)}
              />
              <NumericField
                label="KV heads"
                value={input.kvHeads}
                onChange={(value) => update("kvHeads", value)}
              />
            </div>
            <div className="metadata-strip">
              <span>
                Architecture <strong>{loadedArchitecture}</strong>
              </span>
              <span>
                Parameters <strong>{input.parameterBillions.toFixed(2)}B</strong>
              </span>
              <span>
                Source <strong>Hugging Face or editable input</strong>
              </span>
            </div>
          </section>

          <section className="form-section">
            <div className="section-heading">
              <div>
                <h2>Workload</h2>
                <p>Use production-shaped percentiles rather than averages.</p>
              </div>
              <EvidenceBadge kind="input" />
            </div>
            <div className="field-grid field-grid--four">
              <NumericField
                label="P50 input"
                value={input.p50InputTokens}
                unit="tokens"
                onChange={(value) => update("p50InputTokens", value)}
              />
              <NumericField
                label="P95 input"
                value={input.p95InputTokens}
                unit="tokens"
                onChange={(value) => update("p95InputTokens", value)}
              />
              <NumericField
                label="P50 output"
                value={input.p50OutputTokens}
                unit="tokens"
                onChange={(value) => update("p50OutputTokens", value)}
              />
              <NumericField
                label="P95 output"
                value={input.p95OutputTokens}
                unit="tokens"
                onChange={(value) => update("p95OutputTokens", value)}
              />
              <NumericField
                label="Peak traffic"
                value={input.peakRequestsPerSecond}
                unit="req/s"
                step={0.1}
                onChange={(value) => update("peakRequestsPerSecond", value)}
              />
              <NumericField
                label="Target P95 TTFT"
                value={input.targetTtftSeconds}
                unit="seconds"
                step={0.1}
                onChange={(value) => update("targetTtftSeconds", value)}
              />
              <NumericField
                label="Context policy"
                value={input.maxModelLength}
                unit="tokens"
                onChange={(value) => update("maxModelLength", value)}
              />
              <label className="field">
                <span>Availability</span>
                <select
                  value={input.availability}
                  onChange={(event) =>
                    update(
                      "availability",
                      event.target.value as AssessmentInput["availability"]
                    )
                  }
                >
                  <option value="multi-az">Multi-AZ preferred</option>
                  <option value="single">Single replica permitted</option>
                </select>
              </label>
            </div>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={input.sharedPromptPrefix}
                onChange={(event) =>
                  update("sharedPromptPrefix", event.target.checked)
                }
              />
              <span>
                Requests share a long reusable system prompt or document prefix
              </span>
            </label>
          </section>

          <div className="assessment__action">
            <p>
              Candidate capacity is a planning estimate. Production performance
              remains unverified until benchmarked.
            </p>
            <button
              type="button"
              className="primary-button"
              onClick={calculateCandidates}
              disabled={isAnalyzing}
            >
              {isAnalyzing
                ? "Checking live AWS evidence…"
                : "Calculate candidate configurations →"}
            </button>
          </div>
          {analysisStatus && (
            <p className="analysis-status" aria-live="polite">
              {analysisStatus}
            </p>
          )}

          {hasCalculated && (
            <section className="results" aria-live="polite">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Agent recommendation</p>
                  <h2>Candidate configurations</h2>
                  <p>
                    Ranked using deterministic fit checks and explicitly labelled
                    planning heuristics.
                  </p>
                </div>
                <EvidenceBadge kind="heuristic" />
              </div>
              <div className="candidate-grid">
                {recommendationCards.map(({ label, candidate }) => (
                  <CandidateCard
                    key={`${label}-${candidate.instance.instanceType}`}
                    label={label}
                    candidate={candidate}
                    selected={
                      selected?.instance.instanceType ===
                      candidate.instance.instanceType
                    }
                    onSelect={() =>
                      setSelectedType(candidate.instance.instanceType)
                    }
                  />
                ))}
              </div>

              {selected && (
                <div className="configuration">
                  <div className="configuration__heading">
                    <div>
                      <p className="eyebrow">Proposed baseline</p>
                      <h2>{selected.instance.instanceType} vLLM settings</h2>
                    </div>
                    <EvidenceBadge kind="benchmark" />
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Parameter</th>
                          <th>Proposed value</th>
                          <th>Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selected.vllmSettings.map((setting) => (
                          <tr key={setting.parameter}>
                            <td>
                              <code>{setting.parameter}</code>
                            </td>
                            <td>
                              <code>{setting.value}</code>
                            </td>
                            <td>{setting.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => setHasBenchmarkPlan(true)}
                  >
                    Create benchmark plan →
                  </button>
                  <button
                    type="button"
                    className="secondary-button plan-button"
                    onClick={prepareDeploymentPlan}
                  >
                    Prepare approval-gated deployment plan
                  </button>
                  {deploymentPlan && (
                    <div className="deployment-plan">
                      <strong>Deployment remains locked</strong>
                      <p>{deploymentPlan.blockedReason}</p>
                      <code>Plan hash: {deploymentPlan.planHash}</code>
                      <small>
                        Cost ceiling ${deploymentPlan.maximumTestCostUsd} · cleanup{" "}
                        {new Date(
                          deploymentPlan.cleanupDeadline
                        ).toLocaleString()}
                      </small>
                    </div>
                  )}
                </div>
              )}

              {hasBenchmarkPlan && selected && (
                <div className="benchmark-plan" aria-live="polite">
                  <div className="configuration__heading">
                    <div>
                      <p className="eyebrow">Evidence gate</p>
                      <h2>Production-shaped benchmark plan</h2>
                      <p>
                        Run each load stage against {selected.instance.instanceType}
                        before accepting the planning estimates.
                      </p>
                    </div>
                    <EvidenceBadge kind="benchmark" />
                  </div>
                  <div className="benchmark-grid">
                    {[
                      {
                        stage: "01",
                        title: "Memory and startup",
                        detail:
                          "Load the model, confirm healthy startup, record initialization time, GPU memory, and OOM behavior."
                      },
                      {
                        stage: "02",
                        title: "Latency baseline",
                        detail: `Run single-request P50 and P95 prompts; validate the ${input.targetTtftSeconds}s P95 TTFT target and inter-token latency.`
                      },
                      {
                        stage: "03",
                        title: "Concurrency ramp",
                        detail: `Ramp toward ${selected.estimatedConcurrentSequences} in-flight sequences while monitoring queue depth and KV-cache utilization.`
                      },
                      {
                        stage: "04",
                        title: "Peak traffic",
                        detail: `Sustain ${input.peakRequestsPerSecond} requests/sec using the declared input/output distribution and verify error rate.`
                      },
                      {
                        stage: "05",
                        title: "Replica validation",
                        detail: `Test the planning baseline of ${selected.estimatedReplicas} replicas, failure recovery, and the autoscaling signal.`
                      }
                    ].map((item) => (
                      <article key={item.stage}>
                        <span>{item.stage}</span>
                        <div>
                          <strong>{item.title}</strong>
                          <p>{item.detail}</p>
                        </div>
                      </article>
                    ))}
                  </div>
                  <div className="success-gates">
                    <strong>Required success gates</strong>
                    <span>P95 TTFT ≤ {input.targetTtftSeconds}s</span>
                    <span>No GPU OOM</span>
                    <span>Error rate &lt; 1%</span>
                    <span>Queue remains bounded</span>
                    <span>30% burst headroom</span>
                  </div>
                </div>
              )}

              <div className="agent-trace">
                <div className="configuration__heading">
                  <div>
                    <p className="eyebrow">Reason → Act → Observe</p>
                    <h2>Agent decision trace</h2>
                  </div>
                  <EvidenceBadge kind="calculated" />
                </div>
                <ol>
                  {result.trace.map((step, index) => (
                    <li key={`${step.phase}-${index}`}>
                      <span>{step.phase}</span>
                      <div>
                        <strong>{step.title}</strong>
                        <p>{step.detail}</p>
                      </div>
                      <EvidenceBadge kind={step.evidence} />
                    </li>
                  ))}
                </ol>
              </div>

              <div className="agent-console">
                <div className="configuration__heading">
                  <div>
                    <p className="eyebrow">Bedrock tool-use agent</p>
                    <h2>Ask the deployment advisor</h2>
                    <p>
                      The agent can resolve model metadata, run sizing, and read
                      AWS pricing and quota evidence. It has no AWS write tools.
                    </p>
                  </div>
                  <EvidenceBadge kind="aws" />
                </div>
                <textarea
                  aria-label="Question for deployment advisor"
                  value={agentQuestion}
                  onChange={(event) => setAgentQuestion(event.target.value)}
                />
                <button
                  type="button"
                  className="primary-button"
                  disabled={isAskingAgent}
                  onClick={runAgent}
                >
                  {isAskingAgent ? "Advisor is reasoning…" : "Ask advisor →"}
                </button>
                {agentAnswer && (
                  <div className="agent-answer">
                    <p>{agentAnswer.answer}</p>
                    <small>
                      Model: {agentAnswer.modelId}
                      {agentAnswer.toolsUsed.length
                        ? ` · Tools: ${agentAnswer.toolsUsed.join(", ")}`
                        : " · No tools required"}
                    </small>
                  </div>
                )}
              </div>
            </section>
          )}
        </main>

        <aside className="evidence-panel">
          <div className="evidence-panel__header">
            <h2>Evidence & provenance</h2>
            <p>What is known, calculated, estimated, and still unproven.</p>
          </div>
          <section>
            <EvidenceBadge kind="input" />
            <h3>Model summary</h3>
            <dl className="summary-list">
              <div>
                <dt>Model</dt>
                <dd>{input.modelId.split("/").pop()}</dd>
              </div>
              <div>
                <dt>Precision</dt>
                <dd>{input.precision}</dd>
              </div>
              <div>
                <dt>P95 sequence</dt>
                <dd>{result.p95SequenceTokens.toLocaleString()} tokens</dd>
              </div>
              <div>
                <dt>Peak traffic</dt>
                <dd>{input.peakRequestsPerSecond} req/s</dd>
              </div>
            </dl>
          </section>
          <section className="calculation-panel">
            <EvidenceBadge kind="calculated" />
            <h3>Raw model memory</h3>
            <div className="formula">
              <code>parameters × precision bytes × allowance</code>
              <strong>
                {input.parameterBillions.toFixed(2)}B ×{" "}
                {["BF16", "FP16"].includes(input.precision) ? "2" : input.precision === "INT4" ? "0.55" : "1"} × 1.04
              </strong>
              <output>≈ {result.weightMemoryGb.toFixed(1)} GB</output>
            </div>
            <p>
              This does not include KV cache, runtime reserve, or temporary
              execution memory.
            </p>
          </section>
          <section>
            <EvidenceBadge kind="benchmark" />
            <h3>Performance is not proven</h3>
            <p className="warning-callout">
              The shortlist can establish feasibility. TTFT, inter-token latency,
              throughput and final replica count require a controlled benchmark.
            </p>
          </section>
          <section>
            <p className="eyebrow">Current feasibility</p>
            <div className="feasibility">
              <span>!</span>
              <div>
                <strong>
                  {result.candidates.length
                    ? `${result.candidates.length} viable candidates`
                    : "No candidate fits"}
                </strong>
                <p>
                  {result.liveAwsEnabled
                    ? `Applied endpoint quotas were checked in ${input.region}; unavailable prices remain explicitly unverified.`
                    : `Catalogue availability, pricing, quotas and driver compatibility must be validated in ${input.region}.`}
                </p>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

export default App;
