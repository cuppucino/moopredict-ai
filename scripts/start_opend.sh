#!/bin/bash

# Load .env safely
set -a
source .env
set +a

OPEND_DIR="/Users/admin/moopredict-ai/moomoo_OpenD_10.4.6408_Mac/moomoo_OpenD_10.4.6408_Mac/OpenD.app/Contents/MacOS"
OPEND_BIN="$OPEND_DIR/OpenD"
CONFIG_FILE="/Users/admin/moopredict-ai/moomoo_OpenD_10.4.6408_Mac/moomoo_OpenD_10.4.6408_Mac/OpenD.xml"

echo "🚀 Starting Moomoo OpenD (v10.4) in Headless Mode..."
echo "📂 Directory: $OPEND_DIR"

if [ ! -f "$OPEND_BIN" ]; then
  echo "❌ Error: OpenD binary not found at $OPEND_BIN"
  exit 1
fi

# Run OpenD with credentials and explicit config
# Note: Using & to run in background
cd "$(dirname "$CONFIG_FILE")" && "$OPEND_BIN" -login_account="$MOOMOO_ACCOUNT" -login_pwd_md5="$MOOMOO_PASSWORD_MD5" -config=OpenD.xml > /Users/admin/moopredict-ai/opend.log 2>&1 &

echo "✅ OpenD started in background. Waiting for initialization..."
sleep 5
echo "🛰️ Done. You can now start the main server."
