#!/bin/sh
# Enable HAProxy H2 trace into a custom ring sink so the buffer doesn't roll
# over within a single 5-min capture session. Filters: errors, lifecycle,
# flow-control. Trace state is RESET when the haproxy container restarts;
# re-run this script after every restart.
#
# Configurable knobs (env var or CLI flag, flag wins):
#   --haproxy | HAPROXY  haproxy container name (default: naive-haproxy)
#   --admin   | ADMIN    admin socket "host port" inside the haproxy netns
#                        (default: "127.0.0.1 19999")
#   --sink    | SINK     ring sink to write trace events to
#                        (default: h2trace; must be declared with
#                         `ring h2trace { format timed; size 33554432 }`
#                         in haproxy.cfg, otherwise fall back to the
#                         built-in 1 MiB 'buf0' sink)

set -u

: "${HAPROXY:=naive-haproxy}"
: "${ADMIN:=127.0.0.1 19999}"
: "${SINK:=h2trace}"

while [ $# -gt 0 ]; do
    case "$1" in
        --haproxy) HAPROXY=$2; shift 2 ;;
        --admin)   ADMIN=$2; shift 2 ;;
        --sink)    SINK=$2; shift 2 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

cli() {
    sudo -n podman exec "$HAPROXY" sh -c \
        "(printf '%s\n' \"$1\"; sleep 0.05) | nc $ADMIN" >/dev/null
}

cli_out() {
    sudo -n podman exec "$HAPROXY" sh -c \
        "(printf '%s\n' \"$1\"; sleep 0.05) | nc $ADMIN"
}

cli 'trace h2 stop now' || true
cli 'trace h2 event none'

# Error & lifecycle events that diagnose the buffer-overflow →
# invalid-frame → GOAWAY pattern.
for ev in h2c_err h2s_err strm_err proto_err \
          rx_rst tx_rst rx_goaway tx_goaway \
          h2c_end h2s_end \
          h2c_fctl h2s_fctl h2c_blk h2s_blk; do
    cli "trace h2 event $ev"
done

cli "trace h2 sink $SINK"
cli 'trace h2 verbosity advanced'
cli 'trace h2 level developer'
cli 'trace h2 start now'

cli_out 'show trace h2' | sed -n '1,90p'
