#!/bin/bash

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
RUN_DIR="$APP_DIR/.run"
BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"
APP_URL="http://127.0.0.1:4173"

cd "$APP_DIR"
mkdir -p "$RUN_DIR"

cleanup() {
  echo
  echo "Stopping Model Deployment Advisor..."
  if [ -n "${BACKEND_PID:-}" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [ -n "${FRONTEND_PID:-}" ]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but was not found."
  read -r -p "Press Enter to close..."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required but were not found."
  echo "Install Node.js, then run this file again."
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Creating the Python environment..."
  python3 -m venv "$VENV_DIR"
fi

echo "Installing backend dependencies..."
"$VENV_DIR/bin/pip" install -q -r backend/requirements.txt

if [ ! -d "$APP_DIR/node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi

export AWS_REGION="${AWS_REGION:-us-east-1}"
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-sonnet-4-6}"
export PYTHONPATH="$APP_DIR"

echo "Starting backend..."
"$VENV_DIR/bin/uvicorn" backend.main:app \
  --host 127.0.0.1 \
  --port 8000 >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

echo "Starting frontend..."
npm run dev -- --host 127.0.0.1 >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

echo "Waiting for the application..."
for attempt in {1..40}; do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1 &&
     curl -fsS "$APP_URL" >/dev/null 2>&1; then
    break
  fi

  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "The backend failed to start. See $BACKEND_LOG"
    tail -n 20 "$BACKEND_LOG"
    read -r -p "Press Enter to close..."
    exit 1
  fi

  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "The frontend failed to start. See $FRONTEND_LOG"
    tail -n 20 "$FRONTEND_LOG"
    read -r -p "Press Enter to close..."
    exit 1
  fi

  sleep 0.5
done

if ! curl -fsS "$APP_URL" >/dev/null 2>&1; then
  echo "The application did not become ready."
  echo "Backend log: $BACKEND_LOG"
  echo "Frontend log: $FRONTEND_LOG"
  read -r -p "Press Enter to close..."
  exit 1
fi

echo
echo "Model Deployment Advisor is running at:"
echo "$APP_URL"
echo
echo "Keep this window open. Press Control-C to stop the application."

open "$APP_URL"
wait
