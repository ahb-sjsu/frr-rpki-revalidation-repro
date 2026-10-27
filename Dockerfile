# ROV lab image: FRR 8.4.4 with the RPKI module, StayRTR as the RTR cache,
# ExaBGP as the route injector. Ubuntu 24.04 packages throughout.
FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends frr frr-pythontools frr-rpki-rtrlib \
      stayrtr exabgp iproute2 iptables python3 procps \
 && rm -rf /var/lib/apt/lists/*
