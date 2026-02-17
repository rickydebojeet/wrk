import subprocess
import time
import os
import csv
import re
import argparse

# --- CONFIGURATION (User to update these) ---
SERVER_IP = "127.0.0.1"  # REPLACE WITH ACTUAL SERVER IP
SSH_USER = "ricky"  # REPLACE WITH ACTUAL SSH USER
SERVER_DIR = "/home/ricky/Desktop/6-spring-2026-5/simple-webserver"  # REPLACE WITH PATH TO SERVER BINARY ON REMOTE
CLIENT_DIR = os.getcwd()  # Current directory (where wrk is)
EXPT_PORT = 8080
DURATION = 120  # Duration of each test in seconds
RESULTS_FILE = "results.csv"
WRK_CPU_CORES = 14  # Number of cores to use for wrk
COOL_DOWN_TIME = 5  # Sleep duration between diferent setups in seconds

# List of disks to monitor in iostat (e.g. ['nvme0n1', 'sda'])
DISKS = ["nvme0n1", "nvme1n1", "nvme2n1", "nvme3n1"]

# Connection counts to test
CONNECTIONS_LIST = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35]

# Server configurations (name, flags)
CONFIGURATIONS = [
    ("RW_PageCache", ""),
    ("RW_NoPageCache", "--disable-page-cache"),
    ("Sendfile_PageCache", "--use-sendfile"),
    ("Sendfile_NoPageCache", "--use-sendfile --disable-page-cache"),
]


def run_ssh_command(cmd, background=False):
    """Runs a command via SSH."""
    ssh_cmd = ["ssh", f"{SSH_USER}@{SERVER_IP}", cmd]
    if background:
        return subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True)
        return result


def start_server(flags):
    """Starts the server on the remote machine."""
    run_ssh_command("pkill -9 -x server")
    time.sleep(COOL_DOWN_TIME)

    # Start new server pinned to core 0 (for now)
    cmd = f"cd {SERVER_DIR} && taskset -c 0 ./server {EXPT_PORT} {flags} > server.log 2>&1"
    print(f"Starting server: {cmd}")
    proc = run_ssh_command(cmd, background=True)

    # Check if it stayed alive
    time.sleep(1)
    check = run_ssh_command("pgrep -x server")
    if not check.stdout.strip():
        print("WARNING: Server process not found immediately after start!")

    return proc


def stop_server():
    run_ssh_command("pkill -9 -x server")


def start_metrics(prefix, duration):
    """Starts metrics collection on server."""
    cmd = f"cd {SERVER_DIR} && ./metrics_collector.sh {duration} {prefix}"
    print(f"Starting metrics: {cmd}")
    return run_ssh_command(cmd, background=True)


def parse_wrk_output(output):
    """Parses wrk output for Latency and Throughput."""
    data = {}
    req_sec_match = re.search(r"Requests/sec:\s+(\d+\.\d+)", output)
    if req_sec_match:
        data["throughput_req_sec"] = float(req_sec_match.group(1))

    transfer_match = re.search(r"Transfer/sec:\s+(\d+\.\d+)(\w+)", output)
    if transfer_match:
        val = float(transfer_match.group(1))
        unit = transfer_match.group(2)
        if unit == "KB":
            val /= 1024.0
        elif unit == "GB":
            val *= 1024.0
        elif unit == "B":
            val /= 1024.0 * 1024.0
        data["throughput_bandwidth_mb_sec"] = val

    lat_match = re.search(r"Latency\s+(\d+\.\d+)(\w+)", output)
    if lat_match:
        val = float(lat_match.group(1))
        unit = lat_match.group(2)
        if unit == "us":
            val /= 1000.0
        elif unit == "s":
            val *= 1000.0
        data["latency_avg_ms"] = val

    # Socket errors: connect 0, read 0, write 0, timeout 0
    # Output format: "Socket errors: connect 15, read 42, write 0, timeout 0"
    errors_match = re.search(r"Socket errors:\s+(.+)", output)
    if errors_match:
        # Parse the details, e.g. "connect 15, read 42..."
        err_str = errors_match.group(1)
        data["errors"] = err_str.strip()
    else:
        data["errors"] = "None"

    return data


def parse_server_metrics(prefix, duration):
    """Fetches and parses the generated metric files from the server."""
    data = {
        "instructions": 0,
        "LLC-loads": 0,
        "LLC-load-misses": 0,
        "longest_lat_cache_miss": 0,
        "longest_lat_cache_reference": 0,
        "context_switches": 0,
        "softirqs": 0,
        "disk_read_per_s": 0.0,
        "disk_read_kB_per_s": 0.0,
        "disk_utilization": 0.0,
        "cpu0_user_percent": 0.0,
        "cpu0_system_percent": 0.0,
        "cpu0_softirq_percent": 0.0,
        "cpu0_iowait_percent": 0.0,
        "cpu0_idle_percent": 0.0,
        "memory_read_mb_sec_pcm": 0.0,
        "memory_write_mb_sec_pcm": 0.0,
    }

    perf_out = run_ssh_command(f"cat {SERVER_DIR}/{prefix}_perf.txt").stdout

    # Instructions
    if "instructions" in perf_out:
        match = re.search(r"(\d[\d,]*)\s+instructions", perf_out)
        if match:
            data["instructions"] = int(match.group(1).replace(",", ""))

    # LLC-loads (Read LLC accesses)
    if "LLC-loads" in perf_out:
        match = re.search(r"(\d[\d,]*)\s+LLC-loads", perf_out)
        if match:
            data["LLC-loads"] = int(match.group(1).replace(",", ""))

    # LLC-load-misses (Read LLC misses)
    if "LLC-load-misses" in perf_out:
        match = re.search(r"(\d[\d,]*)\s+LLC-load-misses", perf_out)
        if match:
            data["LLC-load-misses"] = int(match.group(1).replace(",", ""))

    # Longest Latency Cache Misses
    if "longest_lat_cache.miss" in perf_out:
        match = re.search(r"(\d[\d,]*)\s+longest_lat_cache.miss", perf_out)
        if match:
            data["longest_lat_cache_miss"] = int(match.group(1).replace(",", ""))

    # Longest Latency Cache Reference
    if "longest_lat_cache.reference" in perf_out:
        match = re.search(r"(\d[\d,]*)\s+longest_lat_cache.reference", perf_out)
        if match:
            data["longest_lat_cache_reference"] = int(match.group(1).replace(",", ""))

    # Context Switches & CPU0 Line (from /proc/stat)
    def get_proc_stat(filename):
        out = run_ssh_command(f"cat {SERVER_DIR}/{filename}").stdout
        ctxt = 0
        cpu0_line = ""
        for line in out.splitlines():
            if line.startswith("ctxt "):
                ctxt = int(line.split()[1])
            if line.startswith("cpu0 "):
                cpu0_line = line
        return ctxt, cpu0_line

    ctxt_start, cpu0_start_line = get_proc_stat(f"{prefix}_stat_start.txt")
    ctxt_end, cpu0_end_line = get_proc_stat(f"{prefix}_stat_end.txt")
    data["context_switches"] = ctxt_end - ctxt_start

    # SoftIRQs (diff)
    def get_softirqs(filename):
        out = run_ssh_command(f"cat {SERVER_DIR}/{filename}").stdout
        total = 0
        for line in out.splitlines():
            if ":" in line and "CPU0" not in line:
                parts = line.split()
                for p in parts[1:]:
                    if p.isdigit():
                        total += int(p)
        return total

    si_start = get_softirqs(f"{prefix}_softirqs_start.txt")
    si_end = get_softirqs(f"{prefix}_softirqs_end.txt")
    data["softirqs"] = si_end - si_start

    def parse_iostat_output(output_text, selected_disks):
        """Parses iostat output, skips the boot report, and aggregates metrics."""
        lines = output_text.strip().split("\n")
        disk_data = {
            disk: {"r/s": [], "rkB/s": [], "%util": []} for disk in selected_disks
        }

        rs_idx, rkb_idx, util_idx = -1, -1, -1
        report_count = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = line.split()

            # Detect headers and update column indices dynamically
            if parts[0] in ["Device", "Device:"]:
                report_count += 1
                try:
                    rs_idx = parts.index("r/s")
                    rkb_idx = parts.index("rkB/s")
                    util_idx = parts.index("%util")
                except ValueError:
                    pass
                continue

            # Skip the first block of stats (historical data since boot)
            if report_count == 1:
                continue

            disk_name = parts[0]

            if disk_name in selected_disks and rs_idx != -1:
                try:
                    disk_data[disk_name]["r/s"].append(float(parts[rs_idx]))
                    disk_data[disk_name]["rkB/s"].append(float(parts[rkb_idx]))
                    disk_data[disk_name]["%util"].append(float(parts[util_idx]))
                except (IndexError, ValueError):
                    pass

        # Aggregate the averages across all targeted disks
        total_rs = 0.0
        total_rkb = 0.0
        total_util = 0.0

        for disk, metrics in disk_data.items():
            samples = len(metrics["r/s"])
            if samples > 0:
                avg_rs = sum(metrics["r/s"]) / samples
                avg_rkb = sum(metrics["rkB/s"]) / samples
                avg_util = sum(metrics["%util"]) / samples

                total_rs += avg_rs
                total_rkb += avg_rkb
                total_util += avg_util

        return total_rs, total_rkb, total_util

    iostat_out = run_ssh_command(f"cat {SERVER_DIR}/{prefix}_iostat.txt").stdout

    if iostat_out.strip():
        total_rs, total_rkb, total_util = parse_iostat_output(iostat_out, DISKS)
        data["disk_read_per_s"] = total_rs
        data["disk_read_kB_per_s"] = total_rkb
        data["disk_utilization"] = total_util

    # CPU Utilization (Core 0 Specific Breakdown)
    # Re-using cpu0 line from Step 3
    def parse_cpu0_line(line):
        if not line:
            return None
        parts = line.split()
        parsed = [int(p) for p in parts[1:]]
        return {
            "user": parsed[0],
            "nice": parsed[1],
            "system": parsed[2],
            "idle": parsed[3],
            "iowait": parsed[4],
            "irq": parsed[5],
            "softirq": parsed[6],
            "steal": parsed[7] if len(parsed) > 7 else 0,
            "total": sum(parsed),
        }

    cpu_start = parse_cpu0_line(cpu0_start_line)
    cpu_end = parse_cpu0_line(cpu0_end_line)

    if cpu_start and cpu_end:
        diff_total = cpu_end["total"] - cpu_start["total"]
        if diff_total > 0:
            data["cpu0_user_percent"] = (
                (cpu_end["user"] - cpu_start["user"]) / diff_total * 100
            )
            data["cpu0_system_percent"] = (
                (cpu_end["system"] - cpu_start["system"]) / diff_total * 100
            )
            data["cpu0_softirq_percent"] = (
                (cpu_end["softirq"] - cpu_start["softirq"]) / diff_total * 100
            )
            data["cpu0_iowait_percent"] = (
                (cpu_end["iowait"] - cpu_start["iowait"]) / diff_total * 100
            )
            data["cpu0_idle_percent"] = (
                (cpu_end["idle"] - cpu_start["idle"]) / diff_total * 100
            )

    def parse_pcm_memory_output(output_text):
        """Parses PCM memory output to extract read and write bandwidth."""
        mem_read_bw = 0.0
        mem_write_bw = 0.0
        line_count = 0

        for line in output_text.splitlines():
            line = line.strip()

            if "2026-" in line and "Time" not in line:
                parts = line.split(",")

                # Ensure the line has enough columns to avoid index errors
                if len(parts) >= 3:
                    try:
                        # Your indices: Read is 3rd from the end, Write is 2nd from the end
                        mem_read_bw += float(parts[-3])
                        mem_write_bw += float(parts[-2])
                        line_count += 1
                    except ValueError:
                        pass  # Skip lines where conversion to float fails

        # Calculate the average dynamically based on actual lines captured
        if line_count > 0:
            return mem_read_bw / line_count, mem_write_bw / line_count

        return 0.0, 0.0

    pcm_out_raw = run_ssh_command(f"cat {SERVER_DIR}/{prefix}_pcm_memory.csv").stdout

    if pcm_out_raw.strip():
        read_bw, write_bw = parse_pcm_memory_output(pcm_out_raw)
        data["memory_read_mb_sec_pcm"] = read_bw
        data["memory_write_mb_sec_pcm"] = write_bw

    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true", help="Run a short test with 1 connection"
    )
    args = parser.parse_args()

    with open(RESULTS_FILE, "w", newline="") as csvfile:
        fieldnames = [
            "config",
            "connections",
            "throughput_req_sec",
            "throughput_bandwidth_mb_sec",
            "latency_avg_ms",
            "errors",
            "instructions",
            "LLC-loads",
            "LLC-load-misses",
            "longest_lat_cache_miss",
            "longest_lat_cache_reference",
            "context_switches",
            "softirqs",
            "disk_read_per_s",
            "disk_read_kB_per_s",
            "disk_utilization",
            "cpu0_user_percent",
            "cpu0_system_percent",
            "cpu0_softirq_percent",
            "cpu0_iowait_percent",
            "cpu0_idle_percent",
            "memory_read_mb_sec_pcm",
            "memory_write_mb_sec_pcm",
        ]

        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        conns = [1] if args.dry_run else CONNECTIONS_LIST
        duration = 5 if args.dry_run else DURATION

        print("Deploying metrics_collector.sh...")
        subprocess.run(
            [
                "scp",
                "metrics_collector.sh",
                f"{SSH_USER}@{SERVER_IP}:{SERVER_DIR}/metrics_collector.sh",
            ]
        )
        run_ssh_command(f"chmod +x {SERVER_DIR}/metrics_collector.sh")

        for conf_name, conf_flags in CONFIGURATIONS:
            # Start Server
            server_proc = start_server(conf_flags)
            time.sleep(COOL_DOWN_TIME)

            for c in conns:
                print(f"--- Running: {conf_name} | Conns: {c} ---")
                prefix = f"metrics_{conf_name}_{c}"

                # Start Server Metrics with buffer to ensure coverage
                metrics_proc = start_metrics(prefix, duration + 5)

                # Run WRK
                # Usage: wrk -t <threads> -c <conns> -d <duration> -s load_urls.lua <base_url>
                base_url = f"http://{SERVER_IP}:{EXPT_PORT}/"

                threads = min(c, WRK_CPU_CORES) if c > 1 else 1

                wrk_cmd = [
                    "./wrk",
                    "-t",
                    str(threads),
                    "-c",
                    str(c),
                    "-d",
                    str(duration) + "s",
                    "-s",
                    "load_urls.lua",
                    "--latency",
                    base_url,
                ]

                print(f"Running wrk: {' '.join(wrk_cmd)}")
                wrk_res = subprocess.run(wrk_cmd, capture_output=True, text=True)

                # Wait for metrics to finish (they sleep for duration)
                metrics_proc.wait()

                # Parse
                wrk_data = parse_wrk_output(wrk_res.stdout)
                server_data = parse_server_metrics(prefix, duration)

                row = {"config": conf_name, "connections": c}
                row.update(wrk_data)
                row.update(server_data)

                print(f"Result: {row}")
                writer.writerow(row)
                csvfile.flush()

            stop_server()
            time.sleep(COOL_DOWN_TIME)


if __name__ == "__main__":
    main()
