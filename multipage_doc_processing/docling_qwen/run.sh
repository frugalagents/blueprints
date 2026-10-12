#!/bin/sh
# Repeatable setup + launch for the Docling/Qwen payslip app.
# Usage: sh run.sh   (PAYSLIP_PORT=8767 sh run.sh to change port)
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.12 .venv
fi
if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
  uv pip install --python .venv/bin/python -r requirements.txt
  touch .venv/.deps-installed
fi
exec sh app/start.sh
