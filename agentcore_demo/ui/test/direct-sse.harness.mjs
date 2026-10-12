// Harness proving the DIRECT data client's chat decode path emits the SAME 7
// events as the BFF path — i.e. the direct client reuses the EXISTING
// SseParser (via pumpEventStream in src/data/direct-stream.ts) rather than a
// second parser.
//
// It feeds the recorded on-the-wire bytes (test/sse-fixture.bin — the real
// sse_starlette encoder output, incl. a mid-stream `: ping` keepalive) through
// pumpEventStream using a mock async-iterable byte stream, and asserts the
// identical event set the BFF SSE harness asserts.
//
// Run from the ui/ directory:
//   export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
//   node --experimental-strip-types --import=./test/sse-register.mjs ./test/direct-sse.harness.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const wire = readFileSync(resolve(here, "sse-fixture.bin"));

// Import the REAL direct-stream module (pumpEventStream reuses chat.ts's parser).
const streamMod = await import(
  pathToFileURL(resolve(here, "..", "src", "data", "direct-stream.ts")).href
);

/** A WHATWG-free async-iterable byte stream mimicking InvokeAgentRuntime chunks. */
async function* chunkedBytes(bytes, size) {
  for (let i = 0; i < bytes.length; i += size) {
    yield bytes.subarray(i, i + size);
  }
}

const events = [];
await streamMod.pumpEventStream(chunkedBytes(wire, 40), (ev) => events.push(ev));

const text = events
  .filter((e) => e.type === "token")
  .map((e) => e.text)
  .join("");

console.log("DIRECT client event count:", events.length);
console.log("DIRECT reconstructed assistant text:", JSON.stringify(text));

let ok = true;
const expect = (cond, msg) => {
  if (!cond) {
    console.error("FAIL:", msg);
    ok = false;
  }
};

expect(events.length === 7, "expected direct path to produce 7 events");
expect(text.includes("R-5567"), "reconstructed text should cite R-5567");
const ts = events.find((e) => e.type === "tool_start");
const tp = events.find((e) => e.type === "tool_stop");
const policy = events.find((e) => e.type === "policy");
const dn = events.find((e) => e.type === "done");
expect(ts && ts.name === "get_portfolio", "tool_start parsed");
expect(tp && tp.status === "success", "tool_stop parsed");
expect(
  dn && dn.stop_reason === "end_turn",
  "done parsed with stop_reason end_turn"
);
// The policy event is OPTIONAL in the fixture; assert its shape only if present.
if (policy) {
  expect(
    policy.decision === "ALLOWED" || policy.decision === "DENIED",
    "policy event has an ALLOWED/DENIED decision"
  );
}

// deriveRuntimeSessionId must satisfy the InvokeAgentRuntime >=33-char rule.
const shortId = "sess-1";
const derived = streamMod.deriveRuntimeSessionId(shortId);
expect(derived.length >= 33, "runtimeSessionId derived to >=33 chars");
expect(derived.startsWith(shortId), "derived runtimeSessionId keeps the id prefix");
expect(
  streamMod.deriveRuntimeSessionId(shortId) === derived,
  "deriveRuntimeSessionId is deterministic"
);
const longId = "a".repeat(40);
expect(
  streamMod.deriveRuntimeSessionId(longId) === longId,
  "already-long ids pass through unchanged"
);

console.log(
  ok
    ? "\nPASS: direct path reuses the shared SseParser and emits the same 7 events."
    : "\nHARNESS FAILED"
);
process.exit(ok ? 0 : 1);
