# Docling + Bedrock Qwen payslip POC

Separate implementation and results from the image-only POC.

```text
PDF → local Docling layout/OCR/table parsing
    → page Markdown and identifiers
    → local boundary rules
    → Qwen text comparison only for ambiguous boundaries
    → one text-only Qwen extraction request per payslip
    → evidence and arithmetic validation → JSON
```

No training. No PDF is sent to Bedrock. All pages are parsed locally; each
grouped payslip's text/tables are sent to Qwen through the existing application
profile. Boundaries are decided by identifier rules, then Docling layout
signals (`signals.py`: table continuation, missing title block, "continued"
markers), then Qwen on adjacent-page text, and only as a last resort Qwen on
the two page images (`--no-image-fallback` disables this).

Test continuation pages without identifiers: `samples/continuation-payslips.pdf`
with `samples/continuation-expected.json`.

Docling uses pretrained local parsing models. Its first run downloads those
models; it does not upload the PDF to a Docling cloud service.

## Review app

```sh
sh app/start.sh
```

Open http://127.0.0.1:8766. This is a separate app from the image-only version
on port 8765. It includes upload, source-page review, split/merge corrections,
re-extraction, JSON download, architecture, and actual processing-event history.
The sample uses `--no-ocr` because it has embedded text; uploaded PDFs enable
Docling OCR by default.

## Verified sample result

The clean 10-page synthetic PDF was grouped into five payslips with all 45
expected values matching. All nine boundaries were resolved by local rules:
five text-only Bedrock extraction calls, zero boundary model calls.
Recorded usage was 2,202 input tokens and 1,719 output tokens.

The image-only sample run used 14 calls and 31,671 input tokens. This is an
observed comparison on this synthetic sample, not a general accuracy or cost
benchmark. Docling default Markdown omitted page-number footers; retaining its
BODY and FURNITURE layers avoided extra boundary calls. An initial extraction
shortened a surname; explicit full-name copying instructions fixed the checked
sample, and labeled-name consistency is now also flagged during validation.

Real scans, more complex tables, diverse employers and 1,000-page jobs have
not been validated.

## Run

From this folder, with Python 3.12, configured AWS credentials and
`BEDROCK_INFERENCE_PROFILE` set in `.env` (copy `.env.example`) or via `--inference-profile`:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python pipeline.py synthetic-payslips.pdf \
  --expected expected.json --output results
```

Use `--parse-only` to parse and run local grouping rules without Bedrock calls.
`--no-ocr` is only for PDFs with usable embedded text.

## Outputs

`docling-document.json`: layout-aware parsed document.
`page-N.md`: per-page text and tables.
`anchors.json`: extracted grouping identifiers.
`boundaries.json`: every boundary's decision, evidence and method.
`groups.json`: original PDF page groups.
`records.json`: extracted records with source evidence and review flags.
`events.json`: actual processing events.
`report.json`: request/token counts and optional independent expected-value checks.

The rules recognize a small set of labels, YYYY-MM pay periods and explicit
statement numbering. Missing anchors trigger model fallback rather than a
blind merge. This is not a universal payroll classifier. Shuffled pages,
missing text, and uncertain model decisions require review.

Ground truth is used only after extraction, never in prompts or grouping rules.
Prompts and JSON helpers live in this folder. The original pipeline is not
modified or invoked.
