"use client";

import { useState, useRef, useEffect } from "react";

interface Message {
  role: "user" | "assistant";
  content: string;
  pipeline?: PipelineState;
}

interface PipelineStep {
  step: string;
  status: string;
  label?: string;
  data?: Record<string, unknown>;
}

interface PipelineState {
  steps: PipelineStep[];
  retrieval?: { domains: string[]; capabilities: string[]; metadata: Record<string, unknown> };
  plan?: { goal: string; steps: { id: string; type: string; capability?: string; operator?: string }[] };
  execution?: { success: boolean; trace: Record<string, unknown> };
  evidence?: { claims: { claim: string; field?: string; delta?: number }[] };
  answer?: string;
  elapsed?: number;
}

const SUGGESTIONS = [
  "Why did Sarah's net pay drop $340 this month?",
  "What is Sarah's total compensation package?",
  "Is Sarah compliant with California overtime rules?",
  "What training is Sarah overdue on?",
  "How much PTO does Sarah have left?",
];

export default function Home() {
  const [activeTab, setActiveTab] = useState<"chat" | "architecture">("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [currentPipeline, setCurrentPipeline] = useState<PipelineState | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, currentPipeline]);

  const handleSubmit = async (question?: string) => {
    const q = question || input.trim();
    if (!q || isLoading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setIsLoading(true);
    setCurrentPipeline({ steps: [] });

    try {
      const response = await fetch("http://localhost:9000/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No reader");

      const decoder = new TextDecoder();
      let buffer = "";
      let pipeline: PipelineState = { steps: [] };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            pipeline = processEvent(pipeline, data);
            setCurrentPipeline({ ...pipeline });
          } catch {}
        }
      }

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: pipeline.answer || "No answer generated.", pipeline },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Error connecting to backend. Is the API server running on port 9000?" },
      ]);
    }

    setCurrentPipeline(null);
    setIsLoading(false);
    inputRef.current?.focus();
  };

  const processEvent = (state: PipelineState, data: Record<string, unknown>): PipelineState => {
    const event = data.event as string;
    const newState = { ...state };

    if (event === "step") {
      const existing = newState.steps.findIndex((s) => s.step === data.step);
      const stepData: PipelineStep = { step: data.step as string, status: data.status as string, label: data.label as string };
      if (existing >= 0) newState.steps[existing] = stepData;
      else newState.steps = [...newState.steps, stepData];
    } else if (event === "retrieval") {
      newState.retrieval = data as PipelineState["retrieval"];
      newState.steps = newState.steps.map((s) => s.step === "retrieval" ? { ...s, status: "done" } : s);
    } else if (event === "plan") {
      newState.plan = data as unknown as PipelineState["plan"];
      newState.steps = newState.steps.map((s) => s.step === "planning" ? { ...s, status: "done" } : s);
    } else if (event === "execution") {
      newState.execution = data as unknown as PipelineState["execution"];
      newState.steps = newState.steps.map((s) => s.step === "execution" ? { ...s, status: "done" } : s);
    } else if (event === "evidence") {
      newState.evidence = data as unknown as PipelineState["evidence"];
      newState.steps = newState.steps.map((s) => s.step === "evidence" ? { ...s, status: "done" } : s);
    } else if (event === "answer") {
      newState.answer = data.answer as string;
      newState.steps = newState.steps.map((s) => s.step === "synthesis" ? { ...s, status: "done" } : s);
    } else if (event === "done") {
      newState.elapsed = data.elapsed as number;
    }
    return newState;
  };

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-[var(--border)]">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[var(--accent)] flex items-center justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-black">
              <circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m-10-10h4m12 0h4m-3.5-6.5l-2.8 2.8m-5.4 5.4l-2.8 2.8m0-11l2.8 2.8m5.4 5.4l2.8 2.8"/>
            </svg>
          </div>
          <span className="font-semibold text-sm">Enterprise API Knowledge Graph</span>
        </div>
        <div className="flex items-center gap-1 bg-[var(--bg-secondary)] rounded-lg p-1">
          <button
            onClick={() => setActiveTab("chat")}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${activeTab === "chat" ? "bg-[var(--bg-tertiary)] text-[var(--accent)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
          >
            Chat
          </button>
          <button
            onClick={() => setActiveTab("architecture")}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${activeTab === "architecture" ? "bg-[var(--bg-tertiary)] text-[var(--accent)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
          >
            Architecture
          </button>
        </div>
      </header>

      {activeTab === "architecture" && (
        <iframe src="/architecture.html" className="flex-1 w-full border-none" />
      )}

      {activeTab === "chat" && (<>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-8">
          {messages.length === 0 && !isLoading && (
            <div className="flex flex-col items-center justify-center h-full min-h-[60vh]">
              <h2 className="text-2xl font-semibold mb-2">Ask about your enterprise APIs</h2>
              <p className="text-[var(--text-secondary)] mb-8 text-center max-w-md">
                Cross-domain questions answered by orchestrating APIs through a semantic knowledge graph.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => handleSubmit(s)}
                    className="text-left text-sm px-4 py-3 rounded-xl border border-[var(--border)] hover:border-[var(--accent-dim)] hover:bg-[var(--bg-secondary)] transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`mb-6 ${msg.role === "user" ? "flex justify-end" : ""}`}>
              {msg.role === "user" ? (
                <div className="bg-[var(--bg-tertiary)] px-4 py-3 rounded-2xl rounded-br-md max-w-[80%] text-sm">
                  {msg.content}
                </div>
              ) : (
                <AssistantMessage message={msg} />
              )}
            </div>
          ))}

          {/* Streaming pipeline */}
          {currentPipeline && <PipelineIndicator pipeline={currentPipeline} />}

          <div ref={messagesEndRef} />
        </div>
      </main>

      {/* Input */}
      <div className="border-t border-[var(--border)] px-4 py-4">
        <div className="max-w-3xl mx-auto relative">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); } }}
            placeholder="Ask a question about your APIs..."
            disabled={isLoading}
            rows={1}
            className="w-full resize-none rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] px-4 py-3 pr-12 text-sm focus:outline-none focus:border-[var(--accent-dim)] disabled:opacity-50 placeholder:text-[var(--text-secondary)]"
          />
          <button
            onClick={() => handleSubmit()}
            disabled={isLoading || !input.trim()}
            className="absolute right-3 top-1/2 -translate-y-1/2 w-7 h-7 rounded-lg bg-[var(--accent)] flex items-center justify-center disabled:opacity-30 hover:opacity-90 transition-opacity"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-black">
              <path d="M5 12h14m-7-7l7 7-7 7"/>
            </svg>
          </button>
        </div>
        <p className="text-center text-xs text-[var(--text-secondary)] mt-2">
          Semantic search → Graph expansion → LLM planning → Deterministic execution
        </p>
      </div>
      </>)}
    </div>
  );
}

const PIPELINE_STEPS = [
  { key: "retrieval", icon: "🔍", label: "Semantic Search", description: "Searching capability descriptions via vector similarity" },
  { key: "planning", icon: "🧠", label: "LLM Planning", description: "Generating execution plan from retrieved capabilities" },
  { key: "execution", icon: "⚡", label: "API Execution", description: "Running API calls in dependency order" },
  { key: "evidence", icon: "📋", label: "Evidence", description: "Extracting structured claims from results" },
  { key: "synthesis", icon: "✍️", label: "Synthesis", description: "Generating grounded answer from evidence" },
];

function PipelineIndicator({ pipeline }: { pipeline: PipelineState }) {
  const activeStep = pipeline.steps[pipeline.steps.length - 1];

  return (
    <div className="mb-6 bg-[var(--bg-secondary)] rounded-xl p-4 border border-[var(--border)]">
      {/* Step indicators */}
      <div className="space-y-2">
        {PIPELINE_STEPS.map((step) => {
          const s = pipeline.steps.find((p) => p.step === step.key);
          const status = s?.status || "pending";
          const isActive = status === "running" || status === "repairing";
          const isDone = status === "done";

          return (
            <div key={step.key} className={`flex items-center gap-3 transition-opacity ${status === "pending" ? "opacity-30" : "opacity-100"}`}>
              {/* Status icon */}
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs flex-shrink-0 ${
                isDone ? "bg-[var(--accent)] text-black" :
                isActive ? "bg-[var(--accent-dim)] animate-pulse" :
                "bg-[var(--border)]"
              }`}>
                {isDone ? "✓" : isActive ? step.icon : "·"}
              </div>
              {/* Label */}
              <div className="flex-1 min-w-0">
                <div className={`text-xs font-medium ${isActive ? "text-[var(--accent)]" : isDone ? "text-[var(--text-primary)]" : "text-[var(--text-secondary)]"}`}>
                  {step.label}
                  {status === "repairing" && <span className="ml-2 text-[#ff6b6b]">(repairing...)</span>}
                </div>
                {isActive && (
                  <div className="text-xs text-[var(--text-secondary)] mt-0.5">{step.description}</div>
                )}
                {isDone && step.key === "retrieval" && pipeline.retrieval && (
                  <div className="text-xs text-[var(--text-secondary)] mt-0.5">
                    Found {pipeline.retrieval.capabilities.length} APIs across {pipeline.retrieval.domains.join(", ")}
                  </div>
                )}
                {isDone && step.key === "planning" && pipeline.plan && (
                  <div className="text-xs text-[var(--text-secondary)] mt-0.5">
                    {pipeline.plan.steps.length} steps — {pipeline.plan.goal}
                  </div>
                )}
              </div>
              {/* Timer */}
              {isActive && (
                <div className="text-xs text-[var(--text-secondary)] flex-shrink-0">
                  <span className="animate-pulse">●</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AssistantMessage({ message }: { message: Message }) {
  const [showDetails, setShowDetails] = useState(false);
  const pipeline = message.pipeline;

  return (
    <div className="w-full">
      <div className="answer-content text-sm leading-relaxed whitespace-pre-wrap">
        {message.content.split("\n").map((line, i) => {
          if (line.startsWith("# ")) return <h1 key={i}>{line.slice(2)}</h1>;
          if (line.startsWith("## ")) return <h2 key={i}>{line.slice(3)}</h2>;
          if (line.startsWith("- **")) return <li key={i} dangerouslySetInnerHTML={{ __html: formatBold(line.slice(2)) }} />;
          if (line.startsWith("- ")) return <li key={i}>{line.slice(2)}</li>;
          if (line.trim() === "") return <br key={i} />;
          return <p key={i} dangerouslySetInnerHTML={{ __html: formatBold(line) }} />;
        })}
      </div>

      {pipeline && (
        <div className="mt-4">
          <button
            onClick={() => setShowDetails(!showDetails)}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--accent)] flex items-center gap-1 transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`transition-transform ${showDetails ? "rotate-90" : ""}`}>
              <path d="M9 18l6-6-6-6"/>
            </svg>
            {showDetails ? "Hide" : "Show"} reasoning ({pipeline.elapsed?.toFixed(1)}s)
          </button>

          {showDetails && (
            <div className="mt-3 space-y-3 text-xs border-l-2 border-[var(--border)] pl-4">
              {/* Retrieval */}
              {pipeline.retrieval && (
                <div>
                  <div className="font-medium text-[var(--accent)] mb-1">Retrieval</div>
                  <p className="text-[var(--text-secondary)]">
                    Found {pipeline.retrieval.capabilities.length} APIs across {pipeline.retrieval.domains.join(", ")}
                  </p>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {pipeline.retrieval.capabilities.slice(0, 8).map((c) => (
                      <span key={c} className="px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">{c}</span>
                    ))}
                  </div>
                </div>
              )}
              {/* Plan */}
              {pipeline.plan && (
                <div>
                  <div className="font-medium text-[#9b59b6] mb-1">Plan: {pipeline.plan.goal}</div>
                  {pipeline.plan.steps.map((s, i) => (
                    <div key={i} className="text-[var(--text-secondary)] ml-2">
                      {s.type === "api_call" ? `${i + 1}. CALL ${s.capability}` : `${i + 1}. ${(s.operator || "").toUpperCase()}`}
                    </div>
                  ))}
                </div>
              )}
              {/* Evidence */}
              {pipeline.evidence && pipeline.evidence.claims.length > 0 && (
                <div>
                  <div className="font-medium text-[#f5a623] mb-1">Evidence ({pipeline.evidence.claims.length} claims)</div>
                  {pipeline.evidence.claims.slice(0, 5).map((c, i) => (
                    <div key={i} className="text-[var(--text-secondary)] ml-2">{c.claim}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatBold(text: string): string {
  return text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
}
