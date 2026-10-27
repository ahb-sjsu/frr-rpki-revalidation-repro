#!/bin/bash
# Reproduce: RPKI cache updates do not re-apply inbound "match rpki" policy in
# FRR bgpd. Builds two images (FRR 8.4.4 from Ubuntu 24.04, FRR 10.x from the
# FRR project's frr-stable repository), runs the 2-router lab in each, and
# prints the border router's wrong decisions over time.
#
#   ./repro.sh [frr10|frr8|both]      (default both; needs Docker, runs privileged)
#
# Timeline per run: the RPKI cache serves list A; routes are injected and
# filtered correctly; at t=0 the cache switches to list B; for AUTO_S seconds
# (default 300) nothing else happens; then "clear bgp ... soft in" is run on
# the border and sampling continues for up to 180 s.
set -eu
cd "$(dirname "$0")"
WHICH="${1:-both}"
AUTO_S="${AUTO_S:-300}"
OUT="$PWD/out"
mkdir -p "$OUT"

run() {  # image tag, dockerfile
  local tag=$1 df=$2
  docker build -q -t "$tag" -f "$df" . >/dev/null
  local o="$OUT/$tag-$(date +%Y%m%dT%H%M%S)"
  mkdir -p "$o"
  echo "=== $tag: $(docker run --rm --entrypoint /usr/lib/frr/bgpd "$tag" --version | head -1)"
  docker run --rm --privileged -e FREEZE_S=10 -e AUTO_S="$AUTO_S" -e RECOVER_S=180 \
    -v "$PWD:/opt/rov:ro" -v "$o:/out" "$tag" \
    bash -c "bash /opt/rov/run_rov_one.sh /opt/rov/topo-2node.txt freeze 1 /out/run; \
             python3 /opt/rov/analyze_rov.py /out/run > /out/run/summary.json"
  python3 timeline.py "$o/run"
  echo "    (t is seconds after the cache switched to list B; events: $(python3 -c "import json;print([json.loads(l)['event'] for l in open('$o/run/events.jsonl')])"))"
}

case "$WHICH" in
  frr8)  run frr8-rpki-repro Dockerfile ;;
  frr10) run frr10-rpki-repro Dockerfile.frr10 ;;
  both)  run frr8-rpki-repro Dockerfile; run frr10-rpki-repro Dockerfile.frr10 ;;
esac
