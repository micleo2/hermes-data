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

`main` branch:

```
LICENSE          MIT
README.md        this file
.gitignore
runner/
  poll-and-bench.sh        the poller (entry point)
  extract_synth_results.py synth stdout (JSON) -> result schema
  setup-systemd.py         generates + enables the systemd user timer
  synth-bench-simple/      benchmark assets (committed):
      index.android.bundle
      synth_trace.json
```

`data` branch (orphan):

```
results/
  <sha>.json     one per benchmarked commit
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
   The bench assets and scripts come with the `main` worktree; the defaults
   (`facebook/hermes`, `static_h`, 5 reps) need no config. To override, drop a
   `~/hermes-data/runner.env` with `KEY=value` lines (e.g. `REPS=10`).

3. **Install and enable the systemd timer** with the setup script (generates the
   `.service` + `.timer`, reloads, and enables). `--linger` keeps it running
   when nobody is logged in (headless Pi):
   ```bash
   python3 ~/hermes-data/main/runner/setup-systemd.py --linger
   # custom cadence: python3 .../setup-systemd.py --interval-min 30 --linger
   ```
   Default poll interval is 15 minutes. Re-run any time to change it.

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
