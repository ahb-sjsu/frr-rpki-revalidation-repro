#!/usr/bin/env python3
"""Summarize one run directory written by run_rov_one.sh.

    analyze_rov.py <run-dir>   -> one JSON object on stdout

freeze runs: during the frozen phase, the share of samples in which every
border's RTR session was up, and per router the wrong accepts and wrong drops
against the current list (and how many wrong drops still have a covering route
in the FIB). After the cache takes the current list: whether routers correct
themselves within the AUTO_S window, and then, after an operator soft-in at
every border, per router the seconds until its decisions first match the
current list (null if they never do).

bgpfirst runs: the routes were accepted before the cache started, so the
planted wrong accepts are the routes Invalid under the current list; sync_s is
the seconds from the cache start until every border holds the list. The
AUTO_S window and the soft-in are summarized as for freeze.

cut runs: per border, seconds from the cut until the RTR session is reported
down and until the router holds no VRPs; and the wrong accepts (routes Invalid
under the current list that are held) over time.
"""
import ipaddress
import json
import sys

d = sys.argv[1]
plan = json.load(open(f"{d}/plan.json"))
ev = {}
for ln in open(f"{d}/events.jsonl"):
    e = json.loads(ln)
    ev.setdefault(e["event"], e["t"])
S = [json.loads(ln) for ln in open(f"{d}/samples.jsonl") if ln.startswith("{")]
key = lambda r: f"{r['prefix']}|{r['origin']}"
inv_new = {key(r) for r in plan["routes"] if r["state_new"] == "invalid"}
ok_new = {key(r) for r in plan["routes"] if r["state_new"] != "invalid"}
wd_routes = {key(r): r for r in plan["routes"] if r["state_new"] != "invalid" and r["state_old"] == "invalid"}


def covered(fib, pfx):
    net = ipaddress.ip_network(pfx)
    return any(net != ipaddress.ip_network(f) and net.subnet_of(ipaddress.ip_network(f)) for f in fib)


def errors(r):
    held = set(r["held"])
    wd = ok_new - held
    return {"wrong_accept": len(held & inv_new), "wrong_drop": len(wd),
            "wrong_drop_covered": sum(covered(r["fib"], k.split("|")[0]) for k in wd)}


out = {"topo": plan["topo"], "n": len(plan["nodes"]), "diameter": plan["diameter"],
       "borders": plan["borders"], "rr": plan["rr"], "events": ev,
       "planted_wrong_accept": len({key(r) for r in plan["routes"] if r["state_new"] == "invalid" and r["state_old"] != "invalid"}),
       "planted_wrong_drop": len(wd_routes)}

if "publish_frozen" in ev:
    fr = [s for s in S if s["phase"] == "frozen"]
    out["frozen_samples"] = len(fr)
    out["frozen_all_rtr_up"] = sum(all(s["routers"][str(b)].get("rtr_up") for b in plan["borders"]) for s in fr)
    out["frozen_errors_last"] = {n: errors(r) for n, r in fr[-1]["routers"].items()} if fr else None

if "cache_start" in ev:
    # bgpfirst: every route was accepted as NotFound before the cache started, so each
    # route that is Invalid under the current list starts as a wrong accept
    out["planted_wrong_accept"] = len(inv_new)
    out["planted_wrong_drop"] = 0
    out["sync_s"] = round(ev["rtr_synced"] - ev["cache_start"], 1) if "rtr_synced" in ev else None

if "publish_frozen" in ev or "cache_start" in ev:
    auto = [s for s in S if s["phase"] == "auto"]
    out["auto_samples"] = len(auto)
    out["auto_recovered"] = "recovered_auto" in ev
    out["auto_errors_last"] = {n: errors(r) for n, r in auto[-1]["routers"].items()} if auto else None
    rec = [s for s in S if s["phase"] == "recover"]
    t_op = ev.get("operator_softin")
    first = {}
    for s in rec:
        for n, r in s["routers"].items():
            if n not in first and set(r["held"]) == ok_new:
                first[n] = round(s["t"] - t_op, 1)
    out["recover_s"] = {str(n): first.get(str(n)) for n in plan["nodes"]}
    out["recover_errors_last"] = {n: errors(r) for n, r in rec[-1]["routers"].items()} if rec else None
    out["recovered_all"] = "recovered" in ev
    # per route class, at the first border, how many routes are still decided
    # as the stale list would decide them at the end of the recovery window (freeze only:
    # bgpfirst has no stale list)
    if rec and "publish_frozen" in ev:
        held = set(rec[-1]["routers"][str(plan["borders"][0])]["held"])
        by = {}
        for r in plan["routes"]:
            if r["state_old"] == r["state_new"] or (r["state_old"] == "invalid") == (r["state_new"] == "invalid"):
                continue
            k = key(r)
            still_wrong = (k in held) if r["state_new"] == "invalid" else (k not in held)
            b = by.setdefault(r["kind"], [0, 0])
            b[0] += still_wrong
            b[1] += 1
        out["recover_still_wrong_by_kind"] = by

if "cut" in ev:
    ct = [s for s in S if s["phase"] == "cut"]
    t_down, t_empty = {}, {}
    for s in ct:
        for b in plan["borders"]:
            r = s["routers"][str(b)]
            if b not in t_down and not r.get("rtr_up"):
                t_down[b] = round(s["t"] - ev["cut"], 1)
            if b not in t_empty and r.get("vrps") == 0:
                t_empty[b] = round(s["t"] - ev["cut"], 1)
    out["cut_rtr_down_s"] = {str(b): t_down.get(b) for b in plan["borders"]}
    out["cut_vrps_empty_s"] = {str(b): t_empty.get(b) for b in plan["borders"]}
    out["cut_timeline"] = [[round(s["t"] - ev["cut"]), max(errors(r)["wrong_accept"] for r in s["routers"].values()),
                            [s["routers"][str(b)].get("vrps") for b in plan["borders"]]] for s in ct]

print(json.dumps(out))
