#!/usr/bin/env bash
# Upload a local file to a remote shell exposed over a TCP-bridged TTY (e.g.
# socat ... TCP:127.0.0.1:5555,...). Streams base64 in 900-char chunks
# because longer single nc writes truncate on the wire, and verifies the
# SHA-256 on the remote side after decode.
#
# Usage:  upload-via-tty.sh [--tty HOST:PORT] <local_file> <remote_path>
#   default --tty = 127.0.0.1:5555 (env: TTY_ADDR)
#
# Requires only `nc`, `base64`, `sha256sum` on the local side, and a working
# bash on the remote side (any shell that understands `printf`, redirections,
# and can pipe to base64 -d would do).

set -euo pipefail

: "${TTY_ADDR:=127.0.0.1:5555}"
CHUNK=900   # per-chunk byte budget

LOCAL=""
REMOTE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --tty)     TTY_ADDR=$2; shift 2 ;;
        --chunk)   CHUNK=$2; shift 2 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        --) shift; break ;;
        -*) echo "unknown flag: $1" >&2; exit 2 ;;
        *)  if [ -z "$LOCAL" ]; then LOCAL=$1; else REMOTE=$1; fi; shift ;;
    esac
done
[ -n "$LOCAL" ] && [ -n "$REMOTE" ] || {
    echo "usage: $0 [--tty H:P] <local_file> <remote_path>" >&2; exit 2; }

# Split TTY_ADDR into host and port (nc wants them as separate args).
HOST=${TTY_ADDR%:*}
PORT=${TTY_ADDR##*:}

B64=$(base64 -w0 "$LOCAL")
SHA=$(sha256sum "$LOCAL" | awk '{print $1}')

# Truncate the remote staging file.
printf '%s\n' ": > /tmp/.upload.b64" | nc -w 5 "$HOST" "$PORT" >/dev/null

# Stream the base64 in chunks.
i=0
while [ $i -lt ${#B64} ]; do
    PART=${B64:i:CHUNK}
    printf '%s\n' "printf '%s' '${PART}' >> /tmp/.upload.b64" \
        | nc -w 5 "$HOST" "$PORT" >/dev/null
    i=$((i + CHUNK))
done

# Decode + install + verify.
printf '%s\n' \
    "echo __UP__; base64 -d /tmp/.upload.b64 | sudo tee ${REMOTE} >/dev/null && sudo sha256sum ${REMOTE}; echo __UPEND__" \
    | nc -w 12 "$HOST" "$PORT" \
    | sed -n '/__UP__/,/__UPEND__/p' \
    | tail -3

echo "local sha: $SHA"
