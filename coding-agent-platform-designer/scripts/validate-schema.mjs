#!/usr/bin/env node
// Checks every knowledge/**/*.md file's frontmatter and section shape against
// the schema in CLAUDE.md. Catches the class of mistake found in Phase 1's
// manual review (inconsistent `status`, missing sources) automatically.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname, relative, resolve } from "node:path";

const ROOT = resolve(new URL("..", import.meta.url).pathname, "knowledge");
const VALID_TYPES = ["platform-component", "platform-component-group"];
const VALID_STATUS = ["stable", "candidate", "deprecated"];
const REQUIRED_COMPONENT_FIELDS = [
  "type", "title", "description", "group", "tags", "timestamp", "status",
];
const URL_RE = /https?:\/\/\S+/;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (extname(entry) === ".md") out.push(full);
  }
  return out;
}

function parseFrontmatter(body) {
  const match = body.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) return null;
  const [, yamlBlock, rest] = match;
  const fields = {};
  for (const line of yamlBlock.split("\n")) {
    const m = line.match(/^([a-zA-Z]+):\s*(.*)$/);
    if (m) fields[m[1]] = m[2].trim();
  }
  return { fields, rest };
}

const files = walk(ROOT);
const errors = [];
const warnings = [];

for (const file of files) {
  const rel = relative(ROOT, file);
  const body = readFileSync(file, "utf8");
  const parsed = parseFrontmatter(body);

  if (!parsed) {
    errors.push(`${rel}: no valid YAML frontmatter block`);
    continue;
  }

  const { fields, rest } = parsed;

  if (!fields.type) {
    errors.push(`${rel}: missing required field 'type'`);
  } else if (!VALID_TYPES.includes(fields.type)) {
    errors.push(`${rel}: invalid type '${fields.type}' (expected one of ${VALID_TYPES.join(", ")})`);
  }

  if (fields.type === "platform-component") {
    for (const key of REQUIRED_COMPONENT_FIELDS) {
      if (!fields[key]) errors.push(`${rel}: missing required field '${key}'`);
    }
    if (fields.status && !VALID_STATUS.includes(fields.status)) {
      errors.push(`${rel}: invalid status '${fields.status}' (expected one of ${VALID_STATUS.join(", ")})`);
    }

    const hasSources = /^## Sources/m.test(rest);
    if (!hasSources) {
      errors.push(`${rel}: missing '## Sources' section`);
    } else if (fields.status === "stable" || fields.status === "candidate") {
      const sourcesSection = rest.split(/^## Sources/m)[1] ?? "";
      if (!URL_RE.test(sourcesSection)) {
        const explicitlyUnsourced = /verify against current docs/i.test(sourcesSection);
        if (!explicitlyUnsourced) {
          warnings.push(`${rel}: status '${fields.status}' but '## Sources' has no URL and no explicit "verify against current docs" note`);
        }
      }
    }

    if (!/^## Decisions/m.test(rest)) {
      warnings.push(`${rel}: no '## Decisions' section`);
    }
  }
}

for (const w of warnings) console.warn(`WARN: ${w}`);
for (const e of errors) console.error(`ERROR: ${e}`);

console.log(`\n${files.length} file(s) checked — ${errors.length} error(s), ${warnings.length} warning(s).`);
if (errors.length > 0) process.exit(1);
