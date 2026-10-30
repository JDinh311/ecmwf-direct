"""Rebuild historical IFS ENS runs (00Z + 12Z) for a date range with the normal pipeline.

    python backfill.py --start 20251101 --end 20251101 --out data/backfill_part --have data/backfill/runs

Each run goes through fetch_ifs.py exactly as a live run does (same cities, weights, method).
Runs already saved in --have are skipped (so a re-run only fills gaps). A run that fails is retried
once; each attempt is capped by --run-timeout minutes. Status goes to <out>/status_<start>.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="data/backfill_part")
    ap.add_argument("--have", default="data/backfill/runs")
    ap.add_argument("--cycles", default="00,12")
    ap.add_argument("--run-timeout", type=int, default=40, help="minutes per attempt")
    ap.add_argument("--sources", default="google,azure", help="mirrors for archived runs (AWS throttles old dates)")
    a = ap.parse_args()
    d0, d1 = datetime.strptime(a.start, "%Y%m%d"), datetime.strptime(a.end, "%Y%m%d")
    status = {"start": a.start, "end": a.end, "ok": [], "skipped": [], "failed": [], "seconds": {}}
    d = d0
    while d <= d1:
        for cyc in a.cycles.split(","):
            tag = d.strftime("%Y%m%d") + cyc
            if os.path.exists(os.path.join(a.have, tag + ".json")):
                status["skipped"].append(tag)
                continue
            cmd = [sys.executable, "fetch_ifs.py", "--date", d.strftime("%Y%m%d"), "--cycle", cyc,
                   "--out-dir", a.out, "--wait-minutes", "0", "--settle-s", "0", "--sources", a.sources]
            ok = False
            for attempt in (1, 2):
                t = time.time()
                try:
                    rc = subprocess.call(cmd, timeout=a.run_timeout * 60)
                except subprocess.TimeoutExpired:
                    rc = "timeout"
                secs = round(time.time() - t)
                print(f"== {tag} attempt {attempt}: rc={rc} in {secs}s", flush=True)
                if rc == 0 and os.path.exists(os.path.join(a.out, "runs", tag + ".json")):
                    ok = True
                    status["seconds"][tag] = secs
                    break
                time.sleep(20)
            (status["ok"] if ok else status["failed"]).append(tag)
        d += timedelta(days=1)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, f"status_{a.start}.json"), "w") as f:
        json.dump(status, f, indent=1)
    print(f"done: {len(status['ok'])} ok, {len(status['skipped'])} already had, {len(status['failed'])} failed {status['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
