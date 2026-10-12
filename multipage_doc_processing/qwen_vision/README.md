# Multipage payslip POC

## Local review interface

Start the app with:

```sh
sh app/start.sh
```

Open http://127.0.0.1:8765. It loads the tested sample and shows each payslip's
pages beside its extracted fields. Click an amount to jump to its source page.
You can upload a PDF, process it with Bedrock, split or merge groups, re-extract
reviewed groups, and download JSON. Uploads remain local until you click process.

The processing timeline polls actual run events every 1.5 seconds. It shows
local page rendering, page numbers in each Bedrock request, responses with
latency and token usage, boundary decisions and their reported evidence,
assembled groups, validation, and completion. It does not expose internal model
reasoning or simulate events. Completed history remains visible for the run.

The interface is a local proof of concept. The server holds its job list in
memory, so uploaded jobs are not restored automatically after a restart.
It uses the default AWS SDK credential chain; configure a credential profile
via `AWS_PROFILE` when required. Processing is sequential and has a one-hour
timeout, so this version has not been validated for 1,000-page production jobs.

No training, Docling, or OCR service is used. The script renders PDF pages locally
and sends images to Qwen3-VL through the configured Bedrock application inference
profile in us-east-1.

The fictional sample contains five statements on pages 1–2, 3–5, 6, 7–8, and
9–10. It includes three presentation styles, separate earnings and deduction
pages, year-to-date distractors, and the same employee in two pay periods.
The layouts are simple and clean; this is a plumbing test, not a benchmark
for noisy scans or unseen real payroll documents.

## Run

Use Python 3.12 with AWS credentials that allow invocation of the profile, and
set `BEDROCK_INFERENCE_PROFILE` in `.env` (copy `.env.example`) or pass `--inference-profile`.

```sh
python -m pip install -r requirements.txt
python create_sample.py
python payslip_poc.py synthetic-payslips.pdf \
  --expected expected.json --output results
```

Pass `--aws-profile NAME` for a named local AWS credential profile. Override
`--region` when needed.

To process your own PDF, omit `--expected`. The expected file is used only by the
final evaluation and is never included in model prompts.

## Requests and review

For ten pages, boundary detection makes nine requests containing two adjacent
pages each. Extraction makes one request per accepted payslip group. The sample
therefore uses fourteen requests with at most three page images in a request.

Original PDF page numbers and supporting text are requested for extracted fields.
Uncertain boundaries stop extraction for review. Missing evidence, nonnumeric
totals, and gross-minus-deductions arithmetic discrepancies flag records.
Evidence text is model-reported, not independently verified OCR.

The POC assumes consecutive, correctly ordered pages. It does not handle shuffled
pages, overlapping statements, blank-page removal, or groups over eight pages.
Amounts extracted here are totals, not individual earnings and deduction rows.
Arithmetic may need employer-specific adjustments on real documents.

`results/records.json` contains extracted records. `results/report.json` contains
ground-truth checks and token usage. Raw responses and boundary decisions are
also saved for inspection. Invocations incur normal Bedrock usage charges.
