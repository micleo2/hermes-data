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
  poll-and-bench.py        the poller (entry point)
  extract_synth_results.py synth stdout (JSON) -> result schema
  setup-systemd.py         generates + enables the systemd user timer
```

The benchmark assets (`index.android.bundle` + the ~474 MB `synth_trace.json`)
are **not** in git -- the trace is far over GitHub's 100 MB file limit. They ship
as assets of the `bench-assets-v1` GitHub Release and live in a sibling dir on
the Pi (see below).

`data` branch (orphan):

```
results/
  <sha>.json     one per benchmarked commit
```

On the Pi, the two branches are sibling worktrees under one parent, alongside
the (untracked) benchmark assets dir:

```
~/hermes-data/
  main/           worktree of the main branch -- runner scripts
  data/           worktree of the data branch -- results/<sha>.json
  simple-rn-app/  benchmark assets (from the release) -- index.android.bundle,
                  synth_trace.json
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
   (SSH so the headless Pi can push without a prompt). Clone `main`, then add the
   `data` branch as a sibling worktree:
   ```bash
   git clone git@github.com:<you>/hermes-data.git ~/hermes-data/main
   git -C ~/hermes-data/main worktree add ../data data

   # identity for the result commits (or use your account's noreply email)
   git -C ~/hermes-data/data config user.name  "hermes-perf-pi"
   git -C ~/hermes-data/data config user.email "perf@localhost"
   ```
   `~/hermes-data/main` holds the `.git` object store -- don't delete it.
   The defaults (`facebook/hermes`, `static_h`, 5 reps) need no config; to
   override, pass poller flags via the setup script in step 4 (e.g. `-- --reps 10`).

3. **Download the benchmark assets** from the release into the sibling
   `simple-rn-app/` dir (the repo is private, so this uses the Pi's `gh` auth):
   ```bash
   mkdir -p ~/hermes-data/simple-rn-app
   gh release download bench-assets-v1 --repo <you>/hermes-data \
     --dir ~/hermes-data/simple-rn-app
   ```
   A new benchmark trace = a new `bench-assets-vN` tag (results aren't comparable
   across trace versions).

4. **Install and enable the systemd timer** with the setup script (generates the
   `.service` + `.timer`, reloads, and enables). It passes `~/hermes-data/simple-rn-app`
   to the poller as the required bench-dir operand. `--linger` keeps it running
   when nobody is logged in (headless Pi):
   ```bash
   python3 ~/hermes-data/main/runner/setup-systemd.py --linger
   # custom cadence: python3 .../setup-systemd.py --interval-min 30 --linger
   # poller flags go after --: python3 .../setup-systemd.py --linger -- --reps 10
   ```
   Default poll interval is 15 minutes. Re-run any time to change the interval
   or the poller flags.

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
