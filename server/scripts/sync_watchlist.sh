#!/bin/bash
# Watchlist Sync Script
# Syncs database between OpenClaw (Telegram) and Mac (Frontend)

echo "🔄 Syncing watchlist databases..."

# Source (OpenClaw - has all 6 stocks)
SOURCE_DB="/home/node/.openclaw/workspace/moopredict-ai/server/data/moopredict.db"
SOURCE_WAL="/home/node/.openclaw/workspace/moopredict-ai/server/data/moopredict.db-wal"
SOURCE_SHM="/home/node/.openclaw/workspace/moopredict-ai/server/data/moopredict.db-shm"

# Destination (Mac)
DEST_DIR="/Users/$(whoami)/moopredict-ai/server/data"
DEST_DB="$DEST_DIR/moopredict.db"

# Backup current Mac database
echo "📦 Backing up current Mac database..."
if [ -f "$DEST_DB" ]; then
    cp "$DEST_DB" "$DEST_DB.backup.$(date +%Y%m%d_%H%M%S)"
    echo "✅ Backup created"
fi

# Stop any running server first
echo "🛑 Stopping any running server..."
kill $(lsof -t -i:3001) 2>/dev/null

# Copy database files
echo "📤 Copying from OpenClaw..."
cp "$SOURCE_DB" "$DEST_DIR/"

# Only copy WAL if it exists
if [ -f "$SOURCE_WAL" ]; then
    cp "$SOURCE_WAL" "$DEST_DIR/"
fi

if [ -f "$SOURCE_SHM" ]; then
    cp "$SOURCE_SHM" "$DEST_DIR/"
fi

echo "✅ Sync complete!"
echo ""
echo "Next steps:"
echo "  1. npm run dev:server"
echo "  2. Refresh browser"
echo ""
echo "Both Telegram and Frontend now use the same database!"
