import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";

import express from "express";
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";

const __dirname = dirname(fileURLToPath(import.meta.url));

// --- Config -----------------------------------------------------------------
// Lambda Web Functions set AWS_LAMBDA_HTTP_ENDPOINT to the host:port your
// server must listen on. Locally you set it yourself (see `npm run dev`).
const endpoint = process.env.AWS_LAMBDA_HTTP_ENDPOINT ?? "127.0.0.1:3000";
const [host, port] = endpoint.split(":");

// The ARN of the agent you deployed in AgentCore Runtime. Required to chat.
const AGENT_RUNTIME_ARN = process.env.AGENT_RUNTIME_ARN;
const REGION = process.env.AWS_REGION ?? "us-east-1";

const agentCore = new BedrockAgentCoreClient({ region: REGION });

// --- App --------------------------------------------------------------------
const app = express();
app.use(express.json());
app.use(express.static(join(__dirname, "public")));

// Liveness check.
app.get("/health", (_req, res) => {
  res.json({
    ok: true,
    agentConfigured: Boolean(AGENT_RUNTIME_ARN),
    region: REGION,
  });
});

// Serve the chat UI at the root.
app.get("/", (_req, res) => {
  res.sendFile(join(__dirname, "public", "chat.html"));
});

/**
 * POST /chat
 * Body: { message: string, sessionId?: string }
 *
 * Invokes the AgentCore Runtime agent and streams its response back to the
 * browser as Server-Sent Events (SSE). AgentCore itself returns a streaming
 * response; we forward each chunk through as it arrives.
 */
app.post("/chat", async (req, res) => {
  const { message, sessionId } = req.body ?? {};

  if (typeof message !== "string" || message.trim() === "") {
    res.status(400).json({ error: "Body must include a non-empty 'message' string." });
    return;
  }
  if (!AGENT_RUNTIME_ARN) {
    res.status(500).json({
      error:
        "AGENT_RUNTIME_ARN is not set. Point it at your AgentCore Runtime agent ARN.",
    });
    return;
  }

  // A stable session id keeps conversation context across turns in AgentCore.
  // AgentCore requires the session id to be at least 33 characters.
  const runtimeSessionId =
    typeof sessionId === "string" && sessionId.length >= 33
      ? sessionId
      : `${randomUUID()}${randomUUID()}`;

  // Set up an SSE response to the browser.
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders?.();

  // Tell the client which session we settled on (so it can reuse it).
  res.write(`event: session\ndata: ${JSON.stringify({ sessionId: runtimeSessionId })}\n\n`);

  const payload = new TextEncoder().encode(JSON.stringify({ prompt: message }));

  try {
    const command = new InvokeAgentRuntimeCommand({
      agentRuntimeArn: AGENT_RUNTIME_ARN,
      runtimeSessionId,
      payload,
    });

    const response = await agentCore.send(command);
    const contentType = response.contentType ?? "";

    if (contentType.includes("text/event-stream")) {
      // AgentCore is streaming SSE. Decode and forward each `data:` line.
      const decoder = new TextDecoder();
      let buffer = "";

      for await (const event of response.response) {
        buffer += decoder.decode(event, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? ""; // keep incomplete line for next chunk

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data:")) {
            const raw = trimmed.slice(5).trim();
            // AgentCore wraps each chunk as a JSON value (often a JSON string).
            // Unwrap one layer so the browser receives plain text, not quotes.
            let text = raw;
            try {
              const parsed = JSON.parse(raw);
              if (typeof parsed === "string") text = parsed;
            } catch {
              // not JSON — forward as-is
            }
            res.write(`event: token\ndata: ${JSON.stringify(text)}\n\n`);
          }
        }
      }
    } else {
      // Non-streaming (application/json or other): collect and send once.
      const decoder = new TextDecoder();
      let text = "";
      for await (const chunk of response.response) {
        text += decoder.decode(chunk, { stream: true });
      }
      res.write(`event: token\ndata: ${JSON.stringify(text)}\n\n`);
    }

    res.write(`event: done\ndata: ${JSON.stringify({ ok: true })}\n\n`);
  } catch (err) {
    console.error("InvokeAgentRuntime failed:", err);
    res.write(
      `event: error\ndata: ${JSON.stringify({
        name: "AgentInvocationError",
        message: "The agent request failed. Check the server logs for details.",
      })}\n\n`,
    );
  } finally {
    res.end();
  }
});

app.listen(Number(port), host, () => {
  console.log(`Web Function listening on http://${host}:${port}`);
  console.log(`AgentCore region: ${REGION}`);
  console.log(
    AGENT_RUNTIME_ARN
      ? "Agent runtime ARN configured"
      : "WARNING: AGENT_RUNTIME_ARN is not set — /chat will return 500 until you set it.",
  );
});
