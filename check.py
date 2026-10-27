#!/usr/bin/env python3
"""Exit 0 if a sample shows the lab converged on one list, else 1.

    check.py <plan-dir> <old|new> '<sample json line>' [rtr_only]

Converged: every border's RTR session is up and holds that list's VRP count,
and every router holds exactly the routes that list accepts (RFC 6811 states
from gen_rov.py; Invalid is dropped, Valid and NotFound are kept).
"""
import json
import sys

d, which, line = sys.argv[1], sys.argv[2], sys.argv[3]
plan = json.load(open(f"{d}/plan.json"))
nvrp = len(json.load(open(f"{d}/vrp-{which}.json"))["roas"])
want = sorted(f"{r['prefix']}|{r['origin']}" for r in plan["routes"] if r[f"state_{which}"] != "invalid")
try:
    s = json.loads(line)
except json.JSONDecodeError:
    sys.exit(1)
for n, r in s["routers"].items():
    if int(n) in plan["borders"] and not (r.get("rtr_up") and r.get("vrps") == nvrp):
        sys.exit(1)
    if len(sys.argv) < 5 and r["held"] != want:      # 5th arg rtr_only: skip routes
        sys.exit(1)
sys.exit(0)
