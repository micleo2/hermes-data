#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# Pull model for Hermes perf tracking.
#
# The Raspberry Pi 5 is no longer a registered GitHub Actions runner, so CI
# cannot push benchmark jobs to it. Instead this script (driven by a systemd
# timer) PULLS finished builds:
#
#   1. List recent successful runs of the perf-build workflow on the branch.
#   2. For each commit we have not benchmarked yet (state derived from the
#      data-store repo -- no local state to corrupt), download the prebuilt
#      synth + hermesc artifact.
#   3. Compile the benchmark bundle to bytecode, replay the trace with synth.
#   4. Extract the timing JSON and commit it to the data-store repo, building
#      up a commit-per-measurement history.
#
# Requires: gh (authenticated as a repo maintainer), git, python3.
# (gh's built-in --jq is used for JSON; no standalone jq needed.)

set -euo pipefail

# --- Configuration (override via the systemd EnvironmentFile) ----------------

# Public Hermes repo that runs perf-build.yml.
SOURCE_REPO="${SOURCE_REPO:-facebook/hermes}"
# Branch whose commits we track.
BRANCH="${BRANCH:-static_h}"
# Workflow file name (as it lives in .github/workflows/).
WORKFLOW="${WORKFLOW:-perf-build-rpi.yml}"
# Artifact name produced by perf-build.yml.
ARTIFACT="${ARTIFACT:-hermes-bin}"

# How many recent successful runs to consider each poll.
MAX_RUNS="${MAX_RUNS:-30}"
# Benchmark repetitions (synth reports the median rep).
REPS="${REPS:-5}"

# The repo uses two branches:
#   main  -- this script + bench assets (checked out at, e.g., ~/hermes-data)
#   data  -- an orphan branch holding only results/<sha>.json
# On the Pi the data branch is checked out as a git WORKTREE (one clone, two
# dirs), so results land on the data branch automatically. RUNNER_DIR is this
# script's location on main; DATA_REPO_DIR is the data worktree.
#   git -C ~/hermes-data worktree add ~/hermes-data-results data
RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_REPO_DIR="${DATA_REPO_DIR:-$HOME/hermes-data-results}"
RESULTS_SUBDIR="${RESULTS_SUBDIR:-results}"
BENCH_DIR="${BENCH_DIR:-$RUNNER_DIR/synth-bench-simple}"
EXTRACT="${EXTRACT:-$RUNNER_DIR/extract_synth_results.py}"

# --- Setup -------------------------------------------------------------------

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

for tool in gh git python3; do
  command -v "$tool" >/dev/null 2>&1 || { log "FATAL: '$tool' not found"; exit 1; }
done
[ -d "$BENCH_DIR" ] || { log "FATAL: bench dir not found: $BENCH_DIR"; exit 1; }
[ -f "$EXTRACT" ] || { log "FATAL: extractor not found: $EXTRACT"; exit 1; }

# --- Refresh the data-store repo so dedup reflects what's already published ---

log "Updating data repo at $DATA_REPO_DIR"
git -C "$DATA_REPO_DIR" pull --ff-only --quiet
mkdir -p "$DATA_REPO_DIR/$RESULTS_SUBDIR"

# A commit is "done" iff results/<sha>.json already exists in the data repo.
have_result() { [ -f "$DATA_REPO_DIR/$RESULTS_SUBDIR/$1.json" ]; }

# --- Find candidate runs -----------------------------------------------------

log "Listing successful '$WORKFLOW' runs on '$BRANCH' (limit $MAX_RUNS)"
# Oldest-first so the data-store history is chronological and a backlog drains
# in commit order.
mapfile -t runs < <(
  gh run list \
    --repo "$SOURCE_REPO" \
    --workflow "$WORKFLOW" \
    --branch "$BRANCH" \
    --status success \
    --limit "$MAX_RUNS" \
    --json databaseId,headSha \
    --jq 'reverse | .[] | "\(.databaseId)\t\(.headSha)"'
)

if [ "${#runs[@]}" -eq 0 ]; then
  log "No successful runs found."
  exit 0
fi

# --- Benchmark each new commit -----------------------------------------------

# Benchmark a single run. Returns non-zero on failure so the caller can skip it
# without aborting the whole poll.
benchmark_run() {
  local run_id="$1" sha="$2"
  local rundir="$WORKDIR/$run_id"
  mkdir -p "$rundir"

  log "Downloading artifact '$ARTIFACT' from run $run_id ($sha)"
  if ! gh run download "$run_id" --repo "$SOURCE_REPO" \
        --name "$ARTIFACT" --dir "$rundir"; then
    log "  artifact unavailable (expired?), skipping"
    return 1
  fi

  # The run's headSha is the built commit. Fetch its commit timestamp from the
  # API so the history is plottable against time.
  local timestamp
  timestamp="$(gh api "repos/$SOURCE_REPO/commits/$sha" \
    --jq '.commit.committer.date')"

  chmod +x "$rundir/synth" "$rundir/hermesc"

  log "  compiling benchmark bundle -> bytecode"
  "$rundir/hermesc" -O -w -emit-binary \
    -out "$rundir/index.android.hbc" \
    "$BENCH_DIR/index.android.bundle"

  log "  running synth ($REPS reps)"
  "$rundir/synth" \
    -reps "$REPS" \
    "$BENCH_DIR/synth_trace.json" \
    "$rundir/index.android.hbc" \
    2>"$rundir/synth_stderr.txt" \
    >"$rundir/synth_raw_output.txt"

  log "  extracting results"
  local out="$DATA_REPO_DIR/$RESULTS_SUBDIR/$sha.json"
  python3 "$EXTRACT" "$rundir/synth_raw_output.txt" "$sha" "$timestamp" > "$out"

  log "  committing $sha to data repo"
  git -C "$DATA_REPO_DIR" add "$RESULTS_SUBDIR/$sha.json"
  git -C "$DATA_REPO_DIR" commit --quiet \
    -m "perf: $sha @ ${timestamp}"
  # Retry the push once against concurrent updates.
  if ! git -C "$DATA_REPO_DIR" push --quiet; then
    log "  push rejected, rebasing and retrying"
    git -C "$DATA_REPO_DIR" pull --rebase --quiet
    git -C "$DATA_REPO_DIR" push --quiet
  fi
  log "  done: $sha"
}

new_count=0
for entry in "${runs[@]}"; do
  run_id="${entry%%$'\t'*}"
  head_sha="${entry##*$'\t'}"

  # Dedup by the run's commit; skip without downloading if already published.
  if have_result "$head_sha"; then
    continue
  fi

  if benchmark_run "$run_id" "$head_sha"; then
    new_count=$((new_count + 1))
  else
    log "Run $run_id skipped due to error; continuing"
  fi
done

log "Poll complete. Newly benchmarked: $new_count"
