// Direct-mode chat stream decoding — isolated from the AWS SDK + Vite-only
// config so it can be imported under plain Node (the SSE harness) exactly like
// ui/src/chat.ts. It reuses the EXISTING SseParser from ui/src/chat.ts so the
// emitted ChatEvents are IDENTICAL to the BFF path (no second parser).

import { SseParser, type ChatEvent } from "../chat";

/**
 * InvokeAgentRuntime requires runtimeSessionId to be at least 33 characters.
 * Chat session ids from the UI are short, so pad deterministically: keep the
 * original id as a prefix (so the backend still groups by it) and append a
 * stable filler derived from the id until the length requirement is met. The
 * same input always yields the same runtimeSessionId.
 */
export function deriveRuntimeSessionId(sessionId: string): string {
  const MIN = 33;
  if (sessionId.length >= MIN) return sessionId;
  let filler = "";
  let seed = sessionId || "session";
  while (sessionId.length + filler.length < MIN) {
    filler +=
      "_" + Array.from(seed).map((c) => c.charCodeAt(0).toString(36)).join("");
    seed = filler;
  }
  return (sessionId + filler).slice(0, Math.max(MIN, sessionId.length));
}

/**
 * Pump a response byte stream through the SHARED SseParser, invoking onEvent
 * for each parsed ChatEvent. Accepts either a WHATWG ReadableStream (browser,
 * via getReader()) or an async-iterable of Uint8Array chunks (Node / the test
 * harness). Exported so ui/test/direct-sse.harness.mjs can feed recorded wire
 * bytes through the EXACT decode path the live client uses.
 */
export async function pumpEventStream(
  stream: ReadableStream<Uint8Array> | AsyncIterable<Uint8Array>,
  onEvent: (ev: ChatEvent) => void
): Promise<void> {
  const parser = new SseParser();
  const decoder = new TextDecoder();

  const emit = (bytes: Uint8Array) => {
    const chunk = decoder.decode(bytes, { stream: true });
    for (const ev of parser.push(chunk)) onEvent(ev);
  };

  const maybeReadable = stream as ReadableStream<Uint8Array>;
  if (typeof maybeReadable.getReader === "function") {
    const reader = maybeReadable.getReader();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (value) emit(value);
    }
  } else {
    for await (const chunk of stream as AsyncIterable<Uint8Array>) {
      emit(chunk);
    }
  }
}
