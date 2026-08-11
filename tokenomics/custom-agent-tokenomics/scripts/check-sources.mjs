#!/usr/bin/env node
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCES_PATH = join(ROOT, "data", "sources.json");
const STATE_PATH = join(ROOT, "data", "state", "monitoring-state.json");

function loadJSON(path, fallback) {
  if (!existsSync(path)) return fallback;
  return JSON.parse(readFileSync(path, "utf-8"));
}

function normalize(text) {
  // Strip whitespace differences so trivial re-renders don't count as changes.
  return text.replace(/\s+/g, " ").trim();
}

function hashOf(text) {
  return createHash("sha256").update(normalize(text)).digest("hex");
}

async function fetchText(url) {
  const res = await fetch(url, { headers: { "User-Agent": "tokenomics-workshop-monitor/1.0" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.text();
}

async function main() {
  const sources = loadJSON(SOURCES_PATH, null);
  if (!sources) throw new Error(`Missing ${SOURCES_PATH}`);
  const state = loadJSON(STATE_PATH, {});

  const tierA = sources.tierA_monitoring?.sources ?? [];
  const changed = [];
  const unchanged = [];
  const failed = [];

  for (const source of tierA) {
    try {
      const text = await fetchText(source.url);
      const hash = hashOf(text);
      const prev = state[source.id];

      if (!prev) {
        state[source.id] = { hash, lastChecked: new Date().toISOString(), lastChanged: null };
        changed.push({ ...source, reason: "first check (no prior snapshot)" });
      } else if (prev.hash !== hash) {
        state[source.id] = {
          hash,
          lastChecked: new Date().toISOString(),
          lastChanged: new Date().toISOString(),
        };
        changed.push({ ...source, reason: "content hash changed since last check" });
      } else {
        state[source.id].lastChecked = new Date().toISOString();
        unchanged.push(source);
      }
    } catch (err) {
      failed.push({ ...source, error: err.message });
    }
  }

  writeFileSync(STATE_PATH, JSON.stringify(state, null, 2) + "\n", "utf-8");

  console.log(`Checked ${tierA.length} Tier A sources.`);
  console.log(`  ${changed.length} changed, ${unchanged.length} unchanged, ${failed.length} failed to fetch.\n`);

  if (changed.length) {
    console.log("CHANGED — needs human/agent review before touching data/tabs/:");
    for (const c of changed) {
      console.log(`  - [${c.id}] ${c.title} (${c.reason})`);
      console.log(`    ${c.url}`);
    }
    console.log();
  }

  if (failed.length) {
    console.log("FAILED TO FETCH — check manually:");
    for (const f of failed) {
      console.log(`  - [${f.id}] ${f.title}: ${f.error}`);
    }
    console.log();
  }

  if (changed.length === 0 && failed.length === 0) {
    console.log("No changes detected. Nothing to review.");
  } else {
    console.log("Next step: for each changed source, review it against the tab(s) that cite it");
    console.log("(grep data/tabs/*.json for the source URL) and draft an update into drafts/ if warranted.");
    console.log("See CLAUDE.md 'Monitoring workflow' section.");
  }
}

main().catch((err) => {
  console.error("check-sources failed:", err);
  process.exit(1);
});
