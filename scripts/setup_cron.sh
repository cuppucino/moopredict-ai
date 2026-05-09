#!/bin/bash

# Configuration
IP_ADDR="127.0.0.1"
PORT="3001"
BASE_URL="http://$IP_ADDR:$PORT"

# Define cron jobs (Times adjusted for Malaysia Time - MYT)
# 13:00 UTC = 21:00 MYT (9:00 PM) - 30 min before open
CRON_OPEN="00 21 * * 1-5 curl -s -X POST $BASE_URL/api/notify -H 'Content-Type: application/json' -d '{\"message\": \"🚨 Market opens in 30 minutes!\", \"level\": \"alert\"}' > /dev/null"
# 21:00 UTC = 05:00 MYT (5:00 AM) - Market close
CRON_CLOSE="00 05 * * 2-6 curl -s -X POST $BASE_URL/api/notify -H 'Content-Type: application/json' -d '{\"message\": \"📊 Market closed. Generating report...\", \"level\": \"info\"}' > /dev/null"
# Check alerts hourly during US market hours (21:00 - 05:00 MYT)
CRON_CHECK="0 21,22,23,0,1,2,3,4 * * 2-6 curl -s -X POST $BASE_URL/api/alerts/check > /dev/null"

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
