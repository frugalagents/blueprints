# Multipage document processing

Two separate payslip solutions, with the same fictional 10-page test document.

| Folder | Pipeline | App |
| --- | --- | --- |
| qwen_vision | Page images → Qwen boundaries → grouped-image extraction | http://127.0.0.1:8765 |
| docling_qwen | Local Docling parsing → rules / Qwen fallback → grouped-text extraction | http://127.0.0.1:8766 |

## Quick start

Prerequisites: Python 3.12, AWS credentials (default chain or `AWS_PROFILE`) that
can invoke your Bedrock application inference profile for Qwen3-VL in us-east-1.
No credentials are included in this repo.

Pick a solution folder (`qwen_vision` or `docling_qwen`) and run:

```sh
cd docling_qwen                 # or: cd qwen_vision

# 1. Configure (once). .env is git-ignored; never commit it.
cp .env.example .env            # then set BEDROCK_INFERENCE_PROFILE in .env

# 2. Install (once)
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 3. Start the review app
sh app/start.sh
```

Then open the URL from the table above (8765 for `qwen_vision`, 8766 for
`docling_qwen`). To use another port: `PAYSLIP_PORT=8767 sh app/start.sh`. If
another app is already on the port, stop it first.

Settings in `.env` are read by both `app/start.sh` and the command-line scripts.
A real environment variable or `--inference-profile` takes precedence. To run a
pipeline without the app, see the Run section in each folder's README.

Model processing incurs normal Bedrock charges. Docling's first run downloads its
pretrained local parsing models (into `.cache/`, git-ignored).

## Verified synthetic sample

Both solutions grouped five payslips correctly and matched 45 expected values.
The image pipeline used 14 requests; the Docling pipeline used five text-only
requests with no boundary model calls. Real scans and 1,000-page jobs remain
untested. Each folder includes code, UI, sample, expected values and results.
