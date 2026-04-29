#!/bin/bash

# Configuration
IP_ADDR="192.168.0.33"
PORT="3001"
BASE_URL="http://$IP_ADDR:$PORT"

# Define cron jobs
CRON_OPEN="30 13 * * 1-5 curl -s -X POST $BASE_URL/api/notify -H 'Content-Type: application/json' -d '{\"message\": \"🚨 Market opens in 30 minutes!\", \"level\": \"alert\"}' > /dev/null"
CRON_CLOSE="0 20 * * 1-5 curl -s -X POST $BASE_URL/api/notify -H 'Content-Type: application/json' -d '{\"message\": \"📊 Market closed. Generating report...\", \"level\": \"info\"}' > /dev/null"
CRON_CHECK="0 14-21 * * 1-5 curl -s -X POST $BASE_URL/api/alerts/check > /dev/null"

# Get current crontab, excluding existing MooPredict jobs if any to avoid duplicates
CURRENT_CRON=$(crontab -l 2>/dev/null | grep -v "moopredict-ai")

# Create new crontab content
NEW_CRON=$(cat <<EOF
$CURRENT_CRON
# MooPredict-AI Market Automation
$CRON_OPEN
$CRON_CLOSE
$CRON_CHECK
EOF
)

# Install new crontab
echo "$NEW_CRON" | crontab -

echo "✅ Crontab updated successfully with Market Automation jobs."
echo "   - Market Open: 13:30 UTC (9:30 PM MYT)"
echo "   - Market Close: 20:00 UTC (4:00 AM MYT)"
echo "   - Alert Check: Hourly during market hours"
