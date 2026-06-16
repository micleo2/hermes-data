# hermes-data

Historical runtime-performance measurements for the Hermes JavaScript engine,
one commit per measurement, plus the runner that produces them.

This repo is **both** the benchmark runner (the code under `runner/`, which lives
on a dedicated Raspberry Pi 5) **and** the data store (`results/<sha>.json`, one
file per benchmarked commit). Keeping them together means the Pi has exactly one
thing to clone and nothing to keep in sync.

## Why a "pull" model

The Pi used to be a registered GitHub Actions runner and CI *pushed* the
benchmark job to it. That is no longer viable. Now:

- **`.github/workflows/perf-build-rpi.yml`** (in the Hermes source repo, *not*
  here) only **builds** the release binaries (`synth`, `hermesc`) on GitHub's
  fast ARM runners and uploads them as the `hermes-bin` artifact.
- **The Pi polls** for finished builds, downloads the artifact, runs the
  benchmark locally on bare metal, and commits a JSON result back to this repo.

```
GitHub push ──▶ perf-build-rpi.yml (ARM) ──▶ artifact: synth, hermesc
                                                  │
                          Pi systemd timer (every 10 min)
                                                  │  gh run download
                                                  │  gh api commits/<sha>  (timestamp)
                                                  ▼
                       hermesc compile ─▶ synth -reps 5 ─▶ extract JSON
                                                  │
                                                  ▼
                          git commit + push ──▶ results/<sha>.json (this repo)
```

## Layout

```
hermes-data/
  LICENSE                       MIT
  README.md                     this file
  runner/
    poll-and-bench.sh           the poller (entry point)
    extract_synth_results.py    synth stdout (JSON) -> result schema
    hermes-perf.service         systemd user unit
    hermes-perf.timer           systemd user timer (every 10 min)
    synth-bench-simple/         benchmark assets -- YOU provide these:
        index.android.bundle
        synth_trace.json
  results/
    <sha>.json                  one per benchmarked commit
```

## State / dedup

There is **no local state**. A commit counts as "done" iff
`results/<sha>.json` exists in this repo. The poller pulls the repo at the start
of every run, so reimaging the Pi's SD card or running on a second machine just
works -- already-published commits are skipped.

## One-time setup on the Pi

1. **Install tooling:**
   ```bash
   sudo apt-get update
   sudo apt-get install -y gh git python3
   gh auth login        # log in as a Hermes maintainer (needs artifact read)
   ```

2. **Clone this repo** (SSH so the headless Pi can push without a prompt):
   ```bash
   git clone git@github.com:<you>/hermes-data.git ~/hermes-data
   git -C ~/hermes-data config user.name  "hermes-perf-pi"
   git -C ~/hermes-data config user.email "perf@localhost"
   chmod +x ~/hermes-data/runner/poll-and-bench.sh
   ```

3. **Add the benchmark assets** (static inputs, kept on the Pi so the
   environment stays identical across commits):
   ```bash
   # ~/hermes-data/runner/synth-bench-simple/
   #   index.android.bundle
   #   synth_trace.json
   ```

4. **Configure** (optional) `~/hermes-data/runner.env`:
   ```bash
   SOURCE_REPO=facebook/hermes
   BRANCH=static_h
   REPS=5
   ```

5. **Install and enable the systemd user units:**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ~/hermes-data/runner/hermes-perf.service ~/.config/systemd/user/
   cp ~/hermes-data/runner/hermes-perf.timer   ~/.config/systemd/user/
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
