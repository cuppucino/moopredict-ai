#!/bin/bash

# start_stack.sh
# Orchestrates the MooPredict-AI stack: Proxy -> Poller -> Server

echo "🚀 Starting MooPredict-AI Enterprise Stack..."

# Function to cleanup background processes on exit
cleanup_stack() {
    echo -e "\n🛑 Shutting down stack..."
    kill $(jobs -p) 2>/dev/null
    exit
}

trap cleanup_stack SIGINT SIGTERM

# 1. Kill any existing poller instances before starting a fresh one
echo "🧹 Clearing any existing poller processes..."
pkill -f "notify-poller.mjs" 2>/dev/null && sleep 1 || true

# 2. Start Notification Poller with auto-restart
echo "📬 Starting Notification Poller (auto-restart enabled)..."
mkdir -p logs
(
  while true; do
    node --max-old-space-size=512 scripts/notify-poller.mjs >> logs/poller.log 2>&1
    EXIT_CODE=$?
    # Exit code 0 = clean shutdown (Ctrl+C propagated), stop restarting
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ]; then
      break
    fi
    echo "[$(date '+%H:%M:%S')] Poller exited (code $EXIT_CODE), restarting in 5s..." >> logs/poller.log
    sleep 5
  done
) &
POLLER_PID=$!

# 2. Start Main Server
echo "🧠 Starting Enterprise Server (Port 3001)..."
# We run this in the foreground so the user sees the logs and can Ctrl+C
# Note: tsx respects NODE_OPTIONS for memory limits
NODE_OPTIONS="--max-old-space-size=2048" npx tsx server/server.ts
