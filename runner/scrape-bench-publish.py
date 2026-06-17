#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""One scrape -> benchmark -> publish pass for Hermes perf tracking.

The Raspberry Pi 5 is not a GitHub Actions runner, so CI cannot push benchmark
jobs to it. Instead this script does a single pass and exits; a systemd timer
(see setup-systemd.py) runs it periodically -- that timer is what "polls". Each
pass:

  1. List recent successful runs of the perf-build workflow on the branch.
  2. For each commit not benchmarked yet (state derived from the data branch --
     no local state to corrupt), download the prebuilt synth + hermesc artifact.
  3. Compile the benchmark bundle to bytecode, replay the trace with synth.
  4. Extract the timing JSON and publish it -- commit + push to the data branch,
     building up a commit-per-measurement history.

Configuration is via CLI flags (see --help) or their built-in defaults.

Requires: gh (authenticated as a repo maintainer), git.
"""

import argparse
import json
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# This script lives at <data-repo>/main/runner/scrape-bench-publish.py, so its
# directory is on sys.path and the sibling extractor imports cleanly.
from extract_synth_results import extract


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def sh(cmd, *, cwd=None, check=True, capture=False):
    """Run a command (list of args). Returns the CompletedProcess."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "bench_dir",
        metavar="BENCH_DIR",
        help="directory holding index.android.bundle + synth_trace.json "
        "(e.g. ~/hermes-data/simple-rn-app)",
    )
    p.add_argument(
        "output_dir",
        metavar="OUTPUT_DIR",
        help="data-branch worktree where results/<sha>.json are written and "
        "committed (e.g. ~/hermes-data/data) -- required, no default",
    )
    p.add_argument(
        "--repo",
        default="facebook/hermes",
        help="source repo running the build workflow (default: facebook/hermes)",
    )
    p.add_argument(
        "--branch",
        default="static_h",
        help="branch whose commits to track (default: static_h)",
    )
    p.add_argument(
        "--workflow",
        default="perf-build-rpi.yml",
        help="build workflow file name (default: perf-build-rpi.yml)",
    )
    p.add_argument(
        "--artifact",
        default="hermes-bin",
        help="artifact name to download (default: hermes-bin)",
    )
    p.add_argument(
        "--max-runs",
        type=int,
        default=30,
        help="how many recent successful runs to consider (default: 30)",
    )
    p.add_argument(
        "--reps",
        type=int,
        default=5,
        help="benchmark repetitions; synth reports the median (default: 5)",
    )
    p.add_argument(
        "--results-subdir",
        default="results",
        help="results subdir within the data worktree (default: results)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="behave exactly like a normal run, except instead of "
        "writing/committing/pushing each result, print it to stdout",
    )
    return p.parse_args()


def list_runs(args):
    """Return successful runs oldest-first as [{databaseId, headSha}, ...]."""
    out = sh(
        [
            "gh",
            "run",
            "list",
            "--repo",
            args.repo,
            "--workflow",
            args.workflow,
            "--branch",
            args.branch,
            "--status",
            "success",
            "--limit",
            str(args.max_runs),
            "--json",
            "databaseId,headSha",
        ],
        capture=True,
    ).stdout
    runs = json.loads(out)
    # gh returns newest-first; reverse so a backlog drains in commit order.
    runs.reverse()
    return runs


def benchmark_run(args, rundir, run_id, sha, bench_dir, results_dir, data_dir):
    """Benchmark one run into rundir. Returns True if a result was committed."""
    log(f"Downloading artifact '{args.artifact}' from run {run_id} ({sha})")
    dl = sh(
        [
            "gh",
            "run",
            "download",
            run_id,
            "--repo",
            args.repo,
            "--name",
            args.artifact,
            "--dir",
            str(rundir),
        ],
        check=False,
        capture=True,
    )
    if dl.returncode != 0:
        log("  artifact unavailable (expired?), skipping")
        return False

    # headSha is the built commit; fetch its commit timestamp for the history.
    timestamp = sh(
        [
            "gh",
            "api",
            f"repos/{args.repo}/commits/{sha}",
            "--jq",
            ".commit.committer.date",
        ],
        capture=True,
    ).stdout.strip()

    synth = rundir / "synth"
    hermesc = rundir / "hermesc"
    for exe in (synth, hermesc):
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    hbc = rundir / "index.android.hbc"
    hermesc_cmd = [
        str(hermesc),
        "-O",
        "-w",
        "-emit-binary",
        "-out",
        str(hbc),
        str(bench_dir / "index.android.bundle"),
    ]
    log("  compiling benchmark bundle -> bytecode")
    log("    " + shlex.join(hermesc_cmd))
    sh(hermesc_cmd)

    synth_cmd = [
        str(synth),
        "-reps",
        str(args.reps),
        str(bench_dir / "synth_trace.json"),
        str(hbc),
    ]
    log(f"  running synth ({args.reps} reps)")
    log("    " + shlex.join(synth_cmd))
    raw_path = rundir / "synth_raw_output.txt"
    err_path = rundir / "synth_stderr.txt"
    with open(raw_path, "w") as raw, open(err_path, "w") as err:
        subprocess.run(synth_cmd, stdout=raw, stderr=err, check=True)

    log("  extracting results")
    result = extract(raw_path.read_text(), sha, timestamp)
    blob = json.dumps(result, indent=2, sort_keys=True) + "\n"

    if args.dry_run:
        log(f"  DRY RUN: would publish {sha}; result follows:")
        print(blob, end="")
        return True

    (results_dir / f"{sha}.json").write_text(blob)

    log(f"  committing {sha} to data repo")
    rel = str(Path(args.results_subdir) / f"{sha}.json")
    sh(["git", "-C", str(data_dir), "add", rel])
    sh(
        [
            "git",
            "-C",
            str(data_dir),
            "commit",
            "--quiet",
            "-m",
            f"perf: {sha} @ {timestamp}",
        ]
    )
    # Retry the push once against concurrent updates.
    if sh(["git", "-C", str(data_dir), "push", "--quiet"], check=False).returncode:
        log("  push rejected, rebasing and retrying")
        sh(["git", "-C", str(data_dir), "pull", "--rebase", "--quiet"])
        sh(["git", "-C", str(data_dir), "push", "--quiet"])
    log(f"  done: {sha}")
    return True


def main():
    args = parse_args()
    data_dir = Path(args.output_dir)
    bench_dir = Path(args.bench_dir)
    results_dir = data_dir / args.results_subdir

    for tool in ("gh", "git"):
        if shutil.which(tool) is None:
            sys.exit(f"FATAL: '{tool}' not found")
    for f in (bench_dir / "index.android.bundle", bench_dir / "synth_trace.json"):
        if not f.exists():
            sys.exit(f"FATAL: bench asset not found: {f}")

    # Refresh the data worktree so dedup reflects what's already published.
    log(f"Updating data repo at {data_dir}")
    sh(["git", "-C", str(data_dir), "pull", "--ff-only", "--quiet"])
    results_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        log("DRY RUN: results will be printed, not written/committed/pushed")

    def have_result(sha):
        return (results_dir / f"{sha}.json").exists()

    log(
        f"Listing successful '{args.workflow}' runs on '{args.branch}' "
        f"(limit {args.max_runs})"
    )
    runs = list_runs(args)
    if not runs:
        log("No successful runs found.")
        return

    new_count = 0
    for run in runs:
        run_id = str(run["databaseId"])
        sha = run["headSha"]
        # Dedup by the run's commit; skip without downloading if published.
        if have_result(sha):
            continue
        try:
            # A fresh temp dir per run (under /tmp on the Pi) holds the
            # downloaded binaries + compiled bytecode, and is removed as soon
            # as the commit is recorded -- nothing accumulates across runs.
            with tempfile.TemporaryDirectory(prefix="hermes-perf-") as tmp:
                if benchmark_run(
                    args, Path(tmp), run_id, sha, bench_dir, results_dir, data_dir
                ):
                    new_count += 1
        except Exception as e:  # noqa: BLE001 -- one bad run shouldn't abort
            log(f"Run {run_id} skipped due to error: {e}")

    log(f"Poll complete. Newly benchmarked: {new_count}")


if __name__ == "__main__":
    main()
