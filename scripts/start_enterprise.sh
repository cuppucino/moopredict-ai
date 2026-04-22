#!/bin/bash

# MooPredict Enterprise Bootstrap Script
# --------------------------------------

echo "🚀 Initializing MooPredict-AI Enterprise Stack..."
export PATH="/Users/admin/.nvm/versions/node/v24.11.1/bin:$PATH"

# 0. Clean up existing processes on 3000 and 3001 to prevent EADDRINUSE
echo "🧹 Cleaning up existing port processes (3000, 3001)..."
lsof -ti:3000,3001 | xargs kill -9 > /dev/null 2>&1
sleep 3

# 1. Startup Postgres (via Brew if exists)
if [ -f "/opt/homebrew/bin/brew" ]; then
    echo "🐘 Starting PostgreSQL..."
    /opt/homebrew/bin/brew services start postgresql > /dev/null 2>&1
elif [ -f "/usr/local/bin/brew" ]; then
    echo "🐘 Starting PostgreSQL..."
    /usr/local/bin/brew services start postgresql > /dev/null 2>&1
fi

# 2. Open Moomoo OpenD (GUI App)
if [ -d "/Applications/moomoo_OpenD.app" ]; then
    echo "📈 Opening moomoo_OpenD..."
    open -a "moomoo_OpenD"
else
    echo "⚠️  moomoo_OpenD.app not found in /Applications"
fi

# 3. Start Ollama in background
OLLAMA_PATH="/usr/local/bin/ollama"
if [ -f "$OLLAMA_PATH" ]; then
    echo "🧠 Starting Ollama Inference Engine..."
    $OLLAMA_PATH serve > /dev/null 2>&1 &
elif command -v ollama &> /dev/null; then
    echo "🧠 Starting Ollama Inference Engine..."
    ollama serve > /dev/null 2>&1 &
fi

# 4. Wait for dependencies to initialize
echo "⏳ Waiting for services to warm up (5s)..."
sleep 5

# 5. Start Python ML Service (Foundation Models)
echo "🧬 Starting ML Prediction Service (Chronos + TFT)..."
source venv/bin/activate
nohup python ml_service.py >> logs/ml_service.log 2>&1 &
deactivate

# 6. Start Notification Poller in background
echo "📬 Starting Notification Poller..."
/Users/admin/.nvm/versions/node/v24.11.1/bin/node scripts/notify-poller.mjs >> logs/poller-out.log 2>&1 &

# 6. Launch Project
echo "📡 Launching MooPredict Dashboard..."
/Users/admin/.nvm/versions/node/v24.11.1/bin/npm run dev:all
