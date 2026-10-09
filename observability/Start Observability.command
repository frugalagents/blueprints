#!/bin/bash
# Double-click in Finder, or run this file from any Terminal directory.
set -u
PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1
DEMO_PYTHON="$PROJECT_DIR/.runtime/checkpoint1/.venv/bin/python"
if [ ! -x "$DEMO_PYTHON" ]; then
  echo "One-time setup is required. In Terminal run:"
  echo "  cd \"$PROJECT_DIR\""
  echo "  python3.14 scripts/setup.py"
  read -r -p "Press Enter to close... "
  exit 1
fi
"$DEMO_PYTHON" "$PROJECT_DIR/scripts/run.py" --open-browser
START_RESULT=$?
if [ "$START_RESULT" -ne 0 ] && [ "$START_RESULT" -ne 130 ]; then
  echo "Startup failed. Check the message above."
  read -r -p "Press Enter to close... "
fi
exit "$START_RESULT"
