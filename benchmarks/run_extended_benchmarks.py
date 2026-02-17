#!/usr/bin/env python3
"""
Extended ASGI Stack Benchmark Runner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Benchmarks the 6 extended implementations (NautilusTrader + skfolio)
across multiple realistic workload scenarios.

NautilusTrader is stubbed (requires external connectivity); skfolio
runs for real so the portfolio endpoints provide genuine CPU-bound work.

Usage:
    python benchmarks/run_extended_benchmarks.py [--duration 30] [--workers 2]
"""

import subprocess
import sys
import os
import time
import json
import re
import argparse
import textwrap
from pathlib import Path
from urllib.request import urlopen

# ── NautilusTrader mock preamble ─────────────────────────────────────
# Injected into every server subprocess via `python -c`.
# Intercepts all `nautilus_trader.*` imports with lightweight stubs
# that support call / await / iter / bool / str / float so every
# code-path in the app modules survives without the real package.

MOCK_PREAMBLE = textwrap.dedent("""\
    import sys, types
    class _MC:
        def __call__(self, *a, **kw): return _MC()
        def __getattr__(self, n):
            if n[:2] == n[-2:] == "__": raise AttributeError(n)
            return _MC()
        def __await__(self):
            async def _c(): return _MC()
            return _c().__await__()
        def __bool__(self): return True
        def __iter__(self): return iter([])
        def __str__(self): return "mock"
        def __repr__(self): return "<mock>"
        def __float__(self): return 0.0
        def __int__(self): return 0
    class _MM(types.ModuleType):
        def __getattr__(self, n):
            if n[:2] == n[-2:] == "__": raise AttributeError(n)
            return _MC()
    class _F:
        def find_module(self, n, p=None):
            return self if n == "nautilus_trader" or n.startswith("nautilus_trader.") else None
        def load_module(self, n):
            if n in sys.modules: return sys.modules[n]
            m = _MM(n); m.__path__ = [n]; m.__package__ = n
            sys.modules[n] = m; return m
    sys.meta_path.insert(0, _F())
""")

# ── Stack definitions ────────────────────────────────────────────────

STACKS = [
    {
        "id": "uvicorn-raw",
        "name": "Uvicorn · Raw ASGI",
        "module": "benchmarks.uvicorn_extended",
        "server": "uvicorn",
    },
    {
        "id": "granian-raw",
        "name": "Granian · Raw ASGI",
        "module": "benchmarks.granian_extended",
        "server": "granian",
    },
    {
        "id": "gunicorn-uvicorn-raw",
        "name": "Gunicorn+Uvicorn · Raw ASGI",
        "module": "benchmarks.gunicorn_uvicorn_extended",
        "server": "gunicorn",
    },
    {
        "id": "fastapi-uvicorn",
        "name": "FastAPI · Uvicorn",
        "module": "benchmarks.fastapi_uvicorn_extended",
        "server": "uvicorn",
    },
    {
        "id": "fastapi-granian",
        "name": "FastAPI · Granian",
        "module": "benchmarks.fastapi_granian_extended",
        "server": "granian",
    },
    {
        "id": "fastapi-gunicorn-uvicorn",
        "name": "FastAPI · Gunicorn+Uvicorn",
        "module": "benchmarks.fastapi_gunicorn_uvicorn_extended",
        "server": "gunicorn",
    },
]

# ── Scenario definitions ─────────────────────────────────────────────

PORTFOLIO_BODY = json.dumps({
    "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "META"],
    "risk_measure": "variance",
    "objective": "maximize_ratio",
})

HRP_BODY = json.dumps({
    "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "META",
                "NVDA", "JPM", "JNJ", "V", "PG"],
})

SCENARIOS = [
    # ── Lightweight I/O ──────────────────────────────────────────────
    # Reveals raw request-handling overhead and event-loop efficiency.
    {
        "name": "health-c50",
        "label": "GET /health · c=50",
        "desc": "Steady-state health polling (monitoring / LB probes)",
        "method": "GET",
        "path": "/health",
        "concurrency": 50,
    },
    {
        "name": "health-c200",
        "label": "GET /health · c=200",
        "desc": "Max-throughput stress test on minimal endpoint",
        "method": "GET",
        "path": "/health",
        "concurrency": 200,
    },
    # ── CPU-bound (skfolio MeanRisk optimization) ────────────────────
    # Real compute: load SP500 dataset → returns matrix → fit model.
    {
        "name": "optimize-c10",
        "label": "POST /portfolio/optimize · c=10",
        "desc": "Moderate analyst load – MeanRisk optimization (5 assets)",
        "method": "POST",
        "path": "/portfolio/optimize",
        "body": PORTFOLIO_BODY,
        "concurrency": 10,
    },
    {
        "name": "optimize-c50",
        "label": "POST /portfolio/optimize · c=50",
        "desc": "Peak CPU pressure – concurrent optimizations",
        "method": "POST",
        "path": "/portfolio/optimize",
        "body": PORTFOLIO_BODY,
        "concurrency": 50,
    },
    # ── CPU-bound (skfolio Hierarchical Risk Parity) ─────────────────
    # Different compute profile: clustering + dendrogram (10 assets).
    {
        "name": "hrp-c10",
        "label": "POST /portfolio/hierarchical · c=10",
        "desc": "HRP optimization – clustering-based, 10 assets",
        "method": "POST",
        "path": "/portfolio/hierarchical",
        "body": HRP_BODY,
        "concurrency": 10,
    },
    # ── Burst patterns ───────────────────────────────────────────────
    # Fixed request count instead of duration – tests connection
    # handling and queuing under sudden traffic spikes.
    {
        "name": "burst-health",
        "label": "Burst: 5 000 × GET /health · c=200",
        "desc": "Sudden traffic spike – raw connection handling",
        "method": "GET",
        "path": "/health",
        "concurrency": 200,
        "total_requests": 5000,
    },
    {
        "name": "burst-optimize",
        "label": "Burst: 100 × POST /portfolio/optimize · c=50",
        "desc": "CPU burst – queuing behaviour under pressure",
        "method": "POST",
        "path": "/portfolio/optimize",
        "body": PORTFOLIO_BODY,
        "concurrency": 50,
        "total_requests": 100,
    },
]

# ── Server lifecycle ─────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _server_code(stack, port, workers):
    """Build the `python -c` payload for each server type."""
    module = stack["module"]
    server = stack["server"]

    if server == "uvicorn":
        return MOCK_PREAMBLE + textwrap.dedent(f"""\
            import uvicorn
            uvicorn.run("{module}:app", host="0.0.0.0", port={port}, log_level="warning")
        """)

    if server == "granian":
        return MOCK_PREAMBLE + textwrap.dedent(f"""\
            from granian import Granian
            Granian("{module}:app", address="0.0.0.0", port={port}, interface="asgi").serve()
        """)

    if server == "gunicorn":
        return MOCK_PREAMBLE + textwrap.dedent(f"""\
            import sys as _s
            _s.argv = [
                "gunicorn", "{module}:app",
                "-k", "uvicorn.workers.UvicornWorker",
                "-w", "{workers}",
                "--bind", "0.0.0.0:{port}",
                "--log-level", "warning",
            ]
            from gunicorn.app.wsgiapp import run
            run()
        """)

    raise ValueError(f"Unknown server type: {server}")


def start_server(stack, port, workers):
    """Start a server subprocess with NautilusTrader mocked out."""
    code = _server_code(stack, port, workers)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    for _ in range(60):
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            if resp.status == 200:
                return proc
        except Exception:
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                raise RuntimeError(
                    f"{stack['name']} exited (code {proc.returncode}): "
                    + stderr[:500]
                )
            time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(f"{stack['name']} failed to respond within 30 s")


def stop_server(proc):
    """Gracefully stop, escalate to SIGKILL on timeout."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def get_rss_kb(pid):
    """Total RSS of a process tree in kB (Linux /proc only)."""
    total = 0
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        children = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True
        )
        for cpid in children.stdout.strip().splitlines():
            try:
                with open(f"/proc/{cpid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1])
            except (FileNotFoundError, PermissionError):
                pass
    except (FileNotFoundError, PermissionError):
        return None
    return total


# ── hey execution & parsing ──────────────────────────────────────────

def run_hey(base_url, scenario, duration):
    cmd = ["hey"]

    if "total_requests" in scenario:
        cmd += ["-n", str(scenario["total_requests"])]
    else:
        cmd += ["-z", f"{duration}s"]

    cmd += ["-c", str(scenario["concurrency"]), "-m", scenario["method"]]

    if "body" in scenario:
        cmd += ["-T", "application/json", "-d", scenario["body"]]

    cmd.append(f"{base_url}{scenario['path']}")

    timeout = (duration * 3 + 60) if "total_requests" not in scenario else 300
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return parse_hey(result.stdout), result.stdout


def parse_hey(text):
    r = {}
    for key, pat in [
        ("total_time", r"Total:\s+([\d.]+) secs"),
        ("slowest", r"Slowest:\s+([\d.]+) secs"),
        ("fastest", r"Fastest:\s+([\d.]+) secs"),
        ("average", r"Average:\s+([\d.]+) secs"),
        ("rps", r"Requests/sec:\s+([\d.]+)"),
    ]:
        m = re.search(pat, text)
        r[key] = float(m.group(1)) if m else None

    for pct in ("50", "75", "90", "95", "99"):
        m = re.search(rf"{pct}%\s+in\s+([\d.]+)\s+secs", text)
        r[f"p{pct}"] = float(m.group(1)) if m else None

    codes = {}
    for m in re.finditer(r"\[(\d+)\]\s+(\d+)\s+responses", text):
        codes[int(m.group(1))] = int(m.group(2))
    r["status_codes"] = codes
    r["total_requests"] = sum(codes.values())
    r["errors"] = sum(v for k, v in codes.items() if k >= 400)
    return r


# ── Formatting ───────────────────────────────────────────────────────

def fmt_lat(secs):
    if secs is None:
        return "N/A"
    if secs < 0.001:
        return f"{secs * 1_000_000:.0f}\u00b5s"
    if secs < 1:
        return f"{secs * 1000:.1f}ms"
    return f"{secs:.2f}s"


def fmt_rps(rps):
    return f"{rps:,.0f}" if rps else "N/A"


def fmt_mem(kb):
    if kb is None:
        return "N/A"
    return f"{kb / 1024:.1f} MB" if kb > 1024 else f"{kb} kB"


# ── Report generation ────────────────────────────────────────────────

def generate_report(all_results, memory, duration, workers):
    lines = [
        "# Extended ASGI Stack Benchmark Results\n",
        f"- **Duration**: {duration}s per timed scenario",
        f"- **Workers**: {workers} (Gunicorn multi-worker stacks)",
        "- **Runner**: `ubuntu-latest` (GitHub Actions)",
        "- **NautilusTrader**: mocked (stub imports)",
        "- **skfolio**: real (CPU-bound workload)\n",
        "---\n",
    ]

    # Memory table
    lines += [
        "## Memory Usage\n",
        "| Stack | Idle RSS | After Load RSS |",
        "|-------|----------|----------------|",
    ]
    for s in STACKS:
        idle = fmt_mem(memory.get((s["id"], "idle")))
        loaded = fmt_mem(memory.get((s["id"], "loaded")))
        lines.append(f'| {s["name"]} | {idle} | {loaded} |')
    lines.append("")

    # Per-scenario tables
    for sc in SCENARIOS:
        lines += [
            f'## {sc["label"]}',
            f'> {sc["desc"]}\n',
            "| Stack | Req/s | Avg | P50 | P90 | P99 | Errors |",
            "|-------|------:|----:|----:|----:|----:|-------:|",
        ]
        for s in STACKS:
            r = all_results.get((s["id"], sc["name"]), {})
            lines.append(
                f'| {s["name"]} '
                f'| {fmt_rps(r.get("rps"))} '
                f'| {fmt_lat(r.get("average"))} '
                f'| {fmt_lat(r.get("p50"))} '
                f'| {fmt_lat(r.get("p90"))} '
                f'| {fmt_lat(r.get("p99"))} '
                f'| {r.get("errors", "N/A")} |'
            )
        lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extended ASGI Stack Benchmark")
    parser.add_argument("--duration", type=int, default=30,
                        help="Seconds per timed scenario (default: 30)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Workers for multi-process stacks (default: 2)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--stacks", nargs="*",
                        help="Stack IDs to benchmark (default: all)")
    args = parser.parse_args()

    stacks_to_run = STACKS
    if args.stacks:
        stacks_to_run = [s for s in STACKS if s["id"] in args.stacks]

    results_dir = PROJECT_ROOT / "benchmarks" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}
    memory = {}

    for stack in stacks_to_run:
        print(f'\n{"=" * 60}')
        print(f'  {stack["name"]}')
        print(f'{"=" * 60}')

        try:
            proc = start_server(stack, args.port, args.workers)
        except RuntimeError as e:
            print(f"  SKIP: {e}")
            continue

        try:
            base_url = f"http://127.0.0.1:{args.port}"

            # Memory idle
            memory[(stack["id"], "idle")] = get_rss_kb(proc.pid)

            # Warm up
            print("  Warming up ...")
            subprocess.run(
                ["hey", "-n", "100", "-c", "10", f"{base_url}/health"],
                capture_output=True,
                timeout=30,
            )
            # Prime CPU-bound path once so dataset is cached in page cache
            subprocess.run(
                [
                    "hey", "-n", "2", "-c", "1",
                    "-m", "POST",
                    "-T", "application/json",
                    "-d", PORTFOLIO_BODY,
                    f"{base_url}/portfolio/optimize",
                ],
                capture_output=True,
                timeout=60,
            )
            time.sleep(1)

            # Run each scenario
            for scenario in SCENARIOS:
                label = scenario["label"]
                print(f"  > {label} ... ", end="", flush=True)
                try:
                    result, raw = run_hey(base_url, scenario, args.duration)
                    all_results[(stack["id"], scenario["name"])] = result

                    # Persist raw hey output
                    raw_path = results_dir / f'{stack["id"]}_{scenario["name"]}.txt'
                    raw_path.write_text(raw)

                    print(
                        f'{fmt_rps(result.get("rps"))} req/s  '
                        f'avg={fmt_lat(result.get("average"))}  '
                        f'p99={fmt_lat(result.get("p99"))}  '
                        f'err={result.get("errors", 0)}'
                    )
                except Exception as e:
                    print(f"ERROR: {e}")

                time.sleep(2)  # cool-down between scenarios

            # Memory after load
            memory[(stack["id"], "loaded")] = get_rss_kb(proc.pid)

        finally:
            stop_server(proc)
            time.sleep(2)  # release port

    # ── Reports ──────────────────────────────────────────────────────
    report = generate_report(all_results, memory, args.duration, args.workers)
    (results_dir / "summary.md").write_text(report)

    json_data = {f"{k[0]}|{k[1]}": v for k, v in all_results.items()}
    json_data["_memory"] = {f"{k[0]}|{k[1]}": v for k, v in memory.items()}
    (results_dir / "raw_results.json").write_text(
        json.dumps(json_data, indent=2, default=str)
    )

    print(f"\nReport saved to benchmarks/results/summary.md")
    print("\n" + report)


if __name__ == "__main__":
    main()
