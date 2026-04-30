#!/bin/sh
# Start a capture session against a running naive_proxy stack.
#
# Configurable knobs (env var or CLI flag, flag wins):
#   --nic         | NIC          public NIC for host-side tcpdump (default: enp1s0f1)
#   --duration    | DURATION     capture duration, seconds (default: 300)
#   --haproxy     | HAPROXY      haproxy container name (default: naive-haproxy)
#   --listen-port | LISTEN_PORT  public listen port (default: 443)
#   --pod-ports   | POD_PORTS    space-separated ports the pod-netns tcpdump
#                                 watches (default: "443 8444 8080")
#   --units       | UNITS        space-separated journalctl units to follow
#                                 (default: "podman-naive-haproxy.service podman-naive-backend.service")
#   --inspect-units | INSPECT_UNITS
#                                space-separated systemd units snapshotted in
#                                meta.before.txt (default: all four
#                                podman-naive-{pod,haproxy,backend,decoy}.service)
#   --out-prefix  | OUT_PREFIX   output dir prefix (default: /tmp/naive-debug-)
#
# Outputs into <OUT_PREFIX><RUN_ID>/:
#   meta.before.txt              addressing/podman/systemd/ss/nstat snapshot
#   pids.txt                     pids of the 4 background watchers
#   host-<NIC>-port<PORT>.pcap   host-side tcpdump
#   pod-any-<PORTS>.pcap         pod-netns tcpdump
#   journal-follow.log           journalctl -f for the chosen units
#   ss-sample-and-after.log      ss -tan samples every 2 s + nstat after
#
# Each watcher is wrapped in `timeout $DURATION` so the session
# self-terminates even if stop-capture-dump-h2.sh is never called.
#
# Requires passwordless sudo for tcpdump, nsenter, journalctl.

set -u

# --- defaults (overridable via env) ---
: "${NIC:=enp1s0f1}"
: "${DURATION:=300}"
: "${HAPROXY:=naive-haproxy}"
: "${LISTEN_PORT:=443}"
: "${POD_PORTS:=443 8444 8080}"
: "${UNITS:=podman-naive-haproxy.service podman-naive-backend.service}"
: "${INSPECT_UNITS:=podman-naive-pod.service podman-naive-haproxy.service podman-naive-backend.service podman-naive-decoy.service}"
: "${OUT_PREFIX:=/tmp/naive-debug-}"

# --- CLI overrides ---
while [ $# -gt 0 ]; do
    case "$1" in
        --nic)            NIC=$2; shift 2 ;;
        --duration)       DURATION=$2; shift 2 ;;
        --haproxy)        HAPROXY=$2; shift 2 ;;
        --listen-port)    LISTEN_PORT=$2; shift 2 ;;
        --pod-ports)      POD_PORTS=$2; shift 2 ;;
        --units)          UNITS=$2; shift 2 ;;
        --inspect-units)  INSPECT_UNITS=$2; shift 2 ;;
        --out-prefix)     OUT_PREFIX=$2; shift 2 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
DIR="${OUT_PREFIX}${RUN_ID}"
mkdir -p "$DIR"
chmod 700 "$DIR"
HP=$(sudo -n podman inspect --format '{{.State.Pid}}' "$HAPROXY" 2>/dev/null || echo 0)

# Build pod-netns tcpdump filter from POD_PORTS list.
POD_FILTER=$(echo "$POD_PORTS" | awk '{
    for(i=1;i<=NF;i++) printf "%sport %s", (i==1?"":" or "), $i
}')
POD_FILTER="tcp and ($POD_FILTER)"

# Sanitised filename suffix (e.g. "443-8444-8080") for the pod pcap.
POD_SUFFIX=$(echo "$POD_PORTS" | tr ' ' '-')
HOST_PCAP="$DIR/host-${NIC}-port${LISTEN_PORT}.pcap"
POD_PCAP="$DIR/pod-any-${POD_SUFFIX}.pcap"

{
    echo "run_id=$RUN_ID"
    echo "dir=$DIR"
    echo "start=$(date -Is)"
    echo "haproxy_container=$HAPROXY"
    echo "haproxy_pid=$HP"
    echo "nic=$NIC"
    echo "listen_port=$LISTEN_PORT"
    echo "pod_ports=$POD_PORTS"
    echo "duration_s=$DURATION"
    echo "hostname=$(hostname)"
    echo "--- ip -br addr ---"
    ip -br addr 2>&1
    echo "--- ip route ---"
    ip route 2>&1
    echo "--- podman ps ---"
    sudo -n podman ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}' 2>&1
    echo "--- systemd show ---"
    # shellcheck disable=SC2086
    systemctl --no-pager --plain show \
        -p Id -p ActiveState -p SubState -p NRestarts -p ExecMainStatus \
        -p MainPID -p ActiveEnterTimestamp \
        $INSPECT_UNITS 2>&1
    echo "--- ss -s ---"
    ss -s 2>&1
    echo "--- nstat before ---"
    nstat -az 2>&1 | egrep \
        'Tcp(Ext)?(Listen|Retrans|Timeout|Reset|Abort|Backlog|Ofo|Prune|SynRetrans)|TcpOutSegs|TcpInSegs|TcpEstabResets|TcpOutRsts|TcpAttemptFails|TcpRetransSegs|IpInDiscards|IpExtInNoRoutes' \
        || true
} > "$DIR/meta.before.txt" 2>&1

# Host-side pcap on the public NIC. -s 96 keeps headers; payload encrypted anyway.
sudo -n timeout "$DURATION" tcpdump -i "$NIC" -nn -tttt -U -s 96 \
    -w "$HOST_PCAP" "tcp port $LISTEN_PORT" \
    > "${HOST_PCAP%.pcap}.tcpdump.log" 2>&1 &
P1=$!

# Pod-netns pcap on -i any catches loopback + the public 443 entry after DNAT.
if [ "$HP" != "0" ]; then
    sudo -n nsenter -t "$HP" -n timeout "$DURATION" tcpdump -i any -nn -tttt -U -s 96 \
        -w "$POD_PCAP" "$POD_FILTER" \
        > "${POD_PCAP%.pcap}.tcpdump.log" 2>&1 &
    P2=$!
else
    P2=0
fi

# shellcheck disable=SC2086
sudo -n timeout "$DURATION" journalctl -f \
    -u $(echo "$UNITS" | sed 's/ / -u /g') \
    -o short-iso --no-pager > "$DIR/journal-follow.log" 2>&1 &
P3=$!

# ss + nstat sampler. ITERS = ceil(DURATION / 2).
ITERS=$(( (DURATION + 1) / 2 ))
SS_FILTER="( $(echo "$POD_PORTS $LISTEN_PORT" | tr ' ' '\n' | sort -u | awk '{
    printf "%ssport = :%s or dport = :%s", (NR==1?"":" or "), $1, $1
}') )"
(
    for i in $(seq 1 "$ITERS"); do
        echo "--- $(date -Is) ---"
        ss -Htan state established "$SS_FILTER" 2>&1 | sed -n '1,120p'
        sleep 2
    done
    echo "--- end $(date -Is) ---"
    echo "--- ss -s after ---"
    ss -s 2>&1
    echo "--- nstat after ---"
    nstat -az 2>&1 | egrep \
        'Tcp(Ext)?(Listen|Retrans|Timeout|Reset|Abort|Backlog|Ofo|Prune|SynRetrans)|TcpOutSegs|TcpInSegs|TcpEstabResets|TcpOutRsts|TcpAttemptFails|TcpRetransSegs|IpInDiscards|IpExtInNoRoutes' \
        || true
) > "$DIR/ss-sample-and-after.log" 2>&1 &
P4=$!

cat > "$DIR/pids.txt" <<EOF
host_tcpdump_pid=$P1
pod_tcpdump_pid=$P2
journal_pid=$P3
sampler_pid=$P4
EOF

echo "__CAPTURE_STARTED__"
echo "dir=$DIR"
echo "duration_seconds=$DURATION"
echo "nic=$NIC  listen_port=$LISTEN_PORT  pod_ports=$POD_PORTS"
echo "haproxy_container=$HAPROXY  pid=$HP"
cat "$DIR/pids.txt"
echo "start=$(date -Is)"
echo "__CAPTURE_STARTED_END__"
