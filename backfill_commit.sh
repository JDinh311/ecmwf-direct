#!/usr/bin/env bash
# Merge data/backfill_new into data/backfill, rebuild the index, commit and push (retrying if main moved).
set -u
git config user.name "ifs-bot"
git config user.email "ifs-bot@users.noreply.github.com"
mv data/backfill_new /tmp/backfill_new
for i in 1 2 3 4 5 6; do
  mkdir -p data/backfill/runs data/backfill/status
  cp -n /tmp/backfill_new/runs/*.json data/backfill/runs/ 2>/dev/null || true
  cp /tmp/backfill_new/status_*.json data/backfill/status/ 2>/dev/null || true
  python3 backfill_index.py
  git add data/backfill
  if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
  git commit -q -m "Backfill: historical IFS ENS runs"
  if git push -q; then echo "pushed"; exit 0; fi
  echo "main moved; retrying on top of it"
  sleep $((i * 5))
  git fetch -q origin main && git reset -q --hard origin/main
done
echo "push failed after retries"; exit 1
