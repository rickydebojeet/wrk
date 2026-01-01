import subprocess
import time
import os
import csv
import re
import argparse

# --- CONFIGURATION (User to update these) ---
SERVER_IP = "127.0.0.1"  # REPLACE WITH ACTUAL SERVER IP
SSH_USER = "ricky"          # REPLACE WITH ACTUAL SSH USER
SERVER_DIR = "/home/ricky/Desktop/6-spring-2026-5/simple-webserver" # REPLACE WITH PATH TO SERVER BINARY ON REMOTE
CLIENT_DIR = os.getcwd()    # Current directory (where wrk is)
SERVER_PORT = 8080
DURATION = 20 # Duration of each test in seconds
RESULTS_FILE = "results.csv"
WRK_CPU_CORES = 4 # Number of cores to use for wrk
COOL_DOWN_TIME = 5 # Sleep duration between diferent setups in seconds

# Connection counts to test
CONNECTIONS_LIST = [1, 5, 10]

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
    cmd = f"cd {SERVER_DIR} && taskset -c 0 ./server {SERVER_PORT} {flags} > server.log 2>&1"
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
        data['throughput_req_sec'] = float(req_sec_match.group(1))
        
    transfer_match = re.search(r"Transfer/sec:\s+(\d+\.\d+)(\w+)", output)
    if transfer_match:
        val = float(transfer_match.group(1))
        unit = transfer_match.group(2)
        if unit == "KB": val /= 1024.0
        elif unit == "GB": val *= 1024.0
        elif unit == "B": val /= (1024.0 * 1024.0)
        data['throughput_bandwidth_mb_sec'] = val

    lat_match = re.search(r"Latency\s+(\d+\.\d+)(\w+)", output)
    if lat_match:
        val = float(lat_match.group(1))
        unit = lat_match.group(2)
        if unit == "us": val /= 1000.0
        elif unit == "s": val *= 1000.0
        data['latency_avg_ms'] = val
        
    # Socket errors: connect 0, read 0, write 0, timeout 0
    # Output format: "Socket errors: connect 15, read 42, write 0, timeout 0"
    errors_match = re.search(r"Socket errors:\s+(.+)", output)
    if errors_match:
        # Parse the details, e.g. "connect 15, read 42..."
        err_str = errors_match.group(1)
        data['errors'] = err_str.strip()
    else:
        data['errors'] = "None"
        
    return data

def parse_server_metrics(prefix, duration):
    """Fetches and parses the generated metric files from the server."""
    data = {
        'llc_misses': 0, 
        'longest_lat_cache_miss': 0, 
        'context_switches': 0, 
        'softirqs': 0, 
        'disk_read_mb_sec': 0.0, 
        'disk_utilization_percent': 0.0,
        'cpu0_user_percent': 0.0, 
        'cpu0_system_percent': 0.0, 
        'cpu0_softirq_percent': 0.0, 
        'cpu0_iowait_percent': 0.0, 
        'cpu0_idle_percent': 0.0,
        'memory_read_mb_sec_pcm': 0.0, 
        'memory_reads_perf': 0
    }
    
    perf_out = run_ssh_command(f"cat {SERVER_DIR}/{prefix}_perf.txt").stdout

    # LLC-load-misses (Read LLC misses)
    if 'LLC-load-misses' in perf_out:
        match = re.search(r"(\d[\d,]*)\s+LLC-load-misses", perf_out)
        if match:
            data['llc_misses'] = int(match.group(1).replace(',', ''))

    # Longest Latency Cache Misses
    if 'longest_lat_cache.miss' in perf_out:
        match = re.search(r"(\d[\d,]*)\s+longest_lat_cache.miss", perf_out)
        if match:
            data['longest_lat_cache_miss'] = int(match.group(1).replace(',', ''))

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
    data['context_switches'] = ctxt_end - ctxt_start

    # SoftIRQs (diff)
    def get_softirqs(filename):
        out = run_ssh_command(f"cat {SERVER_DIR}/{filename}").stdout
        total = 0
        for line in out.splitlines():
             if ':' in line and "CPU0" not in line:
                 parts = line.split()
                 for p in parts[1:]:
                     if p.isdigit(): total += int(p)
        return total
    
    si_start = get_softirqs(f"{prefix}_softirqs_start.txt")
    si_end = get_softirqs(f"{prefix}_softirqs_end.txt")
    data['softirqs'] = si_end - si_start

    # Disk Stats (Robust Selection)
    def get_disk_stats(filename):
        out = run_ssh_command(f"cat {SERVER_DIR}/{filename}").stdout
        stats = {}
        for line in out.splitlines():
            parts = line.split()
            # Filter loop, ram. Capture all others.
            if len(parts) > 12 and not parts[2].startswith('loop') and not parts[2].startswith('ram'):
                 dev = parts[2]
                 stats[dev] = {'sectors_read': int(parts[5]), 'io_time_ms': int(parts[12])}
        return stats

    disk_start = get_disk_stats(f"{prefix}_disk_start.txt")
    disk_end = get_disk_stats(f"{prefix}_disk_end.txt")

    # Disk Stats Logic // Needs re-looks
    # 1. Throughput (MB/s): SUM of all "Disk" devices (avoiding partitions to prevent double count).
    # 2. Utilization (%): MAX of any device (to find bottleneck).
    
    total_sectors_diff = 0
    max_io_time_diff = 0
    
    for dev in disk_start:
        if dev in disk_end:
            # Simple heuristic to avoid double counting partitions:
            # 1. skip if name ends in digit (sda1) UNLESS it is nvme (nvme0n1)
            # 2. nvme: keep nvme0n1, skip nvme0n1p1
            
            is_partition = False
            if dev.startswith('nvme'):
                if 'p' in dev: is_partition = True # nvme0n1p1
            elif dev[-1].isdigit():
                is_partition = True # sda1
            
            if not is_partition:
                s_diff = disk_end[dev]['sectors_read'] - disk_start[dev]['sectors_read']
                t_diff = disk_end[dev]['io_time_ms'] - disk_start[dev]['io_time_ms']
                
                total_sectors_diff += s_diff
                if t_diff > max_io_time_diff:
                    max_io_time_diff = t_diff
            
    data['disk_read_mb_sec'] = (total_sectors_diff * 512) / duration / (1024 * 1024)
    data['disk_utilization_percent'] = (max_io_time_diff / (duration * 1000.0)) * 100.0

    
    # CPU Utilization (Core 0 Specific Breakdown)
    # Re-using cpu0 line from Step 3
    def parse_cpu0_line(line):
        if not line: return None
        parts = line.split()
        parsed = [int(p) for p in parts[1:]] 
        return {
            'user': parsed[0],
            'nice': parsed[1],
            'system': parsed[2],
            'idle': parsed[3],
            'iowait': parsed[4],
            'irq': parsed[5],
            'softirq': parsed[6],
            'steal': parsed[7] if len(parsed) > 7 else 0,
            'total': sum(parsed)
        }

    cpu_start = parse_cpu0_line(cpu0_start_line)
    cpu_end = parse_cpu0_line(cpu0_end_line)
    
    if cpu_start and cpu_end:
        diff_total = cpu_end['total'] - cpu_start['total']
        if diff_total > 0:
            data['cpu0_user_percent'] = (cpu_end['user'] - cpu_start['user']) / diff_total * 100
            data['cpu0_system_percent'] = (cpu_end['system'] - cpu_start['system']) / diff_total * 100
            data['cpu0_softirq_percent'] = (cpu_end['softirq'] - cpu_start['softirq']) / diff_total * 100
            data['cpu0_iowait_percent'] = (cpu_end['iowait'] - cpu_start['iowait']) / diff_total * 100
            data['cpu0_idle_percent'] = (cpu_end['idle'] - cpu_start['idle']) / diff_total * 100
    
    # Legacy perf memory reads // Needs re-looks
    if 'node-loads' in perf_out:
        match = re.search(r"(\d[\d,]*)\s+node-loads", perf_out)
        if match: data['memory_reads_perf'] = int(match.group(1).replace(',', ''))
    elif 'LLC-loads' in perf_out:
        match = re.search(r"(\d[\d,]*)\s+LLC-loads", perf_out)
        if match: data['memory_reads_perf'] = int(match.group(1).replace(',', ''))

    # Rename keys to match fieldnames for writer
    data['memory_read_mb_sec_pcm'] = data.pop('memory_read_mb_sec', 0)
    
    # Memory Bandwidth (PCM)
    # Parse ${prefix}_pcm_memory.csv
    # Format usually: Time, System Read Throughput(MB/s), System Write...
    def get_pcm_memory_read(filename):
        try:
             # We can't guarantee existence if pcm failed
             out = run_ssh_command(f"cat {SERVER_DIR}/{filename}").stdout
             if not out: return 0.0
             
             # The CSV might contain header lines. 
             # We are looking for "System Read Throughput(MB/s)" or similar
             # Then average the values.
             # pcm-memory csv is complex:
             # Time, Socket 0 Read, Socket 0 Write, Socket 1..., System Read, System Write
             lines = out.splitlines()
             if len(lines) < 2: return 0.0
             
             # Locate "System Read" index
             header = lines[0].split(',')
             sys_read_idx = -1
             for i, col in enumerate(header):
                 if "System Read" in col: # Loose match
                     sys_read_idx = i
                     break
             
             if sys_read_idx == -1: return 0.0
             
             total = 0.0
             count = 0
             for line in lines[1:]: # Skip header
                 parts = line.split(',')
                 if len(parts) > sys_read_idx:
                     val = parts[sys_read_idx]
                     try:
                         total += float(val)
                         count += 1
                     except ValueError:
                         pass
             
             if count > 0: return total / count
             return 0.0
        except:
             return 0.0


    data['memory_read_mb_sec_pcm'] = get_pcm_memory_read(f"{prefix}_pcm_memory.csv")
    
    return data

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run a short test with 1 connection")
    args = parser.parse_args()

    with open(RESULTS_FILE, "w", newline='') as csvfile:
        fieldnames = ['config', 'connections', 'throughput_req_sec', 'throughput_bandwidth_mb_sec', 'latency_avg_ms', 'errors',
                      'llc_misses', 'longest_lat_cache_miss', 'context_switches', 'softirqs', 'disk_read_mb_sec', 'disk_utilization_percent', 
                      'cpu0_user_percent', 'cpu0_system_percent', 'cpu0_softirq_percent', 'cpu0_iowait_percent', 'cpu0_idle_percent',
                      'memory_read_mb_sec_pcm', 'memory_reads_perf']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        conns = [1] if args.dry_run else CONNECTIONS_LIST
        duration = 5 if args.dry_run else DURATION
        
        print("Deploying metrics_collector.sh...")
        subprocess.run(["scp", "metrics_collector.sh", f"{SSH_USER}@{SERVER_IP}:{SERVER_DIR}/metrics_collector.sh"])
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
                base_url = f"http://{SERVER_IP}:{SERVER_PORT}/"
                
                threads = min(c, WRK_CPU_CORES) if c > 1 else 1
                 
                wrk_cmd = ["./wrk", "-t", str(threads), "-c", str(c), "-d", str(duration) + "s", 
                            "-s", "load_urls.lua", "--latency", base_url]
                 
                print(f"Running wrk: {' '.join(wrk_cmd)}")
                wrk_res = subprocess.run(wrk_cmd, capture_output=True, text=True)
                 
                # Wait for metrics to finish (they sleep for duration)
                metrics_proc.wait() 
                 
                # Parse
                wrk_data = parse_wrk_output(wrk_res.stdout)
                server_data = parse_server_metrics(prefix, duration)
                 
                row = {'config': conf_name, 'connections': c}
                row.update(wrk_data)
                row.update(server_data)
                 
                print(f"Result: {row}")
                writer.writerow(row)
                csvfile.flush()
                 
            stop_server()
            time.sleep(COOL_DOWN_TIME)

if __name__ == "__main__":
    main()
