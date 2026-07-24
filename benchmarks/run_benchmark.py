"""
Benchmark harness for Pearl's end-to-end autonomous workflow against
real repositories (Phase 23).

For each target repo, spawns Pearl's real MCP server (`python -m
src.mcp`, with `PYTHONPATH` set to Pearl's own repo and `cwd` set to
the target repo — exactly how a VS Code workspace pointed at that
project runs it) and measures:

  - repo size (files, Python files, lines)
  - planning + execution time (the `pearl/runAutonomous` round trip)
  - patch approval latency (the `pearl/approvePatches` round trip,
    when the run pauses for a patch)
  - success (did the run reach "completed")
  - retries/replans (`ExecutionReport.replans_used`, exposed on the
    wire as `replansUsed`)

Usage:
    .venv/bin/python benchmarks/run_benchmark.py --repos-dir <dir> [--wait 180]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PEARL_ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(PEARL_ROOT / ".venv" / "bin" / "python")

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".tox",
    ".eggs",
}

# (directory slug under --repos-dir, display label)
REPOS: list[tuple[str, str]] = [
    ("fastapi", "FastAPI"),
    ("flask", "Flask"),
    ("django", "Django"),
    ("react", "React"),
    ("langchain", "LangChain"),
    ("requests", "requests (medium Python project)"),
]


@dataclass
class RepoStats:
    name: str
    path: str
    total_files: int = 0
    python_files: int = 0
    total_lines: int = 0


@dataclass
class BenchmarkResult:
    name: str
    prompt: str
    planning_execution_time_s: float | None = None
    approval_latency_s: float | None = None
    stop_reason: str | None = None
    success: bool = False
    replans_used: int = 0
    steps: int = 0
    error: str | None = None


def collect_repo_stats(name: str, path: Path) -> RepoStats:
    total_files = 0
    python_files = 0
    total_lines = 0

    for item in path.rglob("*"):
        if any(part in IGNORED_DIRS for part in item.relative_to(path).parts):
            continue

        if not item.is_file():
            continue

        total_files += 1

        if item.suffix == ".py":
            python_files += 1
            try:
                with item.open(encoding="utf-8", errors="ignore") as f:
                    total_lines += sum(1 for _ in f)
            except OSError:
                pass

    return RepoStats(
        name=name,
        path=str(path),
        total_files=total_files,
        python_files=python_files,
        total_lines=total_lines,
    )


class MCPProbe:
    """
    Drives one `python -m src.mcp` subprocess over stdio, exactly as
    the VS Code extension's `MCPConnection` does.
    """

    def __init__(self, cwd: Path) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PEARL_ROOT)

        self.proc = subprocess.Popen(
            [PYTHON, "-m", "src.mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(cwd),
            env=env,
        )
        self.results: dict[int, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self._id = 0

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()

            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            with self.lock:
                self.results[obj.get("id")] = obj

    def _read_stderr(self) -> None:
        # Drain silently (stderr carries logs, not protocol data) so
        # the pipe never fills and blocks the server.
        for _ in self.proc.stderr:
            pass

    def send(
        self, method: str, params: dict[str, Any] | None = None, wait: float = 180
    ) -> tuple[dict[str, Any], float]:
        self._id += 1
        mid = self._id
        message = {
            "jsonrpc": "2.0",
            "id": mid,
            "method": method,
            "params": params or {},
        }

        start = time.time()
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

        deadline = start + wait

        while time.time() < deadline:
            with self.lock:
                if mid in self.results:
                    return self.results.pop(mid), time.time() - start

            if self.proc.poll() is not None:
                return {
                    "error": {"message": "server process exited"}
                }, time.time() - start

            time.sleep(0.15)

        return {"error": {"message": "client-side wait exceeded"}}, time.time() - start

    def close(self) -> None:
        try:
            self.proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "exit", "params": {}}) + "\n"
            )
            self.proc.stdin.flush()
        except Exception:
            pass

        time.sleep(0.5)

        try:
            self.proc.kill()
        except Exception:
            pass


def run_autonomous_benchmark(
    name: str, repo_path: Path, prompt: str, wait: float
) -> BenchmarkResult:
    result = BenchmarkResult(name=name, prompt=prompt)
    probe = MCPProbe(repo_path)

    try:
        init_response, _ = probe.send("initialize", {}, wait=60)

        if "error" in init_response:
            result.error = f"initialize failed: {init_response['error']}"
            return result

        response, elapsed = probe.send(
            "pearl/runAutonomous", {"prompt": prompt}, wait=wait
        )
        result.planning_execution_time_s = round(elapsed, 2)

        if "error" in response:
            result.error = str(response["error"])
            return result

        payload = response.get("result", {})
        result.stop_reason = payload.get("stopReason")
        result.replans_used = payload.get("replansUsed", 0)
        result.steps = len(payload.get("steps", []))

        if payload.get("stopReason") == "awaiting_approval":
            approve_response, approve_elapsed = probe.send(
                "pearl/approvePatches", {}, wait=wait
            )
            result.approval_latency_s = round(approve_elapsed, 2)

            if "error" in approve_response:
                result.error = str(approve_response["error"])
                return result

            final = approve_response.get("result", {})
            result.stop_reason = final.get("stopReason")
            result.replans_used = max(result.replans_used, final.get("replansUsed", 0))
            result.steps = max(result.steps, len(final.get("steps", [])))

        result.success = result.stop_reason == "completed"
    except Exception as exc:
        result.error = str(exc)
    finally:
        probe.close()

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repos-dir",
        required=True,
        help="Directory containing one subdirectory per benchmarked repo "
        "(fastapi/, flask/, django/, react/, langchain/, requests/).",
    )
    parser.add_argument(
        "--out",
        default=str(PEARL_ROOT / "benchmarks" / "results.json"),
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=180,
        help="Max seconds to wait for each MCP response.",
    )
    parser.add_argument(
        "--prompt",
        default="List the top-level files and directories in this project.",
        help="Autonomous-execution prompt to run against every repo.",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        help="Only benchmark these repo slugs (default: all of REPOS).",
    )
    args = parser.parse_args()

    repos_dir = Path(args.repos_dir)
    targets = REPOS if not args.only else [r for r in REPOS if r[0] in args.only]

    all_results: dict[str, list[dict[str, Any]]] = {"repos": [], "benchmarks": []}

    for slug, label in targets:
        repo_path = repos_dir / slug

        if not repo_path.exists():
            print(f"skip {label}: not found at {repo_path}", file=sys.stderr)
            continue

        print(f"=== {label} ===", file=sys.stderr)

        stats = collect_repo_stats(label, repo_path)
        print(
            f"  files={stats.total_files} python_files={stats.python_files} "
            f"lines={stats.total_lines}",
            file=sys.stderr,
        )
        all_results["repos"].append(asdict(stats))

        bench = run_autonomous_benchmark(label, repo_path, args.prompt, args.wait)
        print(
            f"  planning+execution={bench.planning_execution_time_s}s "
            f"approval={bench.approval_latency_s}s stop={bench.stop_reason} "
            f"success={bench.success} replans={bench.replans_used} "
            f"error={bench.error}",
            file=sys.stderr,
        )
        all_results["benchmarks"].append(asdict(bench))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"Results written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
