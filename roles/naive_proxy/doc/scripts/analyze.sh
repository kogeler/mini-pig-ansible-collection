#!/usr/bin/env bash
# Comprehensive analysis of a naive_proxy debug capture session.
#
# Usage:  analyze.sh [--out-glob GLOB] [--history-file PATH] [<capture_dir>]
#   default <capture_dir> = newest match of OUT_GLOB
#
# Configurable (env or CLI flag):
#   --out-glob     | OUT_GLOB      glob for default capture-dir auto-detect
#                                   (default: /tmp/naive-debug-*)
#   --history-file | HISTORY_FILE  cumulative TSV across sessions
#                                   (default: /tmp/naive-history.tsv)
#
# Inputs (created by start-capture.sh + stop-capture-dump-h2.sh):
#   <dir>/journal-follow.log         HAProxy + naive container journals
#   <dir>/haproxy-h2-events.log      H2 trace ring dump (falls back to
#                                     haproxy-h2-events-buf0.log for legacy)
#   <dir>/host-*.pcap                host-side pcap (auto-detected by glob)
#   <dir>/pod-*.pcap                 pod-netns pcap (auto-detected by glob)
#   <dir>/meta.before.txt, pids.txt, ss-sample-and-after.log
#
# Output: a structured text report on stdout that covers
#   1) raw counters (BADREQ, ERR_CONNECTION_RESET, PROTOCOL_ERROR, bad_hdr, ...)
#   2) which event types fired (h2c_err / wait_room / demux_full / ...)
#   3) bug trigger frame distribution (always 'dft=DATA/00 dfl=0 glitches=N')
#   4) time-to-first-failure (seconds since capture start)
#   5) per-failed-h2c statistics: stream count, txw/rxw at error
#   6) BADREQ time histogram (per-second buckets)
#   7) ERR_CONNECTION_RESET time histogram (5 s buckets)
#   8) TCP-level zero-window / retransmit counts from host pcap
#   9) backend term-state breakdown (CD/SD/PR per backend)
#  10) historical comparison row appended to <HISTORY_FILE>

set -u

: "${OUT_GLOB:=/tmp/naive-debug-*}"
: "${HISTORY_FILE:=/tmp/naive-history.tsv}"

DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out-glob)     OUT_GLOB=$2; shift 2 ;;
        --history-file) HISTORY_FILE=$2; shift 2 ;;
        -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
        --) shift; break ;;
        -*) echo "unknown flag: $1" >&2; exit 2 ;;
        *)  DIR=$1; shift ;;
    esac
done
[ -n "$DIR" ] || DIR=$(ls -dt $OUT_GLOB 2>/dev/null | head -1)
[ -d "$DIR" ] || { echo "no capture dir: $DIR"; exit 1; }

BUF_RAW="$DIR/haproxy-h2-events.log"
[ -s "$BUF_RAW" ] || BUF_RAW="$DIR/haproxy-h2-events-buf0.log"   # legacy
J="$DIR/journal-follow.log"
# Auto-detect pcap files by prefix (start-capture writes host-<NIC>-* and pod-any-<PORTS>-*).
HOSTPCAP=$(ls "$DIR"/host-*.pcap 2>/dev/null | head -1)
PODPCAP=$(ls "$DIR"/pod-*.pcap 2>/dev/null | head -1)

# Capture start epoch is needed for time bucketing. Derive from meta.before.txt
# header (first 'time:' line) or fall back to the first frame_time in pcap.
START_EPOCH=""
if [ -s "$DIR/meta.before.txt" ]; then
    START_HUMAN=$(sudo grep -m1 -oE '^start=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+:-]+' "$DIR/meta.before.txt" 2>/dev/null \
                  | sed 's/^start=//' || true)
    if [ -n "$START_HUMAN" ]; then
        START_EPOCH=$(date -u -d "$START_HUMAN" +%s 2>/dev/null || true)
    fi
fi
if [ -z "$START_EPOCH" ] && command -v tshark >/dev/null 2>&1 && [ -s "$HOSTPCAP" ]; then
    START_EPOCH=$(sudo tshark -nr "$HOSTPCAP" -c1 -T fields -e frame.time_epoch 2>/dev/null \
                  | awk '{printf "%d\n", $1}')
fi
[ -n "$START_EPOCH" ] || START_EPOCH=$(stat -c%Y "$DIR" 2>/dev/null || echo 0)

# Capture duration → end epoch. Used to clip the trace ring to events that
# actually happened during this capture (the ring is in-memory and persists
# across capture sessions until HAProxy restarts).
DURATION=$(sudo grep -m1 -oE '^duration_s=[0-9]+' "$DIR/meta.before.txt" 2>/dev/null \
           | cut -d= -f2 || true)
DURATION=${DURATION:-300}
END_EPOCH=$((START_EPOCH + DURATION))

# Build a window-filtered copy of the trace ring. Use mktime (gawk) for speed
# and set TZ=UTC0 so that bare 'YYYY MM DD HH MM SS' strings are interpreted
# in UTC (the trace timestamps end with +00:00).
BUF=$(mktemp)
trap 'rm -f "$BUF"' EXIT
if [ -s "$BUF_RAW" ]; then
    # mawk's match() does not support capture-group arrays (gawk extension),
    # so use substr() to pull the timestamp at fixed offsets.
    # Format: <N>YYYY-MM-DDTHH:MM:SS.fff... → digits start right after the '>'.
    TZ=UTC0 sudo awk -v s="$START_EPOCH" -v e="$END_EPOCH" '
    BEGIN { keep = 0 }
    {
        if ($0 ~ /^<[0-9]+>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}/) {
            # Find offset of the first digit (just past "<N>").
            i = index($0, ">")
            yyyy = substr($0, i+1, 4)
            mm   = substr($0, i+6, 2)
            dd   = substr($0, i+9, 2)
            HH   = substr($0, i+12, 2)
            MM   = substr($0, i+15, 2)
            SS   = substr($0, i+18, 2)
            ts = mktime(yyyy " " mm " " dd " " HH " " MM " " SS)
            keep = (ts >= s && ts <= e) ? 1 : 0
        }
        if (keep) print
    }' "$BUF_RAW" > "$BUF" 2>/dev/null || true
fi
BUF_RAW_LINES=$(sudo wc -l < "$BUF_RAW" 2>/dev/null || echo 0)
BUF_LINES=$(wc -l < "$BUF" 2>/dev/null || echo 0)

DIRNAME=$(basename "$DIR")
echo "=========================================================================="
echo "  Capture analysis: $DIRNAME"
echo "  trace ring (raw): $BUF_RAW  ($BUF_RAW_LINES lines, includes events from prior sessions if HAProxy was not restarted)"
echo "  trace ring (windowed): $BUF_LINES lines kept after clipping to $START_EPOCH..$END_EPOCH"
echo "  start epoch:      $START_EPOCH ($(date -u -d "@$START_EPOCH" +%FT%TZ 2>/dev/null))"
echo "  end epoch:        $END_EPOCH (capture duration: ${DURATION}s)"
echo "=========================================================================="

# ------------------------------------------------------------- (1) raw counters
echo
echo "## RAW COUNTERS (the headline numbers per session)"
cnt() { sudo grep -c "$@" 2>/dev/null; true; }
BADREQ=$(cnt -- '<BADREQ>' "$J")
ERR_RST=$(cnt -- 'ERR_CONNECTION_RESET' "$J")
BAD_HDR=$(cnt -- 'invalid H2 frame header' "$BUF")
PROTO_ERR=$(cnt -- 'PROTOCOL_ERROR' "$BUF")
UNIQ_H2C=$(sudo grep -oE 'invalid H2 frame header.*h2c=0x[0-9a-f]+' "$BUF" 2>/dev/null \
           | grep -oE '0x[0-9a-f]+' | sort -u | wc -l)
WAIT_ROOM=$(cnt -- 'waiting for room in output buffer' "$BUF")
DEMUX_FULL=$(sudo grep -cE 'demux (buffer )?full' "$BUF" 2>/dev/null; true)
RXBUF_FULL=$(cnt -- 'rxbuf is full' "$BUF")
: "${BADREQ:=0}" "${ERR_RST:=0}" "${BAD_HDR:=0}" "${PROTO_ERR:=0}" "${WAIT_ROOM:=0}" "${DEMUX_FULL:=0}" "${RXBUF_FULL:=0}"
DROPPED=$(sudo grep -m1 -oE '\[[0-9]+ dropped\]' "$DIR/haproxy-h2-trace-status.out" 2>/dev/null \
          | grep -oE '[0-9]+' || echo "?")

echo "  BADREQ (journal):                $BADREQ  — H2 conn killed by GOAWAY"
echo "  ERR_CONNECTION_RESET (naive):    $ERR_RST  — upstream connections reset by HAProxy"
echo "  PROTOCOL_ERROR (h2 trace):       $PROTO_ERR  — every line in the GOAWAY chain"
echo "  bad_hdr (h2 trace, the trigger): $BAD_HDR  — 'invalid H2 frame header' events"
echo "  unique h2c connections w/ trigger: $UNIQ_H2C"
echo "  wait_room (mbuf to client full): $WAIT_ROOM"
echo "  demux_full (dbuf from client):   $DEMUX_FULL"
echo "  rxbuf_full (per-stream rxbuf):   $RXBUF_FULL"
echo "  trace ring dropped events:       $DROPPED  (0 means no overflow — counts trustworthy)"

# ------------------------------------------------------- (3) bug trigger frame
echo
echo "## BUG TRIGGER FRAME (always DATA/00 dfl=0 glitches=N)"
sudo grep -oE 'dft=[A-Z_]+/[0-9a-fA-F]+ dfl=[0-9]+' "$BUF" 2>/dev/null \
    | sort | uniq -c | sort -rn | head -5
echo "  glitches count distribution:"
sudo grep -oE 'glitches=[0-9]+' "$BUF" 2>/dev/null \
    | sort | uniq -c | sort -rn | head -5

# ------------------------------------------------------- (4) time-to-first-fail
echo
echo "## TIME TO FIRST FAILURE (seconds since capture start)"
FIRST_BAD_HDR_EP=$(sudo grep -m1 'invalid H2 frame header' "$BUF" 2>/dev/null \
                   | grep -oE '^<[0-9]+>[0-9-]+T[0-9:.]+' | sed 's/^<[0-9]*>//' \
                   | head -1)
if [ -n "$FIRST_BAD_HDR_EP" ]; then
    F_EP=$(date -u -d "$FIRST_BAD_HDR_EP" +%s 2>/dev/null || echo "$START_EPOCH")
    DT=$((F_EP - START_EPOCH))
    echo "  first bad_hdr: $FIRST_BAD_HDR_EP  (t+${DT}s after capture start)"
else
    echo "  no bad_hdr events captured — clean run!"
fi

FIRST_BADREQ=$(sudo grep -m1 '<BADREQ>' "$J" 2>/dev/null \
               | awk '{print $1}' | sed 's/+00:00$//')
if [ -n "$FIRST_BADREQ" ]; then
    F_EP=$(date -u -d "$FIRST_BADREQ" +%s 2>/dev/null || echo "$START_EPOCH")
    DT=$((F_EP - START_EPOCH))
    echo "  first BADREQ:  $FIRST_BADREQ  (t+${DT}s)"
fi

# --------------------------------------------- (5) per-failed-h2c stream stats
echo
echo "## PER-FAILED-H2C STATISTICS (impact size)"
echo "  for each unique h2c that hit bad_hdr, count rst_streams + extract first txw/rxw:"
sudo grep -oE 'invalid H2 frame header.*h2c=0x[0-9a-f]+' "$BUF" 2>/dev/null \
    | grep -oE '0x[0-9a-f]+' | sort -u | head -20 | while read addr; do
    # count unique h2s IDs that received rst_stream (each stream emits 2 entering+leaving)
    streams=$(sudo grep "h2s_send_rst_stream.*h2c=$addr" "$BUF" 2>/dev/null \
              | grep -oE 'h2s=0x[0-9a-f]+' | sort -u | wc -l)
    txw_sample=$(sudo grep "h2c=$addr.*txw=" "$BUF" 2>/dev/null \
                 | grep -oE 'txw=[0-9]+' | head -1)
    rxw_sample=$(sudo grep "h2c=$addr.*rxw=" "$BUF" 2>/dev/null \
                 | grep -oE 'rxw=[0-9]+' | head -1)
    printf "    %s  streams_killed=%-3s  first_%s  first_%s\n" \
        "$addr" "$streams" "${txw_sample:-txw=?}" "${rxw_sample:-rxw=?}"
done

# ----------------------------------------------- (6) BADREQ time histogram
echo
echo "## BADREQ time histogram (1 s buckets)"
sudo grep '<BADREQ>' "$J" 2>/dev/null \
    | awk -v s="$START_EPOCH" '{
        ts=$1; gsub(/T/, " ", ts); gsub(/\+00:00$/, "", ts)
        cmd="date -u -d \""ts"\" +%s 2>/dev/null"
        cmd | getline e; close(cmd)
        b = e - s
        buckets[b]++
    } END { for (k in buckets) printf "%d %d\n", k, buckets[k] }' \
    | sort -n \
    | awk '{ bar=""; for (k=0; k<$2 && k<60; k++) bar=bar"#"; printf "  t+%4ds  %4d %s\n", $1, $2, bar }' \
    | head -50

# ---------------------------------------- (7) ERR_CONNECTION_RESET histogram
echo
echo "## ERR_CONNECTION_RESET time histogram (5 s buckets)"
sudo grep ERR_CONNECTION_RESET "$J" 2>/dev/null \
    | awk -v s="$START_EPOCH" '{
        ts=$1; gsub(/T/, " ", ts); gsub(/\+00:00$/, "", ts)
        cmd="date -u -d \""ts"\" +%s 2>/dev/null"
        cmd | getline e; close(cmd)
        b = int((e - s)/5)*5
        buckets[b]++
    } END { for (k in buckets) printf "%d %d\n", k, buckets[k] }' \
    | sort -n \
    | awk '{ bar=""; for (k=0; k<$2 && k<40; k++) bar=bar"#"; printf "  t+%4ds  %4d %s\n", $1, $2, bar }'

# ---------------------------------------- (8) TCP-level zero-window from pcap
echo
echo "## TCP-LEVEL PRESSURE FROM HOST PCAP"
if command -v tshark >/dev/null 2>&1 && [ -s "$HOSTPCAP" ]; then
    echo "  zero-window events from server (HAProxy can't accept more from client):"
    sudo tshark -nr "$HOSTPCAP" \
        -Y 'tcp.srcport == 443 && tcp.analysis.zero_window' \
        -T fields -e ip.dst -e tcp.dstport 2>/dev/null \
        | awk '{print $1":"$2}' | sort | uniq -c | sort -rn | head -5
    SRV_ZW=$(sudo tshark -nr "$HOSTPCAP" -Y 'tcp.srcport == 443 && tcp.analysis.zero_window' 2>/dev/null | wc -l)
    CLI_ZW=$(sudo tshark -nr "$HOSTPCAP" -Y 'tcp.dstport == 443 && tcp.analysis.zero_window' 2>/dev/null | wc -l)
    SRV_RT=$(sudo tshark -nr "$HOSTPCAP" -Y 'tcp.srcport == 443 && tcp.analysis.retransmission' 2>/dev/null | wc -l)
    CLI_RT=$(sudo tshark -nr "$HOSTPCAP" -Y 'tcp.dstport == 443 && tcp.analysis.retransmission' 2>/dev/null | wc -l)
    echo "  totals:  srv→cli zero_window=$SRV_ZW  retransmits=$SRV_RT"
    echo "           cli→srv zero_window=$CLI_ZW  retransmits=$CLI_RT"
else
    echo "  (tshark not installed or no host pcap)"
fi

# ---------------------------------------- (9) backend term-state breakdown
echo
echo "## JOURNAL: HAProxy session terminator codes per backend"
for be in "be_naive/naive" "be_https/local-https" "be_decoy/caddy"; do
    n=$(sudo grep -c "$be" "$J" 2>/dev/null; true)
    n=${n:-0}
    if [ "${n}" != "0" ]; then
        echo "  $be ($n total):"
        sudo grep "$be" "$J" 2>/dev/null \
            | grep -oE '[A-Z]{2}--' | sort | uniq -c | sort -rn | head -5
    fi
done

# -------------------------------- (10) historical comparison append
HIST="$HISTORY_FILE"
[ -e "$HIST" ] || printf "ts\tdir\tBADREQ\tERR_RST\tPROTO_ERR\tbad_hdr\tuniq_h2c\twait_room\tdemux_full\tdropped\n" > "$HIST"
printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
       "$(date -u -d "@$START_EPOCH" +%FT%TZ)" "$DIRNAME" \
       "$BADREQ" "$ERR_RST" "$PROTO_ERR" "$BAD_HDR" "$UNIQ_H2C" "$WAIT_ROOM" "$DEMUX_FULL" "$DROPPED" >> "$HIST"

echo
echo "## HISTORICAL TABLE  ($HIST — dedup by dir, keep latest)"
# dedup: keep header row, then for each unique dir use only its latest entry
awk -F'\t' '
    NR==1 { print; next }
    NF<10 { next }                                      # skip malformed rows
    { rows[$2] = $0 }                                    # keep last row for each dir
    END { for (d in rows) print rows[d] }
' "$HIST" | awk -F'\t' '
    NR==1 { for(i=1;i<=NF;i++){h[i]=$i; w[i]=length($i)}; cols=NF }
    NR>1  { for(i=1;i<=NF;i++) if(length($i)>w[i]) w[i]=length($i) }
    { rows[NR]=$0 }
    END {
        for(r=1;r<=NR;r++){
            n=split(rows[r],f,FS)
            line=""
            for(i=1;i<=n;i++) line = line sprintf("%-*s  ", w[i], f[i])
            print line
        }
    }'

echo
echo "==========================================================================="
echo "  done."
