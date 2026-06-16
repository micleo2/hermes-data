# hermes-data

Historical runtime-performance measurements for the Hermes JavaScript engine,
one commit per measurement, plus the runner that produces them.

This repo has **two branches**:

- **`main`** — the benchmark runner (the code under `runner/`), which lives on a
  dedicated Raspberry Pi 5.
- **`data`** — an orphan branch (no shared history with `main`) holding only
  `results/<sha>.json`, one file per benchmarked commit. Keeping the
  machine-generated measurement stream off `main` keeps the code history clean.

On the Pi both branches are checked out at once via a single clone + a `git
worktree`, so the runner reads scripts from `main` and writes results onto
`data` without juggling two clones.

## How it works

`.github/workflows/perf-build-rpi.yml` (in the Hermes source repo, *not* here)
builds the release binaries on GitHub's ARM runners. The Pi polls for finished
builds, downloads the artifact, benchmarks on bare metal, and commits the result
here.

## Layout

```
main branch                         data branch (orphan)
  LICENSE          MIT                 results/
  README.md        this file            <sha>.json   one per benchmarked commit
  .gitignore
  runner/
    poll-and-bench.sh        the poller (entry point)
    extract_synth_results.py synth stdout (JSON) -> result schema
    hermes-perf.service      systemd user unit
    hermes-perf.timer        systemd user timer (every 10 min)
    synth-bench-simple/      benchmark assets -- YOU provide these:
        index.android.bundle
        synth_trace.json
```

On the Pi these are two sibling worktrees under one parent, backed by one clone:

```
~/hermes-data/
  main/    worktree of the main branch -- runner scripts + bench assets
  data/    worktree of the data branch -- results/<sha>.json
  runner.env   (optional) config, kept outside both worktrees
```

## State / dedup

There is **no local state**. A commit counts as "done" iff
`results/<sha>.json` exists on the `data` branch. The poller pulls the data
worktree at the start of every run, so reimaging the Pi's SD card or running on a
second machine just works -- already-published commits are skipped.

## One-time setup on the Pi

1. **Install tooling:**
   ```bash
   sudo apt-get update
   sudo apt-get install -y gh git python3
   gh auth login        # log in as a Hermes maintainer (needs artifact read)
   ```

2. **Set up `main/` and `data/` as sibling worktrees** under `~/hermes-data`
   (SSH so the headless Pi can push without a prompt). The bare-repo variant
   keeps the two worktrees symmetric (neither holds the object store):
   ```bash
   mkdir ~/hermes-data && cd ~/hermes-data
   git clone --bare git@github.com:<you>/hermes-data.git .bare
   echo "gitdir: ./.bare" > .git
   git worktree add main main      # main branch: runner scripts
   git worktree add data data      # data branch: results

   # identity for the result commits (or use your account's noreply email)
   git -C ~/hermes-data/data config user.name  "hermes-perf-pi"
   git -C ~/hermes-data/data config user.email "perf@localhost"
   chmod +x ~/hermes-data/main/runner/poll-and-bench.sh
   ```
   (Simpler alternative: `git clone … ~/hermes-data/main` then
   `git -C ~/hermes-data/main worktree add ../data data` -- works the same, but
   `main/` then holds the `.git` object store and must not be deleted.)
   If your layout differs, set `DATA_REPO_DIR` in `runner.env` (step 4).

3. **Add the benchmark assets** (static inputs, kept on the Pi so the
   environment stays identical across commits):
   ```bash
   # ~/hermes-data/main/runner/synth-bench-simple/
   #   index.android.bundle
   #   synth_trace.json
   ```

4. **Configure** (optional) `~/hermes-data/runner.env`:
   ```bash
   SOURCE_REPO=facebook/hermes
   BRANCH=static_h
   REPS=5
   # Override only if the data worktree isn't the default ~/hermes-data/data
   # DATA_REPO_DIR=/home/pi/hermes-data/data
   ```

5. **Install and enable the systemd user units:**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ~/hermes-data/main/runner/hermes-perf.service ~/.config/systemd/user/
   cp ~/hermes-data/main/runner/hermes-perf.timer   ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now hermes-perf.timer

   # Keep user services running when nobody is logged in (headless Pi):
   sudo loginctl enable-linger "$USER"
   ```

## Operating

```bash
# Trigger a poll right now:
systemctl --user start hermes-perf.service

# Watch logs:
journalctl --user -u hermes-perf.service -f

# When does it next fire?
systemctl --user list-timers hermes-perf.timer
```

## Result schema

One file per commit, `results/<sha>.json`:

```json
{
  "sha": "...",
  "timestamp": "2026-06-16T12:00:00+00:00",
  "totalTime": 1.234,
  "totalCPUTime": 1.230,
  "totalGCTime": 0.045,
  "totalGCCPUTime": 0.044,
  "numCollections": 12,
  "maxGCPause": 0.003,
  "finalHeapSize": "...",
  "peakAllocatedBytes": "...",
  "peakRSS": 12345678,
  "heapSize": 12345678,
  "perfEvents": {}
}
```

`totalTime` (wall-clock seconds for the median rep) is the primary runtime
metric. `synth` reports the median rep by `totalTime`, so a single value per
commit is stable and comparable.
