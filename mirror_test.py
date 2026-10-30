"""Which mirror serves archived IFS ENS runs fastest, without throttling? (diagnostic)"""
import time, sys, os, tempfile
import requests
from ecmwf.opendata import Client
DATES = [("20260331", 0), ("20251215", 12), ("20241215", 0)]
SRC = ["azure", "google", "aws"]
for d, h in DATES:
    for src in SRC:
        tmp = tempfile.mkdtemp()
        for step in (120, 240):
            t = time.time()
            try:
                c = Client(source=src, model="ifs", resol="0p25", maximum_retries=1, retry_after=5)
                c.retrieve(date=d, time=h, stream="enfo", type="pf", param="2t", step=step, target=os.path.join(tmp, f"s{step}.grib2"))
                sz = os.path.getsize(os.path.join(tmp, f"s{step}.grib2")) / 1e6
                print(f"RESULT {d} {h:02d}Z step {step:3d} {src:6s} OK   {sz:5.1f} MB in {time.time()-t:5.1f}s", flush=True)
            except Exception as e:
                print(f"RESULT {d} {h:02d}Z step {step:3d} {src:6s} FAIL {time.time()-t:5.1f}s {str(e)[:150]}", flush=True)
