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

# 2. Start Notification Poller
echo "📬 Starting Notification Poller..."
mkdir -p logs
node scripts/notify-poller.mjs >> logs/poller.log 2>&1 &
POLLER_PID=$!

# 2. Start Main Server
echo "🧠 Starting Enterprise Server (Port 3001)..."
# We run this in the foreground so the user sees the logs and can Ctrl+C
npx tsx server/server.ts
