"""Rebuild historical IFS ENS runs (00Z + 12Z) for a date range with the normal pipeline.

    python backfill.py --start 20251101 --end 20251105 --out data/backfill_part

Each run goes through fetch_ifs.py exactly as a live run does (same cities, weights, method).
Failures are retried once and listed in <out>/status_<start>.json; the job never stops on one bad run.
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
    ap.add_argument("--cycles", default="00,12")
    a = ap.parse_args()
    d0, d1 = datetime.strptime(a.start, "%Y%m%d"), datetime.strptime(a.end, "%Y%m%d")
    status = {"start": a.start, "end": a.end, "ok": [], "failed": []}
    d = d0
    while d <= d1:
        for cyc in a.cycles.split(","):
            tag = d.strftime("%Y%m%d") + cyc
            cmd = [sys.executable, "fetch_ifs.py", "--date", d.strftime("%Y%m%d"), "--cycle", cyc,
                   "--out-dir", a.out, "--wait-minutes", "0", "--settle-s", "0"]
            ok = False
            for attempt in (1, 2):
                t = time.time()
                rc = subprocess.call(cmd)
                print(f"== {tag} attempt {attempt}: rc={rc} in {time.time() - t:.0f}s", flush=True)
                if rc == 0 and os.path.exists(os.path.join(a.out, "runs", tag + ".json")):
                    ok = True
                    break
                time.sleep(30)
            (status["ok"] if ok else status["failed"]).append(tag)
        d += timedelta(days=1)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, f"status_{a.start}.json"), "w") as f:
        json.dump(status, f, indent=1)
    print(f"done: {len(status['ok'])} ok, {len(status['failed'])} failed {status['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
