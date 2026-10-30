import json, sys
d = sys.argv[1]
plan = json.load(open(f"{d}/plan.json"))
ev = {}
for l in open(f"{d}/events.jsonl"):
    e = json.loads(l); ev.setdefault(e["event"], e["t"])
key = lambda r: f"{r['prefix']}|{r['origin']}"
inv = {key(r) for r in plan["routes"] if r["state_new"] == "invalid"}
ok = {key(r) for r in plan["routes"] if r["state_new"] != "invalid"}
b = str(plan["borders"][0])
t0 = "unfreeze" if "unfreeze" in ev else "cache_start"   # freeze: cache switched lists; bgpfirst: cache started
print(t0.replace("_", " "), "at", ev[t0])
last = None
for l in open(f"{d}/samples.jsonl"):
    s = json.loads(l)
    if s["phase"] not in ("frozen", "sync", "auto", "recover"):
        continue
    r = s["routers"][b]; h = set(r["held"])
    row = (len(h & inv), len(ok - h), r.get("vrps"), r.get("rtr_up"))
    if row != last:
        print(f"t={s['t'] - ev[t0]:7.1f}  wrong_accept={row[0]:3}  wrong_drop={row[1]:3}  vrps={row[2]}  rtr_up={row[3]}")
        last = row
