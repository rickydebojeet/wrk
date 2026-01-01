#!/bin/bash
# metrics_collector.sh
# Collects system metrics for a specified duration.

DURATION=$1
PREFIX=$2

if [ -z "$DURATION" ] || [ -z "$PREFIX" ]; then
    echo "Usage: $0 <duration> <output_prefix>"
    exit 1
fi

echo "Starting metrics collection for ${DURATION}s..."

# 1. CPU & Context Switches via perf
perf stat -e LLC-load-misses,cache-misses,node-loads,LLC-loads,context-switches,cpu-migrations,page-faults,cycles,instructions \
    -o "${PREFIX}_perf.txt" \
    --append 2>&1 \
    -- sleep "$DURATION" &
PERF_PID=$!

# 2. Disk Stats & /proc/stat for detailed CPU breakdown
cat /proc/diskstats > "${PREFIX}_disk_start.txt"
cat /proc/softirqs > "${PREFIX}_softirqs_start.txt"
cat /proc/stat > "${PREFIX}_stat_start.txt"

# 3. Wait for duration (Perf is running in bg)
wait $PERF_PID

# Capture end state
cat /proc/diskstats > "${PREFIX}_disk_end.txt"
cat /proc/softirqs > "${PREFIX}_softirqs_end.txt"
cat /proc/stat > "${PREFIX}_stat_end.txt"

echo "Metrics collection complete."
