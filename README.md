# frr-rpki-revalidation-repro

A reproduction harness for an FRR bgpd behaviour: when the RPKI cache changes
the VRP set, the inbound route-map that denies `match rpki invalid` is not
re-applied, even with `soft-reconfiguration inbound`. Routes that become
Invalid stay installed and are advertised to iBGP peers. Routes that become
Valid stay denied. `clear bgp ... soft in` corrects both at once.

Seen with FRR 8.4.4 (Ubuntu 24.04 package) and FRR 10.7.1 (frr-stable from
deb.frrouting.org), both with librtr 0.8.0, StayRTR 0.5.1 as the cache and
ExaBGP 4.2.21 as the eBGP peer. FRR issue: <ISSUE LINK>.

## Run it

```
./repro.sh both        # or frr8 / frr10; needs Docker and runs containers privileged
```

Each version takes about 6 minutes. Output for each version is a timeline at the
border router:

```
=== frr10-rpki-repro: bgpd version 10.7.1
t=  -10.3  wrong_accept= 36  wrong_drop= 48  vrps=72  rtr_up=True
t=    5.3  wrong_accept= 36  wrong_drop= 48  vrps=144  rtr_up=True
t=  301.7  wrong_accept=  0  wrong_drop=  0  vrps=144  rtr_up=True    <- after "soft in"

=== frr8-rpki-repro: bgpd version 8.4.4
t=  -10.5  wrong_accept= 36  wrong_drop= 48  vrps=72  rtr_up=True
t=    5.3  wrong_accept= 36  wrong_drop= 48  vrps=144  rtr_up=True
t=  301.4  wrong_accept=  0  wrong_drop=  0  vrps=144  rtr_up=True    <- after "soft in"
```

(Output of `./repro.sh both` on 2026-10-27, Ubuntu 24.04 host, Docker 29.1.3.
A line is printed only when a value changes.)

`wrong_accept` counts routes held that are Invalid under the current list;
`wrong_drop` counts routes not held that the current list accepts. t is
seconds after the cache switched lists. The router has the new list (vrps=144)
within seconds, but its decisions do not change until the operator's soft-in
at t = `AUTO_S` (300 s by default). In 12 of 13 runs of this 2-router lab on our host the decisions
did not change before the soft-in. In one they corrected themselves at 63 s,
cause unknown.

## What runs

| file | role |
|---|---|
| `topo-2node.txt` | two routers: r1 is the iBGP route reflector, r2 the border |
| `gen_rov.py` | the 132 announced routes, VRP lists A (stale) and B (current), and each route's expected RFC 6811 state under each list |
| `rov_lab.sh` | network namespaces, IS-IS, iBGP, the eBGP link to the injector, and the FRR config (route-map `ROV`, `soft-reconfiguration inbound`, `rpki` block) |
| `run_rov_one.sh` | starts StayRTR and ExaBGP, waits for convergence on list A, switches the cache to list B, waits `AUTO_S`, runs `soft in` on the border, samples throughout |
| `sample.py`, `check.py` | snapshot every router's best paths, FIB and RTR state; test convergence |
| `analyze_rov.py`, `timeline.py` | summarize a run |
| `Dockerfile`, `Dockerfile.frr10` | FRR 8.4.4 from Ubuntu; FRR 10.x from frr-stable |

Expected decisions come from the small RFC 6811 validator in `gen_rov.py`, not
from FRR. The harness was built for a larger study of RPKI staleness on
Topology Zoo graphs, and `run_rov_one.sh` also has a `cut` mode (cache
unreachable past the Expire Interval) not needed here.

## License

Two licenses, split by what the file is.

| what | license | file |
|---|---|---|
| code (`*.py`, `*.sh`, Dockerfiles) | MIT | `LICENSE` |
| prose and data (this README, the route and VRP sets) | CC BY 4.0 | `LICENSE-TEXT` |
