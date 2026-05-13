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
  ./venv/bin/python3 scripts/notify_poller.py >> logs/poller.log 2>&1
) &

# 2. Start Prediction Resolver
echo "⚖️  Starting Prediction Resolver..."
(
  ./venv/bin/python3 scripts/prediction_resolver.py >> logs/resolver.log 2>&1
) &

# 3. Start Discovery Proxy (for Docker/Network access)
echo "🛰️ Starting Discovery Proxy..."
(
  ./venv/bin/python3 scripts/discovery_proxy.py >> logs/discovery.log 2>&1
) &

# 4. Start Server
echo "🧠 Starting Server (Python)..."
export PYTHONPATH=$PYTHONPATH:.
./venv/bin/python3 main.py
