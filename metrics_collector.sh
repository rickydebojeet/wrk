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
rm -f "${PREFIX}_perf.txt" "${PREFIX}_pcm_memory.csv" "${PREFIX}_softirqs_start.txt" "${PREFIX}_softirqs_end.txt" \
"${PREFIX}_stat_start.txt" "${PREFIX}_stat_end.txt" "${PREFIX}_iostat.txt"

export PCM_NO_MSR=1

echo "Starting metrics collection for ${DURATION}s..."

perf stat -e instructions, LLC-loads, LLC-load-misses, longest_lat_cache.miss, longest_lat_cache.reference, llc_misses.mem_read, llc_misses.mem_write \
    -o "${PREFIX}_perf.txt" \
    --append 2>&1 \
    -- sleep "$DURATION" &
PERF_PID=$!

# Use pcm only if it is available
if command -v pcm-memory &> /dev/null; then
    pcm-memory /csv 1 -i=180 2>/dev/null 1>>"${PREFIX}_pcm_memory.csv"
    PCM_PID=$!
fi

iostat -xd 1 ${DURATION} >> "${PREFIX}_iostat.txt" &
DISK_PID=$!
echo "iostat pid: ${disk_pid}"

cat /proc/softirqs > "${PREFIX}_softirqs_start.txt"
cat /proc/stat > "${PREFIX}_stat_start.txt"

sleep "$DURATION"

wait $PERF_PID
wait $DISK_PID

cat /proc/softirqs > "${PREFIX}_softirqs_end.txt"
cat /proc/stat > "${PREFIX}_stat_end.txt"

if [ -n "$PCM_PID" ]; then
    kill -s SIGINT $PCM_PID
    wait $PCM_PID 2>/dev/null
fi

echo "Metrics collection complete."