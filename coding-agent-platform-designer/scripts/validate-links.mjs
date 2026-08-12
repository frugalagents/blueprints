#!/usr/bin/env node
// Walks every markdown link in knowledge/**/*.md and fails if any relative
// target doesn't resolve to a real file. OKF does not enforce link integrity
// itself (see ARCHITECTURE.md §2.7) — this is the project's own guard.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname, resolve, extname } from "node:path";

const ROOT = resolve(new URL("..", import.meta.url).pathname, "knowledge");
const LINK_RE = /\[[^\]]*\]\(([^)]+)\)/g;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (extname(entry) === ".md") out.push(full);
  }
  return out;
}

function isExternal(target) {
  return /^[a-z]+:\/\//i.test(target);
}

const files = walk(ROOT);
let brokenCount = 0;

for (const file of files) {
  const body = readFileSync(file, "utf8");
  for (const match of body.matchAll(LINK_RE)) {
    const target = match[1];
    if (isExternal(target)) continue; // external URLs are Sources, not graph edges
    const targetPath = resolve(dirname(file), target);
    try {
      statSync(targetPath);
    } catch {
      brokenCount++;
      console.error(`BROKEN LINK: ${file}\n  -> ${target}`);
    }
  }
}

if (brokenCount > 0) {
  console.error(`\n${brokenCount} broken link(s) found.`);
  process.exit(1);
}

console.log(`OK — ${files.length} file(s) checked, no broken links.`);
