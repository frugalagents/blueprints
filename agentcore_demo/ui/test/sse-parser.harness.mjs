// Harness proving the Problem-1 root cause and fix for the Chat SSE consumer.
//
// It feeds the REAL on-the-wire bytes produced by sse_starlette's encoder
// (test/sse-fixture.bin — the same encoder bff/main.py uses, including a
// mid-stream `: ping` keepalive comment) through:
//   (1) the OLD parser logic (split on "\n\n") -> expected 0 events (the bug),
//   (2) the REAL current parser imported from src/chat.ts -> all 7 events.
//
// Run from the ui/ directory:
//   export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
//   node --experimental-strip-types --import=./test/sse-register.mjs ./test/sse-parser.harness.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const wire = readFileSync(resolve(here, "sse-fixture.bin"));

// ---- OLD parser (verbatim logic from the pre-fix chat.ts) ----------------
function oldParseFrame(raw) {
  let eventName = "";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!eventName) return null;
  let data = {};
  if (dataLines.length) {
    try { data = JSON.parse(dataLines.join("\n")); } catch { return null; }
  }
  return { eventName, data };
}
function runOld(bytes) {
  const decoder = new TextDecoder();
  let buffer = "";
  const events = [];
  for (let i = 0; i < bytes.length; i += 40) {
    buffer += decoder.decode(bytes.subarray(i, i + 40), { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const p = oldParseFrame(raw);
      if (p) events.push(p);
    }
  }
  return events;
}

// ---- NEW parser (imported from the REAL source) -------------------------
const chatMod = await import(
  pathToFileURL(resolve(here, "..", "src", "chat.ts")).href
);
function runNew(bytes) {
  const parser = new chatMod.SseParser();
  const decoder = new TextDecoder();
  const events = [];
  for (let i = 0; i < bytes.length; i += 40) {
    const chunk = decoder.decode(bytes.subarray(i, i + 40), { stream: true });
    for (const ev of parser.push(chunk)) events.push(ev);
  }
  return events;
}

const oldEvents = runOld(wire);
const newEvents = runNew(wire);
const text = newEvents.filter((e) => e.type === "token").map((e) => e.text).join("");

console.log("OLD parser event count:", oldEvents.length);
console.log("NEW parser event count:", newEvents.length);
console.log("NEW reconstructed assistant text:", JSON.stringify(text));

let ok = true;
const expect = (cond, msg) => { if (!cond) { console.error("FAIL:", msg); ok = false; } };
expect(oldEvents.length === 0, "expected OLD parser to produce 0 events (bug)");
expect(newEvents.length === 7, "expected NEW parser to produce 7 events");
expect(text.includes("R-5567"), "reconstructed text should cite R-5567");
const ts = newEvents.find((e) => e.type === "tool_start");
const tp = newEvents.find((e) => e.type === "tool_stop");
const dn = newEvents.find((e) => e.type === "done");
expect(ts && ts.name === "get_portfolio", "tool_start parsed");
expect(tp && tp.status === "success", "tool_stop parsed");
expect(dn && dn.stop_reason === "end_turn", "done parsed");

console.log(ok
  ? "\nPASS: old=0 events (bug reproduced), new parses all frames correctly."
  : "\nHARNESS FAILED");
process.exit(ok ? 0 : 1);
