#!/usr/bin/env python3
"""Snapshot every router's decisions and every border's RTR state.

    sample.py <plan-dir> <phase>   -> one JSON line on stdout

Per router: the external routes it holds as best paths ("prefix|origin") and
the BGP prefixes in its kernel FIB. Per border: whether the RTR session to the
cache is up and how many IPv4 VRPs the router holds.
"""
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time

PLAN = json.load(open(f"{sys.argv[1]}/plan.json"))
PHASE = sys.argv[2]
OURS = {r["prefix"] for r in PLAN["routes"]}


def sh(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""


def vty(n, cmd):
    return sh(["ip", "netns", "exec", f"ns{n}", "vtysh", "-N", f"ns{n}", "-c", cmd])


def router(n):
    held = []
    try:
        rib = json.loads(vty(n, "show ip bgp json") or "{}").get("routes", {})
    except json.JSONDecodeError:
        rib = {}
    for pfx, paths in rib.items():
        if pfx not in OURS:
            continue
        for p in paths:
            if p.get("valid") and p.get("bestpath"):
                asp = (p.get("path") or p.get("aspath", {}).get("string", "")).split()
                if asp:
                    held.append(f"{pfx}|{asp[-1]}")
    fib = [ln.split()[0] for ln in sh(["ip", "-n", f"ns{n}", "route", "show", "proto", "bgp"]).splitlines() if ln]
    out = {"held": sorted(held), "fib": sorted(f for f in fib if f in OURS)}
    if n in PLAN["borders"]:
        conn = vty(n, "show rpki cache-connection")
        table = vty(n, "show rpki prefix-table")     # FRR 8.4 has no prefix-count
        out["rtr_up"] = "Connected to group" in conn
        out["vrps"] = sum(1 for ln in table.splitlines() if re.match(r"\d", ln))
        out["rtr_text"] = conn.strip()[:200]
    return n, out


def main():
    t = time.time()
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(ex.map(router, PLAN["nodes"]))
    print(json.dumps({"t": round(t, 2), "dt": round(time.time() - t, 2), "phase": PHASE,
                      "routers": {str(k): v for k, v in res.items()}}), flush=True)


if __name__ == "__main__":
    main()
