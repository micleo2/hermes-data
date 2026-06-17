#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Extract structured benchmark results from synth's stdout.

`synth -reps N` prints the GC/perf stats of the *median* rep (median by
"totalTime") as a single pretty-printed JSON object. See
GCBase::printStats() and TraceInterpreter::mergeGCStats() in the Hermes
source. The headline runtime metric is general.totalTime (wall seconds).

Usage:
    extract_synth_results.py <synth_raw_output.txt> <sha> <timestamp>

Emits a flat JSON object on stdout, suitable for committing one-per-commit
to the perf data-store repo.
"""

import json
import sys


def load_synth_json(text):
    """Parse the JSON object synth prints, tolerating leading/trailing noise."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to slicing out the outermost {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in synth output")
    return json.loads(text[start : end + 1])


def extract(raw_text, sha, timestamp):
    """Build the flat result dict from synth's raw stdout text."""
    stats = load_synth_json(raw_text)
    general = stats.get("general", {})
    heap_info = stats.get("heapInfo", {})

    return {
        "sha": sha,
        "timestamp": timestamp,
        # Primary metric: wall-clock seconds for the median rep.
        "totalTime": general.get("totalTime"),
        "totalCPUTime": general.get("totalCPUTime"),
        "totalGCTime": general.get("totalGCTime"),
        "totalGCCPUTime": general.get("totalGCCPUTime"),
        "numCollections": general.get("numCollections"),
        "maxGCPause": general.get("maxGCPause"),
        "finalHeapSize": general.get("finalHeapSize"),
        "peakAllocatedBytes": general.get("peakAllocatedBytes"),
        "peakRSS": heap_info.get("Peak RSS"),
        "heapSize": heap_info.get("Heap size"),
        # Keep any perfEvent_* counters Linux perf inserted into "general".
        "perfEvents": {k: v for k, v in general.items() if k.startswith("perfEvent_")},
    }


def main():
    if len(sys.argv) != 4:
        sys.stderr.write(
            "usage: extract_synth_results.py <raw_output> <sha> <timestamp>\n"
        )
        return 2

    raw_path, sha, timestamp = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(raw_path, "r") as f:
        result = extract(f.read(), sha, timestamp)

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
