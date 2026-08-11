# Custom Agent Tokenomics — project conventions

This project is content-as-data. The published workshop (a single HTML file) is
*generated*, never hand-edited.

All Python code samples use the **Strands Agents SDK** (`strands-agents`,
`strands-agents-tools`) running on **Amazon Bedrock**, not raw `anthropic` SDK calls. Where
Strands ships a built-in for a pattern (conversation management, hooks, caching, agents-as-tools
routing), use it directly rather than hand-rolling the equivalent — check
`mcp__strands-agents__search_docs` / `fetch_doc` before asserting any Strands API exists.
Model IDs must be current Bedrock model cards — verify via WebFetch against
`docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-*` before citing an ID;
do not assume version suffixes are consistent across models (e.g. Haiku 4.5 keeps a versioned
suffix `-20251001-v1:0`, Sonnet 5 / Opus 5 do not).

## Layout

- `data/manifest.json` — ordered list of tab ids. Defines tab order and tier grouping.
- `data/tabs/<id>.json` — one file per tab. Schema below.
- `data/sources.json` — curated Tier A monitoring sources + Tier B discovery seed queries.
- `data/state/monitoring-state.json` — last-seen hash/date per Tier A source. Written by the
  monitoring pipeline, not by hand.
- `data/state/watchlist.json` — Tier B candidate patterns that haven't cleared the
  corroboration bar yet. Written by the discovery pipeline, not by hand.
- `drafts/` — proposed patches (new/updated tab JSON, or new candidate tab) awaiting human
  review. Nothing here is live content. Never copy a draft into `data/` without a human
  reading it first — every claim must carry a source before it ships.
- `site/template.html` — the static shell: CSS, tab-nav container, renderer JS. Never contains
  workshop content directly.
- `site/index.html` — generated output. Do not hand-edit; changes will be lost on next build.
- `scripts/build.mjs` — reads `data/manifest.json` + `data/tabs/*.json`, embeds them as JSON
  into `site/template.html`, writes `site/index.html`.

## Tab JSON schema

```
{
  "id": "kebab-case-slug",
  "title": "Human title",
  "tier": 0,                     // integer, defines build-order / complexity ladder position
  "tierLabel": "short phrase describing what this tier costs to adopt",
  "status": "stable" | "candidate" | "deprecated",
  "summary": "1-2 sentences, what this tab covers",
  "whyItMatters": "1-2 sentences, the cost mechanism this tab addresses",
  "concepts": [ { "heading": "...", "body": "..." } ],
  "exercises": [
    {
      "title": "...",
      "goal": "1 sentence",
      "steps": [ "Do X. → Expect: Y." ],
      "code": { "lang": "python", "content": "..." },
      "costImpact": "1-2 sentences, quantified where possible"
    }
  ],
  "sources": [
    { "title": "...", "url": "...", "dateChecked": "YYYY-MM-DD", "claim": "what this source supports" }
  ]
}
```

Global exercise numbers are NOT stored in the tab file — `build.mjs` assigns them
sequentially across tabs in manifest order at build time. Never hardcode an exercise number
inside `steps` or `code`.

## Monitoring workflow (Tier A — keeping content current)

`scripts/check-sources.mjs` fetches every source in `data/sources.json`'s `tierA_monitoring`
list, hashes the page text, and compares against `data/state/monitoring-state.json`. It is
mechanical and cheap (no LLM call) — it only tells you *something changed*, never *what to do
about it*.

Run it:

```
node scripts/check-sources.mjs
```

Output is one of three states per source: `unchanged` (nothing to do), `changed` (a source's
content differs from last check — could be a real doc update or just page noise on dynamic
pages), or `failed to fetch` (dead link or network issue — fix the URL in `data/sources.json`).

**When a source is flagged `changed`:**
1. `grep -l "<source-url>" data/tabs/*.json` to find which tab(s) cite it.
2. Fetch the source yourself (WebFetch or the browser) and read what actually changed.
3. If a cited claim is now stale/wrong: draft the fix as a new file under `drafts/` (same shape
   as the tab's JSON, just the changed fields), noting the new `dateChecked`. Do not edit
   `data/tabs/*.json` directly — see rule 1 below.
4. If nothing relevant changed (common on pages with dynamic elements — relative timestamps,
   view counters, GitHub release pages that reflow on every fetch): no action needed. The hash
   will keep flagging "changed" on such pages every run; this is a known false-positive source,
   not a bug to fix in the checker.
5. A human reviews and promotes anything in `drafts/` into `data/tabs/` when satisfied, then
   runs `node scripts/build.mjs`.

**Cadence:** run weekly (`data/sources.json` → `tierA_monitoring.cadenceCron`: `"0 9 * * 1"`,
i.e. Monday 9am). To automate the trigger itself, either:
- a durable `CronCreate` job in Claude Code that runs `node scripts/check-sources.mjs` and
  reports the output — note durable cron jobs only fire while a Claude Code session is open and
  idle, and recurring jobs auto-expire after 7 days and must be re-armed; or
- an OS-level `cron`/`launchd` entry invoking `node scripts/check-sources.mjs` (or
  `claude -p "run node scripts/check-sources.mjs and summarize"` headlessly) for a setup that
  survives the laptop being closed.

Tier B (discovery sweep for genuinely new named patterns not yet in this workshop's taxonomy) is
designed in `data/sources.json`'s `tierB_discovery` block but has no runnable script yet — it
needs an agent (not just a hash diff) to judge corroboration across sources, so it's a future
`Workflow`, not a follow-up to this checker.

## Rules for any agent (human or automated) touching this project

1. Never write directly to `data/tabs/*.json` from an automated pipeline. Automated output
   goes to `drafts/`. A human promotes a draft into `data/` after reading it.
2. Every claim in `concepts`/`exercises`/`costImpact` must be traceable to an entry in that
   tab's `sources` array. If you can't cite it, don't assert it — say "verify against current
   docs" instead of guessing.
3. After any change under `data/`, run `node scripts/build.mjs` and confirm it exits 0 before
   considering the change done.
4. Adding a new tab: add its id to `data/manifest.json`, add `data/tabs/<id>.json` matching the
   schema, rebuild. Do not touch `site/template.html` renderer logic for routine content
   additions — it is generic over the tab list.
