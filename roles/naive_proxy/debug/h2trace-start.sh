#!/bin/sh
# Enable HAProxy H2 trace into a custom ring sink so the buffer doesn't roll
# over within a single 5-min capture session. Default event filter is
# 'lifecycle' (errors + lifecycle + flow-control); pass '--events all' for
# every event source (rx_fhdr/rx_frame/etc.) when you need the full demux
# context around an error. Trace state is RESET when the haproxy container
# restarts; re-run this script after every restart.
#
# Configurable knobs (env var or CLI flag, flag wins):
#   --haproxy   | HAPROXY    haproxy container name (default: naive-haproxy)
#   --admin     | ADMIN      admin socket "host port" inside the haproxy netns
#                            (default: "127.0.0.1 19999")
#   --sink      | SINK       ring sink to write trace events to
#                            (default: h2trace; must be declared with
#                             `ring h2trace { format timed; size <bytes> }`
#                             in haproxy.cfg, otherwise fall back to the
#                             built-in 1 MiB 'buf0' sink)
#   --verbosity | VERBOSITY  HAProxy trace verbosity (default: advanced;
#                            use 'complete' to dump full frame contents
#                            including DATA payload — needed when an
#                            HAProxy maintainer asks for hexdumps to
#                            disambiguate frame parser issues. WARNING:
#                            'complete' fills the ring sink ~10x faster
#                            per event AND dumps plaintext HTTP/2 headers
#                            (Proxy-Authorization values, Cookie, etc.)
#                            — if the trace will be shared publicly,
#                            redact and/or rotate credentials first.)
#   --level     | LEVEL      HAProxy trace level (default: developer)
#   --events    | EVENTS     event filter: 'lifecycle' (default — error +
#                            lifecycle + flow-control, the original
#                            buffer-overflow→GOAWAY filter) or 'all'
#                            (every event source, including rx_fhdr /
#                            rx_frame / rx_data / rx_hdr; required when
#                            you need the hex dump of a frame header
#                            because at 'lifecycle' the proto_err event
#                            line carries metadata only, not bytes).

set -u

: "${HAPROXY:=naive-haproxy}"
: "${ADMIN:=127.0.0.1 19999}"
: "${SINK:=h2trace}"
: "${VERBOSITY:=advanced}"
: "${LEVEL:=developer}"
: "${EVENTS:=lifecycle}"

while [ $# -gt 0 ]; do
    case "$1" in
        --haproxy)   HAPROXY=$2; shift 2 ;;
        --admin)     ADMIN=$2; shift 2 ;;
        --sink)      SINK=$2; shift 2 ;;
        --verbosity) VERBOSITY=$2; shift 2 ;;
        --level)     LEVEL=$2; shift 2 ;;
        --events)    EVENTS=$2; shift 2 ;;
        -h|--help)   sed -n '2,36p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

case "$EVENTS" in
    lifecycle|all) ;;
    *) echo "--events must be 'lifecycle' or 'all' (got: $EVENTS)" >&2; exit 2 ;;
esac

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

if [ "$EVENTS" = "all" ]; then
    # Every event source. Required when you need hex dumps of frame
    # headers / payloads — the proto_err event itself carries metadata
    # only, the bytes come from the rx_fhdr/rx_frame events that fire
    # immediately before it. NOTE: HAProxy's CLI uses 'any' (not 'all')
    # as the keyword for "every event source" — confirmed against
    # `trace h2 event` output on 3.3.x.
    cli 'trace h2 event any'
else
    # Error & lifecycle events that diagnose the buffer-overflow →
    # invalid-frame → GOAWAY pattern.
    for ev in h2c_err h2s_err strm_err proto_err \
              rx_rst tx_rst rx_goaway tx_goaway \
              h2c_end h2s_end \
              h2c_fctl h2s_fctl h2c_blk h2s_blk; do
        cli "trace h2 event $ev"
    done
fi

cli "trace h2 sink $SINK"
cli "trace h2 verbosity $VERBOSITY"
cli "trace h2 level $LEVEL"
cli 'trace h2 start now'

cli_out 'show trace h2' | sed -n '1,90p'
