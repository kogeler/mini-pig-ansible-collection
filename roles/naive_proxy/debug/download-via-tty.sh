#!/usr/bin/env bash
# Download a remote file from a TCP-bridged TTY (e.g. socat ...
# TCP:127.0.0.1:5555,...). Reads `sudo base64 -w0 <path>` over the wire,
# scrubs ANSI/PS1 noise that the bash prompt injects, and base64-decodes
# back to the local target file.
#
# Usage:  download-via-tty.sh [--tty HOST:PORT] [--timeout SECS] <remote_path> <local_file>
#   default --tty = 127.0.0.1:5555 (env: TTY_ADDR)
#   default --timeout = 60 (long enough for hundreds of KB; raise for MB)
#
# The on-the-wire format is plain base64 produced by `sudo base64 -w0` so
# the remote side does not need anything special — just sudo.

set -euo pipefail

: "${TTY_ADDR:=127.0.0.1:5555}"
TIMEOUT=60

REMOTE=""
LOCAL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --tty)     TTY_ADDR=$2; shift 2 ;;
        --timeout) TIMEOUT=$2; shift 2 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        --) shift; break ;;
        -*) echo "unknown flag: $1" >&2; exit 2 ;;
        *)  if [ -z "$REMOTE" ]; then REMOTE=$1; else LOCAL=$1; fi; shift ;;
    esac
done
[ -n "$REMOTE" ] && [ -n "$LOCAL" ] || {
    echo "usage: $0 [--tty H:P] [--timeout S] <remote_path> <local_file>" >&2; exit 2; }

HOST=${TTY_ADDR%:*}
PORT=${TTY_ADDR##*:}

TMP=$(mktemp)
printf '%s\n' "echo __DLBEGIN__; sudo base64 -w0 ${REMOTE}; echo; echo __DLEND__" \
    | nc -w "$TIMEOUT" "$HOST" "$PORT" \
    > "$TMP"

# Extract between markers, drop CR + ANSI escapes + bash bracketed-paste.
awk '/__DLBEGIN__/{flag=1; next} /__DLEND__/{flag=0} flag' "$TMP" \
    | tr -d '\r' \
    | sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g' \
    | sed 's/\[?2004[hl]//g' \
    > "$TMP.b64"

# Join base64 onto one line in case the remote split it across lines.
tr -d '\n' < "$TMP.b64" > "$TMP.b64.j"
base64 -d "$TMP.b64.j" > "$LOCAL"

LSZ=$(wc -c < "$LOCAL")
echo "downloaded bytes: $LSZ"
rm -f "$TMP" "$TMP.b64" "$TMP.b64.j"
