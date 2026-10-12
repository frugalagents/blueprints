#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOLUTION_DIR=$(dirname "$APP_DIR")
if [ ! -x "$SOLUTION_DIR/.venv/bin/python" ]; then
  printf '%s\n' 'Create a Python 3.12 environment first:' "  cd $SOLUTION_DIR" '  python3.12 -m venv .venv' '  .venv/bin/python -m pip install -r requirements.txt'
  exit 1
fi
export HF_HOME="$SOLUTION_DIR/.cache/huggingface"
export TORCH_HOME="$SOLUTION_DIR/.cache/torch"
export XDG_CACHE_HOME="$SOLUTION_DIR/.cache"
export AWS_SDK_UA_APP_ID=AWSSkill-Bedrock
if [ -f "$SOLUTION_DIR/.env" ]; then
  set -a
  . "$SOLUTION_DIR/.env"
  set +a
fi
exec "$SOLUTION_DIR/.venv/bin/python" "$APP_DIR/server.py"
