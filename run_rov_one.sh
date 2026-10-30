#!/bin/bash
# Run one zoo topology through one experiment, inside a privileged container.
#
#   run_rov_one.sh <topology.txt> <mode> <softreconf 0|1> <outdir>
#
# mode freeze: the cache serves the stale list. Once every router has converged
#   on it, the current list is published but the cache keeps serving the stale
#   one (a frozen relying party with its RTR sessions up) for FREEZE_S seconds.
#   Then the cache takes the current list. For AUTO_S seconds we watch whether
#   routers correct their decisions on their own. Then an operator runs
#   "clear bgp ... soft in" on every border's eBGP session, and we sample every
#   second until every router matches the current list or RECOVER_S passes.
# mode cut: the cache serves the current list. Once converged, RTR traffic to
#   the cache is dropped (an unreachable relying party) and we sample for CUT_S
#   seconds, past the 600 s Expire Interval.
# mode bgpfirst: no cache at the start, as when BGP converges before the RTR
#   session comes up. Routes are injected and every router accepts all of them
#   (NotFound, no VRPs). Then the cache starts, serving the current list. Once
#   every border holds it, we watch for AUTO_S seconds and then soft-in, as in
#   freeze.
#
# Writes <outdir>/samples.jsonl (sample.py lines), events.jsonl, plan files.
set -u
TOPO="$1"; MODE="$2"; SOFT="$3"; OUT="$4"
FREEZE_S="${FREEZE_S:-60}"; AUTO_S="${AUTO_S:-90}"; RECOVER_S="${RECOVER_S:-180}"; CUT_S="${CUT_S:-1080}"
SAMPLE_GAP="${SAMPLE_GAP:-5}"
SRC=/opt/rov
mkdir -p "$OUT" /tmp/cache
ev() { python3 -c "import json,time,sys; print(json.dumps({'t':round(time.time(),2),'event':sys.argv[1]}))" "$1" >> "$OUT/events.jsonl"; }
samp() { python3 "$SRC/sample.py" "$OUT" "$1" >> "$OUT/samples.jsonl"; }

python3 "$SRC/gen_rov.py" "$TOPO" "$OUT" > "$OUT/gen.json" || exit 3
if [ "$MODE" = freeze ]; then cp "$OUT/vrp-old.json" /tmp/cache/vrp.json; SERVED=old
else cp "$OUT/vrp-new.json" /tmp/cache/vrp.json; SERVED=new; fi

bash "$SRC/rov_lab.sh" "$OUT" "$SOFT" > "$OUT/lab.log" 2>&1
# RTR cache: reread the file every 5 s; End of Data timers as configured on the routers
start_cache() {
  stayrtr -bind 172.20.20.1:8282 -cache /tmp/cache/vrp.json -refresh 5 -checktime=false \
          -rtr.refresh 30 -rtr.retry 30 -rtr.expire 600 > "$OUT/stayrtr.log" 2>&1 &
}
[ "$MODE" = bgpfirst ] || start_cache
ev start

# converged = every border's RTR up with the served list, every router matches
converged() {
  python3 "$SRC/check.py" "$OUT" "$1" "$(tail -n 1 "$OUT/samples.jsonl")" ${2:-}
}
# Wait until every border holds the served list ($1 is the sample phase).
rtr_wait() {
  local ok=0
  for i in $(seq 1 60); do
    samp "$1"
    converged "$SERVED" rtr_only && { ok=1; break; }
    sleep "$SAMPLE_GAP"
  done
  [ "$ok" = 1 ] && ev "rtr_synced" || { ev "rtr_not_synced"; exit 4; }
}
# Watch for AUTO_S seconds whether the routers match the current list on their own.
# If not, run "clear bgp ... soft in" on every border and sample every second until
# they match or RECOVER_S passes.
auto_then_softin() {
  end=$(( $(date +%s) + AUTO_S ))
  auto=0
  while [ "$(date +%s)" -lt "$end" ]; do
    samp auto
    converged new && { ev recovered_auto; auto=1; break; }
    sleep "$SAMPLE_GAP"
  done
  if [ "$auto" = 0 ]; then
    pids=()
    for b in $(python3 -c "import json; print(' '.join(map(str, json.load(open('$OUT/plan.json'))['borders'])))"); do
      ip netns exec "ns$b" vtysh -N "ns$b" -c "clear bgp ipv4 unicast 192.168.$b.2 soft in" >/dev/null 2>&1 &
      pids+=($!)
    done
    wait "${pids[@]}"      # not a bare wait: StayRTR and ExaBGP are background jobs too
    ev operator_softin
    end=$(( $(date +%s) + RECOVER_S ))
    while [ "$(date +%s)" -lt "$end" ]; do
      samp recover
      converged new && { ev recovered; break; }
      sleep 1
    done
  fi
}

# freeze and cut: routes are injected only after every border holds the served
# list. FRR 8.4.4 does not rerun inbound policy when VRPs arrive after the routes
# (seen in the shakedown), so routes that arrive first would be judged NotFound
# and kept. bgpfirst measures exactly that, so its routes go in with no cache.
[ "$MODE" = bgpfirst ] || rtr_wait rtr_sync
env exabgp.daemon.user=root exabgp.tcp.bind='' exabgp.api.cli=false \
    ip netns exec nsx exabgp "$OUT/exabgp.conf" > "$OUT/exabgp.log" 2>&1 &
ev inject
WARM=$SERVED; [ "$MODE" = bgpfirst ] && WARM=none
ok=0
for i in $(seq 1 120); do
  samp warmup
  converged "$WARM" && { ok=1; break; }
  sleep "$SAMPLE_GAP"
done
[ "$ok" = 1 ] && ev "ready" || { ev "not_ready"; exit 4; }

if [ "$MODE" = freeze ]; then
  ev publish_frozen                  # current list exists; the cache does not take it
  end=$(( $(date +%s) + FREEZE_S ))
  while [ "$(date +%s)" -lt "$end" ]; do samp frozen; sleep "$SAMPLE_GAP"; done
  cp "$OUT/vrp-new.json" /tmp/cache/vrp.json.tmp && mv /tmp/cache/vrp.json.tmp /tmp/cache/vrp.json
  ev unfreeze
  auto_then_softin
elif [ "$MODE" = bgpfirst ]; then
  start_cache
  ev cache_start                     # t = 0 in the timeline
  rtr_wait sync
  auto_then_softin
else
  iptables -I INPUT -p tcp --dport 8282 -j DROP
  iptables -I OUTPUT -p tcp --sport 8282 -j DROP
  ev cut
  end=$(( $(date +%s) + CUT_S ))
  while [ "$(date +%s)" -lt "$end" ]; do samp cut; sleep 15; done
fi
ev end
