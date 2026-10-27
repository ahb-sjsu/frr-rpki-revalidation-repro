#!/bin/bash
# Build one BGP + route-origin-validation lab from a Topology Zoo graph, in one
# kernel's network namespaces (adapted from emulation/pcep/overlay/topo_lab.sh,
# without the SR-MPLS and PCEP parts).
#
#   rov_lab.sh <plan-dir> [softreconf 0|1]
#
# <plan-dir> is gen_rov.py's output. Each zoo node is a router in netns ns<id>
# with loopback 10.0.0.<id>/32 and IS-IS on every link (10.<a>.<b>.0/30). All
# routers run iBGP (AS 65000) to the route reflector. Border routers also peer
# eBGP with the injector netns nsx (ExaBGP, AS 64999) over 192.168.<b>.0/30,
# drop RPKI-Invalid routes on that session, and hold an RTR session to the cache
# (StayRTR, started by the runner) at 172.20.20.1:8282 on the mgmt bridge.
set -u
PLAN="${1:?usage: rov_lab.sh <plan-dir> [softreconf]}"
SOFT="${2:-1}"
FRRLIB=/usr/lib/frr
MGMT=172.20.20
run() { ip netns exec "ns$1" "${@:2}"; }
pj() { python3 -c "import json,sys; p=json.load(open('$PLAN/plan.json')); print($1)"; }

nodes=($(pj "' '.join(map(str,p['nodes']))"))
links=($(pj "' '.join(f'{a}:{b}' for a,b in p['links'])"))
RR=$(pj "p['rr']")
borders=($(pj "' '.join(map(str,p['borders']))"))
is_border() { local x; for x in "${borders[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }
for n in "${nodes[@]}"; do
  [ "$n" -ge 1 ] && [ "$n" -le 254 ] || { echo "node id $n outside 1..254"; exit 2; }
done
FRRVER=$("$FRRLIB/bgpd" --version | awk '/version/{print $3; exit}')
# FRR 9 and later name the transport in the cache command
case "$FRRVER" in
  8.*) CACHE_CMD="rpki cache $MGMT.1 8282 preference 1" ;;
  *)   CACHE_CMD="rpki cache tcp $MGMT.1 8282 preference 1" ;;
esac
echo "=== FRR $FRRVER: ${#nodes[@]} routers, ${#links[@]} links, RR $RR, borders ${borders[*]}, softreconf $SOFT ==="

ip link add br-mgmt type bridge 2>/dev/null
ip addr add "$MGMT.1/24" dev br-mgmt 2>/dev/null
ip link set br-mgmt up

for n in "${nodes[@]}"; do
  ip netns add "ns$n"
  run "$n" sysctl -qw net.ipv4.ip_forward=1
  run "$n" ip link set lo up
  run "$n" ip addr add "10.0.0.$n/32" dev lo
  ip link add "mv$n" netns "ns$n" type veth peer name "mb$n"
  ip link set "mb$n" master br-mgmt up
  run "$n" ip addr add "$MGMT.$((10+n))/24" dev "mv$n"
  run "$n" ip link set "mv$n" up
  mkdir -p "/var/run/frr/ns$n" "/etc/frr/ns$n"
done

declare -A ifaces
for lk in "${links[@]}"; do
  a="${lk%:*}"; b="${lk#*:}"
  ia="e$a$b"; ib="e$b$a"
  ip link add "$ia" netns "ns$a" type veth peer name "$ib" netns "ns$b"
  run "$a" ip addr add "10.$a.$b.1/30" dev "$ia"; run "$a" ip link set "$ia" up
  run "$b" ip addr add "10.$a.$b.2/30" dev "$ib"; run "$b" ip link set "$ib" up
  ifaces[$a]+=" $ia"; ifaces[$b]+=" $ib"
done

# injector netns and one eBGP link per border
ip netns add nsx
ip netns exec nsx ip link set lo up
for b in "${borders[@]}"; do
  ip link add "x$b" netns "ns$b" type veth peer name "xb$b" netns nsx
  run "$b" ip addr add "192.168.$b.1/30" dev "x$b"; run "$b" ip link set "x$b" up
  ip netns exec nsx ip addr add "192.168.$b.2/30" dev "xb$b"
  ip netns exec nsx ip link set "xb$b" up
done

for n in "${nodes[@]}"; do
  conf="/etc/frr/ns$n/frr.conf"
  {
    echo "hostname r$n"
    echo "interface lo"
    echo " ip router isis 1"
    echo " isis passive"
    echo "exit"
    for i in ${ifaces[$n]:-}; do
      echo "interface $i"
      echo " ip router isis 1"
      echo " isis network point-to-point"
      echo " isis hello-interval 1"
      echo " isis hello-multiplier 3"
      echo "exit"
    done
    printf 'router isis 1\n net 49.0000.0000.%04d.00\n is-type level-1\n lsp-gen-interval 1\n spf-interval 1\nexit\n' "$n"
    if is_border "$n"; then
      # RTR timers: RFC 8210 minimum Expire Interval (600 s); short polling so
      # changes show up within the sampling interval.
      cat <<CONF
rpki
 rpki polling_period 30
 rpki retry_interval 30
 rpki expire_interval 600
 $CACHE_CMD
exit
route-map ROV deny 10
 match rpki invalid
exit
route-map ROV permit 20
exit
CONF
    fi
    echo "router bgp 65000"
    echo " bgp router-id 10.0.0.$n"
    echo " no bgp ebgp-requires-policy"
    echo " timers bgp 3 9"
    if [ "$n" = "$RR" ]; then
      echo " neighbor CL peer-group"
      echo " neighbor CL remote-as 65000"
      echo " neighbor CL update-source lo"
      echo " neighbor CL advertisement-interval 0"
      echo " bgp listen range 10.0.0.0/24 peer-group CL"
    else
      echo " neighbor 10.0.0.$RR remote-as 65000"
      echo " neighbor 10.0.0.$RR update-source lo"
      echo " neighbor 10.0.0.$RR advertisement-interval 0"
    fi
    if is_border "$n"; then
      echo " neighbor 192.168.$n.2 remote-as 64999"
      echo " neighbor 192.168.$n.2 advertisement-interval 0"
    fi
    echo " address-family ipv4 unicast"
    if [ "$n" = "$RR" ]; then
      echo "  neighbor CL route-reflector-client"
    else
      echo "  neighbor 10.0.0.$RR next-hop-self"
    fi
    if is_border "$n"; then
      echo "  neighbor 192.168.$n.2 route-map ROV in"
      [ "$SOFT" = 1 ] && echo "  neighbor 192.168.$n.2 soft-reconfiguration inbound"
    fi
    echo " exit-address-family"
    echo "exit"
  } > "$conf"
  echo "service integrated-vtysh-config" > "/etc/frr/ns$n/vtysh.conf"
done
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null

for n in "${nodes[@]}"; do
  for d in zebra isisd bgpd; do
    opt="-d -A 127.0.0.1"; [ "$d" = bgpd ] && opt="$opt -M rpki"
    run "$n" "$FRRLIB/$d" -N "ns$n" $opt \
       -i "/var/run/frr/ns$n/$d.pid" -z "/var/run/frr/ns$n/zserv.api" 2>/dev/null
  done
done
sleep 2
for n in "${nodes[@]}"; do
  run "$n" vtysh -N "ns$n" -b 2>&1 | sed "s/^/r$n: /" | grep -v "^r$n: *$"
done
echo "=== FRR up in ${#nodes[@]} netns ==="
