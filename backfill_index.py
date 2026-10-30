"""Rebuild data/backfill/index.json from the saved runs + status files."""
import json, glob, os
runs = []
for f in sorted(glob.glob("data/backfill/runs/*.json")):
    j = json.load(open(f))
    runs.append({"init_utc": j["init_utc"], "cycle": j["cycle"], "file": "runs/" + os.path.basename(f),
                 "available_utc": j.get("available_utc"), "n_dates": len(j["dates"]), "members": j["members"]})
have = {r["file"][5:-5] for r in runs}
failed, secs = set(), {}
for f in glob.glob("data/backfill/status/status_*.json"):
    st = json.load(open(f))
    failed |= {t for t in st.get("failed", []) if t not in have}
    secs.update(st.get("seconds", {}))
med = sorted(secs.values())[len(secs) // 2] if secs else None
json.dump({"runs": runs, "failed": sorted(failed), "median_run_seconds": med}, open("data/backfill/index.json", "w"), indent=1)
print(f"{len(runs)} runs in data/backfill, still missing after retries: {sorted(failed)}; median run {med}s")
