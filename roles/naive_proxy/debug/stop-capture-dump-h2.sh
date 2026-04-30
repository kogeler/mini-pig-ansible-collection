#!/bin/sh
# Stop the running capture (host pcap, pod pcap, journal-follow, ss sampler)
# and dump HAProxy H2 trace state from a ring sink.
#
# Configurable knobs (env var or CLI flag, flag wins):
#   --haproxy   | HAPROXY    haproxy container name (default: naive-haproxy)
#   --admin     | ADMIN      admin socket "host port" (default: "127.0.0.1 19999")
#   --sink      | SINK       ring sink to read events from (default: h2trace);
#                            if empty/missing the script falls back to buf0
#   --out-glob  | OUT_GLOB   glob for default capture dir auto-detection
#                            (default: /tmp/naive-debug-*)
#   first positional arg     explicit capture dir (overrides auto-detect)
#
# Output files written into the capture dir:
#   haproxy-h2-trace-stop.out     output of 'trace h2 stop now'
#   haproxy-h2-trace-status.out   output of 'show trace h2'
#   haproxy-h2-events.log         dump of 'show events <sink>'
#   (plus the 7 capture artifacts already present)
#
# Backwards-compat: legacy dirs with `haproxy-h2-events-buf0.log` are still
# readable by analyze.sh — it falls back to that filename automatically.

set -eu

: "${HAPROXY:=naive-haproxy}"
: "${ADMIN:=127.0.0.1 19999}"
: "${SINK:=h2trace}"
: "${OUT_GLOB:=/tmp/naive-debug-*}"

DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --haproxy)  HAPROXY=$2; shift 2 ;;
        --admin)    ADMIN=$2; shift 2 ;;
        --sink)     SINK=$2; shift 2 ;;
        --out-glob) OUT_GLOB=$2; shift 2 ;;
        -h|--help)  sed -n '2,22p' "$0"; exit 0 ;;
        --) shift; break ;;
        -*) echo "unknown flag: $1" >&2; exit 2 ;;
        *)  DIR=$1; shift ;;
    esac
done
[ -n "$DIR" ] || DIR=$(ls -dt $OUT_GLOB 2>/dev/null | head -1)
[ -d "$DIR" ] || { echo "no capture dir: $DIR"; exit 1; }
echo "dir=$DIR"

# Stop the capture watchers.
if [ -f "$DIR/pids.txt" ]; then
    . "$DIR/pids.txt"
    sudo -n kill -INT \
        ${host_tcpdump_pid:-0} ${pod_tcpdump_pid:-0} \
        ${journal_pid:-0} ${sampler_pid:-0} 2>/dev/null || true
fi
sleep 2

cli() {
    sudo -n podman exec "$HAPROXY" sh -c \
        "(printf '%s\n' \"$1\"; sleep $2) | nc $ADMIN"
}

# Halt the trace and snapshot its state.
cli 'trace h2 stop now' 0.05  > "$DIR/haproxy-h2-trace-stop.out"   2>&1 || true
cli 'show trace h2'      0.05  > "$DIR/haproxy-h2-trace-status.out" 2>&1 || true
cli "show events $SINK"  0.5   > "$DIR/haproxy-h2-events.log"      2>&1 || true

# Fall back to buf0 if the custom sink is missing or empty.
if [ ! -s "$DIR/haproxy-h2-events.log" ] && [ "$SINK" != "buf0" ]; then
    cli 'show events buf0' 0.5 > "$DIR/haproxy-h2-events.log" 2>&1 || true
fi

echo "files:"
sudo -n find "$DIR" -maxdepth 1 -type f -printf "%f %s bytes\n" | sort
echo "events_lines=$(sudo -n wc -l < "$DIR/haproxy-h2-events.log" 2>/dev/null || echo 0)"
echo "journal_dbg=$(sudo -n grep -c dbg_https "$DIR/journal-follow.log" 2>/dev/null || echo 0)"
echo "journal_badreq=$(sudo -n grep -c "<BADREQ>" "$DIR/journal-follow.log" 2>/dev/null || echo 0)"
echo "backend_resets=$(sudo -n grep -c ERR_CONNECTION_RESET "$DIR/journal-follow.log" 2>/dev/null || echo 0)"
