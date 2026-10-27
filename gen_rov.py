#!/usr/bin/env python3
"""Plan one ROV lab from a Topology Zoo file (topo_lab.sh NODE/LINK format).

    gen_rov.py <topology.txt> <outdir> [--borders 3] [--per-class 12]

Each zoo graph becomes one provider, AS 65000: IS-IS inside, iBGP to a route
reflector (the highest-degree node), and eBGP at border routers to an external
injector (ExaBGP, AS 64999) that announces a fixed route set. Border routers
drop RPKI-Invalid routes. Writes to <outdir>:

  plan.json      rr, borders, routers, the route set with each route's class
  vrp-old.json   the stale list (StayRTR / rpki-client JSON)
  vrp-new.json   the current list
  exabgp.conf    the injector's neighbors and routes
  expected.json  per route: RFC 6811 state and accept/drop under each list

The expected outcomes come from the small validator below, not from FRR, so
the run checks FRR's route origin validation as well as measuring it.
Standard library only (runs inside the lab container).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from collections import deque

PROVIDER_AS, INJECTOR_AS = 65000, 64999
BASE = int(ipaddress.IPv4Address("100.64.0.0"))
BLOCK = 4096                       # one /20 per route item; the route is its first /24


def read_topo(path):
    nodes, links = [], []
    for line in open(path):
        f = line.split()
        if not f or f[0].startswith("#"):
            continue
        if f[0] == "NODE":
            nodes.append(int(f[1]))
        elif f[0] == "LINK":
            links.append((int(f[1]), int(f[2])))
    return nodes, links


def bfs(adj, src):
    dist, q = {src: 0}, deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def pick(nodes, links, k):
    adj = {n: set() for n in nodes}
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)
    rr = max(nodes, key=lambda n: (len(adj[n]), -n))
    dist = {n: bfs(adj, n) for n in nodes}
    borders = []
    while len(borders) < min(k, len(nodes) - 1):
        chosen = [rr] + borders
        cand = [n for n in nodes if n not in chosen]
        borders.append(max(cand, key=lambda n: (min(dist[c][n] for c in chosen), -n)))
    ecc = max(max(d.values()) for d in dist.values())
    return rr, sorted(borders), ecc


# --- RFC 6811 -----------------------------------------------------------------

def validate(prefix, origin, vrps):
    net = ipaddress.ip_network(prefix)
    covered = False
    for v in vrps:
        vn = ipaddress.ip_network(v["prefix"])
        if net.version == vn.version and net.subnet_of(vn):
            covered = True
            if v["asn"] != 0 and v["asn"] == origin and net.prefixlen <= v["maxLength"]:
                return "valid"
    return "invalid" if covered else "not_found"


# --- the route set ------------------------------------------------------------
# Each item: the announced route(s) and the VRPs in the stale and current lists.
# Classes mirror the mechanisms behind the decision errors of a stale list.

def items(per_class):
    out = []
    asn = iter(range(64600, 64990))

    def blk(i):
        b = BASE + i * BLOCK
        return str(ipaddress.ip_network((b, 24))), str(ipaddress.ip_network((b, 20)))

    i = 0
    for kind in ("valid", "invalid", "not_found", "wd_new_origin", "wd_new_origin_cover",
                 "wd_maxlen", "wa_new_roa", "wa_as0", "wd_exact", "wa_exact"):
        for _ in range(per_class):
            p24, p20 = blk(i)
            i += 1
            o, o2 = next(asn), next(asn)
            ann, old, new = [], [], []
            if kind == "valid":                  # authorized in both lists
                ann = [(p24, o)]
                old = new = [(p20, 24, o)]
            elif kind == "invalid":              # announced by the wrong origin in both
                ann = [(p24, o2)]
                old = new = [(p20, 24, o)]
            elif kind == "not_found":            # no authorization in either
                ann = [(p24, o)]
            elif kind in ("wd_new_origin", "wd_new_origin_cover"):
                # the holder moved the prefix to a new origin and authorized it;
                # the stale list still has only the old origin -> wrong drop
                ann = [(p24, o2)]
                old = [(p20, 24, o)]
                new = [(p20, 24, o), (p20, 24, o2)]
                if kind == "wd_new_origin_cover":    # an aggregate still covers it
                    ann.append((p20, o))
            elif kind == "wd_maxlen":            # maxLength raised to allow the /24
                ann = [(p24, o)]
                old = [(p20, 20, o)]
                new = [(p20, 24, o)]
            elif kind == "wa_new_roa":           # the holder authorized a different origin
                ann = [(p24, o2)]                # after the stale list; announcer is now Invalid
                new = [(p20, 24, o)]
            elif kind == "wa_as0":               # the holder declared no origin (AS0)
                ann = [(p24, o)]
                new = [(p20, 24, 0)]
            # the same two errors with the VRP on exactly the announced prefix,
            # to see whether revalidation depends on VRP and route lengths matching
            elif kind == "wd_exact":
                ann = [(p24, o2)]
                old = [(p24, 24, o)]
                new = [(p24, 24, o), (p24, 24, o2)]
            elif kind == "wa_exact":
                ann = [(p24, o2)]
                new = [(p24, 24, o)]
            for j, (pfx, org) in enumerate(ann):
                out.append({"kind": kind if j == 0 else "cover", "prefix": pfx, "origin": org,
                            "old": [list(v) for v in old], "new": [list(v) for v in new]})
    return out


def vrp_file(routes, which):
    seen, roas = set(), []
    for r in routes:
        for p, ml, a in r[which]:
            if (p, ml, a) not in seen:
                seen.add((p, ml, a))
                roas.append({"prefix": p, "maxLength": ml, "asn": a, "ta": "lab"})
    return {"metadata": {"buildtime": "2026-10-27T00:00:00Z", "vrps": len(roas)},
            "roas": sorted(roas, key=lambda v: (v["prefix"], v["asn"], v["maxLength"]))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topo")
    ap.add_argument("outdir")
    ap.add_argument("--borders", type=int, default=3)
    ap.add_argument("--per-class", type=int, default=12)
    a = ap.parse_args()
    nodes, links = read_topo(a.topo)
    rr, borders, ecc = pick(nodes, links, a.borders)
    routes = items(a.per_class)
    vold, vnew = vrp_file(routes, "old"), vrp_file(routes, "new")
    for r in routes:
        r["state_old"] = validate(r["prefix"], r["origin"], vold["roas"])
        r["state_new"] = validate(r["prefix"], r["origin"], vnew["roas"])
    os.makedirs(a.outdir, exist_ok=True)
    plan = {"topo": os.path.basename(a.topo)[:-4], "nodes": nodes, "links": links,
            "rr": rr, "borders": borders, "diameter": ecc,
            "provider_as": PROVIDER_AS, "injector_as": INJECTOR_AS, "routes": routes}
    json.dump(plan, open(f"{a.outdir}/plan.json", "w"), indent=1)
    json.dump(vold, open(f"{a.outdir}/vrp-old.json", "w"), indent=1)
    json.dump(vnew, open(f"{a.outdir}/vrp-new.json", "w"), indent=1)
    exp = {f"{r['prefix']}|{r['origin']}": {"kind": r["kind"], "old": r["state_old"],
                                            "new": r["state_new"]} for r in routes}
    json.dump(exp, open(f"{a.outdir}/expected.json", "w"), indent=1)
    with open(f"{a.outdir}/exabgp.conf", "w") as f:
        for b in borders:
            f.write(f"neighbor 192.168.{b}.1 {{\n router-id 192.168.{b}.2;\n"
                    f" local-address 192.168.{b}.2;\n local-as {INJECTOR_AS};\n"
                    f" peer-as {PROVIDER_AS};\n static {{\n")
            for r in routes:
                f.write(f"  route {r['prefix']} next-hop self as-path [ {INJECTOR_AS} {r['origin']} ];\n")
            f.write(" }\n}\n")
    print(json.dumps({"topo": plan["topo"], "n": len(nodes), "rr": rr, "borders": borders,
                      "routes": len(routes), "vrp_old": len(vold["roas"]), "vrp_new": len(vnew["roas"])}))


if __name__ == "__main__":
    main()
