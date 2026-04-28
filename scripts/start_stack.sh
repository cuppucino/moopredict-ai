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
echo "📬 Starting Poller (Python)..."
(
  python3 scripts/notify_poller.py >> logs/poller.log 2>&1
) &

# 2. Start Server
echo "🧠 Starting Server (Python)..."
python3 main.py
