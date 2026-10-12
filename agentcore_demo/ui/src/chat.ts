// SSE chat client for the Advisor Copilot UI (task 3.3).
//
// `/api/chat` is a POST that streams Server-Sent Events, but the browser's
// native EventSource API is GET-only. So we POST with fetch and parse the
// `event:`/`data:` frames off the ReadableStream ourselves. Frame shapes match
// the BFF (bff/main.py), which mirror agent/app.py:
//   event: token      data: {"text": "<delta>"}
//   event: tool_start data: {"toolUseId","name"}
//   event: tool_stop  data: {"toolUseId","name","status","result"}
//   event: policy     data: {"decision","principal","resource"}
//   event: done       data: {"stop_reason","message"}
//   event: error      data: {"message"}
//
// WIRE FORMAT NOTE (root cause of the "no chat output" bug): the BFF uses
// sse_starlette's EventSourceResponse, which encodes every frame with CRLF
// (`\r\n`) line endings and separates frames with a BLANK CRLF line, i.e.
// `\r\n\r\n` — NOT `\n\n`. It also emits periodic keepalive COMMENT frames
// (a line beginning with `:` such as `: ping - <ts>`). An earlier parser split
// frames on `"\n\n"`, which never occurs in a `\r\n\r\n`-delimited stream, so
// no frame was ever emitted and the Chat tab stayed blank. The parser below
// normalizes line endings and ignores comment lines per the SSE spec.

import { BFF_BASE_URL } from "./api";

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "tool_start"; toolUseId: string; name: string }
  | { type: "tool_stop"; toolUseId: string; name: string; status: string; result: unknown }
  | { type: "policy"; decision: "ALLOWED" | "DENIED"; principal: string; resource: string }
  | { type: "done"; stop_reason: string | null; message: string }
  | { type: "error"; message: string };

/**
 * Stateful SSE frame parser. Feed it decoded text chunks; it returns the fully
 * parsed events contained in each chunk and keeps any partial trailing frame
 * buffered for the next `push`. Exported for unit testing against real bytes.
 */
export class SseParser {
  private buffer = "";

  /** Feed a decoded text chunk, returning any complete events it produced. */
  push(chunk: string): ChatEvent[] {
    // Normalize CRLF / CR to LF so frame + line splitting is separator-agnostic
    // regardless of whether the server uses `\r\n` (sse_starlette default),
    // `\r`, or `\n`.
    this.buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    const events: ChatEvent[] = [];
    let sep: number;
    // Frames are separated by a blank line (`\n\n` after normalization).
    while ((sep = this.buffer.indexOf("\n\n")) !== -1) {
      const rawFrame = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + 2);
      const parsed = parseFrame(rawFrame);
      if (parsed) events.push(parsed);
    }
    return events;
  }
}

/**
 * POST a chat turn and invoke `onEvent` for each parsed SSE frame.
 * Resolves when the stream ends. Pass an AbortSignal to cancel.
 */
export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BFF_BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ sessionId, message }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`chat request failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    for (const ev of parser.push(chunk)) onEvent(ev);
  }
}

/**
 * Parse one SSE frame (lines already normalized to LF). Returns null for frames
 * with no event name — this includes keepalive comment frames (lines starting
 * with `:`), which carry no `event:` line and are correctly ignored.
 */
export function parseFrame(raw: string): ChatEvent | null {
  let eventName = "";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    // Comment / keepalive lines begin with a colon (SSE spec) — ignore them.
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      eventName = stripField(line, "event:");
    } else if (line.startsWith("data:")) {
      dataLines.push(stripField(line, "data:"));
    }
    // `id:` / `retry:` and any other fields are ignored.
  }
  if (!eventName) return null;

  let data: Record<string, unknown> = {};
  if (dataLines.length) {
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return null;
    }
  }

  switch (eventName) {
    case "token":
      return { type: "token", text: String(data.text ?? "") };
    case "tool_start":
      return {
        type: "tool_start",
        toolUseId: String(data.toolUseId ?? ""),
        name: String(data.name ?? ""),
      };
    case "tool_stop":
      return {
        type: "tool_stop",
        toolUseId: String(data.toolUseId ?? ""),
        name: String(data.name ?? ""),
        status: String(data.status ?? ""),
        result: data.result,
      };
    case "policy":
      return {
        type: "policy",
        decision: data.decision === "DENIED" ? "DENIED" : "ALLOWED",
        principal: String(data.principal ?? ""),
        resource: String(data.resource ?? ""),
      };
    case "done":
      return {
        type: "done",
        stop_reason: (data.stop_reason as string | null) ?? null,
        message: String(data.message ?? ""),
      };
    case "error":
      return { type: "error", message: String(data.message ?? "") };
    default:
      return null;
  }
}

/**
 * Strip an SSE field prefix and the single optional leading space the spec
 * allows after the colon (sse_starlette emits `event: token`, with a space).
 */
function stripField(line: string, prefix: string): string {
  let value = line.slice(prefix.length);
  if (value.startsWith(" ")) value = value.slice(1);
  return value;
}
