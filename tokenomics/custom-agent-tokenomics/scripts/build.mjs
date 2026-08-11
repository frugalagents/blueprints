#!/usr/bin/env node
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DATA_DIR = join(ROOT, "data");
const TABS_DIR = join(DATA_DIR, "tabs");
const TEMPLATE_PATH = join(ROOT, "site", "template.html");
const OUTPUT_PATH = join(ROOT, "site", "index.html");

function loadJSON(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function main() {
  const manifest = loadJSON(join(DATA_DIR, "manifest.json"));
  const availableTabFiles = new Set(
    readdirSync(TABS_DIR).filter(f => f.endsWith(".json")).map(f => f.replace(/\.json$/, ""))
  );

  const missing = manifest.tabs.filter(id => !availableTabFiles.has(id));
  if (missing.length) {
    throw new Error(`manifest.json references tabs with no data file: ${missing.join(", ")}`);
  }

  const tabs = manifest.tabs.map(id => loadJSON(join(TABS_DIR, `${id}.json`)));

  // Validate tab shape and ordering
  const seenIds = new Set();
  for (const tab of tabs) {
    if (!tab.id || !tab.title || tab.tier === undefined) {
      throw new Error(`Tab missing required fields (id/title/tier): ${JSON.stringify(tab).slice(0, 120)}`);
    }
    if (seenIds.has(tab.id)) {
      throw new Error(`Duplicate tab id: ${tab.id}`);
    }
    seenIds.add(tab.id);
    for (const ex of tab.exercises || []) {
      if (!ex.title || !ex.goal || !ex.steps) {
        throw new Error(`Tab '${tab.id}' has an exercise missing title/goal/steps: ${ex.title || "(untitled)"}`);
      }
      for (const source of tab.sources || []) {
        if (!source.url || !source.title || !source.claim) {
          throw new Error(`Tab '${tab.id}' has a source missing url/title/claim.`);
        }
      }
    }
  }

  // Assign global exercise numbers in manifest order
  let counter = 0;
  for (const tab of tabs) {
    for (const ex of tab.exercises || []) {
      counter += 1;
      ex.globalNum = counter;
    }
  }

  const payload = {
    title: manifest.title,
    subtitle: manifest.subtitle,
    tabs,
  };

  const template = readFileSync(TEMPLATE_PATH, "utf-8");
  const output = template
    .replace("__SITE_TITLE__", escapeHtml(manifest.title))
    .replace("__WORKSHOP_DATA__", JSON.stringify(payload).replace(/</g, "\\u003c"));

  writeFileSync(OUTPUT_PATH, output, "utf-8");

  console.log(`Built ${OUTPUT_PATH}`);
  console.log(`  ${tabs.length} tabs, ${counter} exercises, ${tabs.reduce((n, t) => n + (t.sources || []).length, 0)} cited sources.`);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

main();
