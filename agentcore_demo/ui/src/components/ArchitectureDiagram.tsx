// Clean inline-SVG architecture diagram for the Overview screen.
//
// Shows the real data path for a technical audience:
//   UI → BFF → AgentCore Runtime → { Gateway → 3 mock Lambdas, Memory, Guardrail }
// with Observability + Evaluation + Registry drawn as a governance / quality
// plane, and the protocols on each hop (HTTP/SSE, MCP/SigV4, ADOT) labeled.
//
// Pure SVG + theme tokens — no new dependencies.

const C = {
  navy: "#1a2b4a",
  slate700: "#334155",
  gateway: "#0e7490",
  memory: "#6d28d9",
  guardrail: "#b45309",
  gold: "#b8860b",
  slate600: "#475569",
  slate500: "#64748b",
  slate400: "#94a3b8",
  slate300: "#cbd5e1",
  slate200: "#e2e8f0",
  slate100: "#f1f5f9",
  slate50: "#f8fafc",
  white: "#ffffff",
};

interface NodeBoxProps {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  sub?: string;
  stroke: string;
  fill?: string;
  titleColor?: string;
}

function NodeBox({
  x,
  y,
  w,
  h,
  title,
  sub,
  stroke,
  fill = C.white,
  titleColor = C.navy,
}: NodeBoxProps) {
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={8}
        fill={fill}
        stroke={stroke}
        strokeWidth={1.5}
      />
      <text
        x={x + w / 2}
        y={sub ? y + h / 2 - 5 : y + h / 2 + 4}
        textAnchor="middle"
        fontSize={13}
        fontWeight={600}
        fill={titleColor}
      >
        {title}
      </text>
      {sub && (
        <text
          x={x + w / 2}
          y={y + h / 2 + 12}
          textAnchor="middle"
          fontSize={10.5}
          fill={C.slate500}
        >
          {sub}
        </text>
      )}
    </g>
  );
}

function Arrow({
  x1,
  y1,
  x2,
  y2,
  label,
  dashed = false,
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  label?: string;
  dashed?: boolean;
}) {
  const midX = (x1 + x2) / 2;
  const midY = (y1 + y2) / 2;
  return (
    <g>
      <line
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        stroke={C.slate400}
        strokeWidth={1.5}
        strokeDasharray={dashed ? "4 4" : undefined}
        markerEnd="url(#arrowhead)"
      />
      {label && (
        <text
          x={midX}
          y={midY - 5}
          textAnchor="middle"
          fontSize={9.5}
          fontFamily="var(--font-mono)"
          fill={C.slate500}
        >
          {label}
        </text>
      )}
    </g>
  );
}

export default function ArchitectureDiagram() {
  return (
    <svg
      viewBox="0 0 900 470"
      width="100%"
      role="img"
      aria-label="Advisor Copilot architecture: UI to BFF to AgentCore Runtime, which calls Gateway, Memory and Guardrail, with Observability, Evaluation and Registry as the governance and quality plane."
      style={{ maxWidth: 900, display: "block", margin: "0 auto" }}
    >
      <defs>
        <marker
          id="arrowhead"
          markerWidth="8"
          markerHeight="8"
          refX="6"
          refY="3"
          orient="auto"
        >
          <polygon points="0 0, 7 3, 0 6" fill={C.slate400} />
        </marker>
      </defs>

      {/* request path (top row) */}
      <NodeBox x={30} y={30} w={150} h={54} title="React UI" sub="Vite SPA" stroke={C.slate300} />
      <Arrow x1={180} y1={57} x2={255} y2={57} label="HTTP/SSE" />
      <NodeBox x={255} y={30} w={160} h={54} title="FastAPI BFF" sub="creds stay server-side" stroke={C.slate300} />
      <Arrow x1={415} y1={57} x2={495} y2={57} label="SSE + boto3" />
      <NodeBox
        x={495}
        y={24}
        w={200}
        h={66}
        title="AgentCore Runtime"
        sub="Strands agent · ARM64 · ADOT"
        stroke={C.navy}
        fill={C.slate50}
      />

      {/* runtime → leaf plane */}
      <Arrow x1={560} y1={90} x2={430} y2={160} label="MCP/SigV4" />
      <Arrow x1={595} y1={90} x2={595} y2={160} label="Memory API" />
      <Arrow x1={630} y1={90} x2={760} y2={160} label="ApplyGuardrail" />

      <NodeBox x={330} y={160} w={190} h={56} title="Gateway" sub="3 MCP tool targets" stroke={C.gateway} />
      <NodeBox x={520} y={160} w={150} h={56} title="Memory" sub="per-session store" stroke={C.memory} />
      <NodeBox x={690} y={160} w={170} h={56} title="Guardrail" sub="Bedrock policy" stroke={C.guardrail} />

      {/* gateway → mock lambdas */}
      <Arrow x1={425} y1={216} x2={425} y2={266} />
      <NodeBox
        x={300}
        y={266}
        w={250}
        h={54}
        title="3 mock Lambdas (synthetic)"
        sub="Portfolio · Market · Research"
        stroke={C.slate300}
        fill={C.slate50}
      />

      {/* governance / quality plane */}
      <rect
        x={30}
        y={366}
        width={830}
        height={84}
        rx={10}
        fill={C.slate50}
        stroke={C.slate200}
        strokeDasharray="5 4"
      />
      <text x={46} y={386} fontSize={11} fontWeight={700} fill={C.slate600}>
        GOVERNANCE &amp; QUALITY PLANE
      </text>
      <text x={200} y={386} fontSize={10} fontFamily="var(--font-mono)" fill={C.slate500}>
        read by the BFF · ADOT spans
      </text>

      <NodeBox x={60} y={398} w={230} h={40} title="Observability" sub="CloudWatch GenAI spans" stroke={C.slate300} titleColor={C.slate700} />
      <NodeBox x={335} y={398} w={230} h={40} title="Evaluation" sub="LLM-as-a-judge scores" stroke={C.slate300} />
      <NodeBox x={610} y={398} w={230} h={40} title="Registry" sub="catalog + approval" stroke={C.slate300} />

      {/* runtime → governance plane (ADOT, dashed = emitted/async) */}
      <Arrow x1={520} y1={90} x2={200} y2={398} label="ADOT" dashed />
    </svg>
  );
}
