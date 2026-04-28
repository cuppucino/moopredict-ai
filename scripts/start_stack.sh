#!/bin/bash

# start_stack.sh (Simplified)
echo "🚀 Starting MooPredict REBUILD Stack..."

# Cleanup on exit
cleanup() {
    echo -e "\n🛑 Shutting down..."
    kill $(jobs -p) 2>/dev/null
    exit
}
trap cleanup SIGINT SIGTERM

# 1. Start Notification Poller
echo "📬 Starting Poller..."
(
  while true; do
    node scripts/notify-poller.mjs >> logs/poller.log 2>&1
    sleep 5
  done
) &

# 2. Start Server
echo "🧠 Starting Server..."
npx tsx server/server.ts
