#!/bin/bash

# kill_port.sh
# Robustly identifies and terminates processes on a specific port.
# Usage: ./kill_port.sh <port_number>

kill_port_if_active() {
    local target_port=$1
    
    # Use lsof to get the PID of the process using the port
    # -t: terse output (PID only)
    # -i: list IP sockets
    local pids=$(lsof -ti :"$target_port" 2>/dev/null)
    
    if [ -z "$pids" ]; then
        echo "✅ Port $target_port is already clear."
        return 0
    fi
    
    echo "⚠️  Found active process(es) on port $target_port: $pids"
    echo "🧹 Terminating..."
    
    # Try to kill gracefully first, then force if necessary
    # On Mac, we'll go straight to -9 for reliable enterprise cleanup as requested
    if kill -9 $pids 2>/dev/null; then
        echo "✨ Successfully cleared port $target_port."
    else
        echo "❌ Failed to terminate process on port $target_port. It may require higher privileges."
        return 1
    fi
}

# Run the function for all provided ports
if [ $# -eq 0 ]; then
    kill_port_if_active 3001
else
    for port in "$@"; do
        kill_port_if_active "$port"
    done
fi
