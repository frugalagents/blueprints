#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Enterprise API Knowledge Graph ==="
echo ""

# Kill any existing processes
echo "Stopping existing processes..."
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:9000 | xargs kill -9 2>/dev/null || true
lsof -ti:3000 | xargs kill -9 2>/dev/null || true
pkill -f "uvicorn.*mock" 2>/dev/null || true
pkill -f "uvicorn.*api_kg.server" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "streamlit" 2>/dev/null || true
sleep 2
echo "  Done."
echo ""

# Start mock API server
echo "Starting mock API server on :8080..."
python3 -m api_kg.cli mock --specs-dir ./specs/sample_hcm --fixtures-dir ./fixtures/sample_hcm --port 8080 &
MOCK_PID=$!
sleep 2

# Verify mock is up
if ! curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "  ERROR: Mock server failed to start"
    exit 1
fi
echo "  Mock server running (PID $MOCK_PID)"
echo ""

# Start backend API
echo "Starting backend API on :9000..."
python3 -m uvicorn api_kg.server.api:app --port 9000 &
BACKEND_PID=$!
sleep 2

if ! curl -s http://localhost:9000/api/stats > /dev/null 2>&1; then
    echo "  ERROR: Backend API failed to start"
    exit 1
fi
echo "  Backend API running (PID $BACKEND_PID)"
echo ""

# Start Next.js frontend
echo "Starting frontend on :3000..."
cd web
npm run dev &
FRONTEND_PID=$!
cd ..
sleep 4
echo "  Frontend running (PID $FRONTEND_PID)"
echo ""

echo "=== All services running ==="
echo ""
echo "  Frontend:    http://localhost:3000"
echo "  Backend API: http://localhost:9000"
echo "  Mock APIs:   http://localhost:8080"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

# Trap Ctrl+C to kill all children
trap 'echo ""; echo "Shutting down..."; kill $MOCK_PID $BACKEND_PID $FRONTEND_PID 2>/dev/null; wait; echo "Done."' INT TERM

# Wait for any child to exit
wait
