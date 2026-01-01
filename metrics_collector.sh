#!/bin/bash
# metrics_collector.sh
# Collects system metrics for a specified duration.

DURATION=$1
PREFIX=$2

if [ -z "$DURATION" ] || [ -z "$PREFIX" ]; then
    echo "Usage: $0 <duration> <output_prefix>"
    exit 1
fi

# Remove any old metrics files
rm -f "${PREFIX}_perf.txt" "${PREFIX}_pcm_memory.csv" "${PREFIX}_disk_start.txt" "${PREFIX}_disk_end.txt" "${PREFIX}_softirqs_start.txt" "${PREFIX}_softirqs_end.txt" "${PREFIX}_stat_start.txt" "${PREFIX}_stat_end.txt"

echo "Starting metrics collection for ${DURATION}s..."

perf stat -e longest_lat_cache.miss,longest_lat_cache.reference,LLC-load-misses,cache-misses,node-loads,LLC-loads,context-switches,cpu-migrations,page-faults,cycles,instructions \
    -o "${PREFIX}_perf.txt" \
    --append 2>&1 \
    -- sleep "$DURATION" &
PERF_PID=$!

# Use pcm only if it is available
if command -v pcm-memory &> /dev/null; then
    pcm-memory 1 -csv="${PREFIX}_pcm_memory.csv" 2>/dev/null &
    PCM_PID=$!
fi

cat /proc/diskstats > "${PREFIX}_disk_start.txt"
cat /proc/softirqs > "${PREFIX}_softirqs_start.txt"
cat /proc/stat > "${PREFIX}_stat_start.txt"

sleep "$DURATION"
wait $PERF_PID

cat /proc/diskstats > "${PREFIX}_disk_end.txt"
cat /proc/softirqs > "${PREFIX}_softirqs_end.txt"
cat /proc/stat > "${PREFIX}_stat_end.txt"

if [ -n "$PCM_PID" ]; then
    kill -SIGINT $PCM_PID
    wait $PCM_PID 2>/dev/null
fi

echo "Metrics collection complete."
