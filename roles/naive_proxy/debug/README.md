# naive_proxy debug toolkit

Operator-side shell scripts for diagnosing HAProxy H2 issues against a
deployed naive_proxy stack. Built during the 2026-04-29 investigation of
intermittent `PROTOCOL_ERROR` GOAWAY storms under speedtest-style load.

These scripts are **not rendered by the role at apply time**. Copy them to
`/tmp/` on the target host (or anywhere on `$PATH`) and invoke manually.
Two of them — `upload-via-tty.sh` and `download-via-tty.sh` — run on the
**operator's** machine, the other four run on the **target** host.

## What problem they help diagnose

Originally built to investigate intermittent `received invalid H2
frame header : dft=DATA/00 dfl=0 glitches=1` → `PROTOCOL_ERROR/01`
GOAWAY storms on a single H2 connection under speedtest-style load,
where every multiplexed stream on that connection died together. The
signature was reproducible across HAProxy 2.8 / 3.0 / 3.2 / 3.3
(checked) and traced down (see `temp/haproxy-3354-evidence/`) to a
PADDED-DATA padding-drain bug in `h2_frt_transfer_data()`: padding
bytes were never `b_del`'d from `h2c->dbuf`, so the next demux
iteration parsed a "header" out of the previous frame's leftover
padding. Reported as [haproxy/haproxy#3354](https://github.com/haproxy/haproxy/issues/3354)
and fixed upstream by `faf3e9a` ("BUG/MEDIUM: mux-h2: Properly
consume padding for DATA frames"), backported to the 3.3 maintenance
branch as `043db34` and slated for backport down to 2.8. The H2
tunings we exposed at the time (larger
`tune.h2.fe.initial-window-size`, `tune.h2.max-frame-size`,
`tune.h2.fe.rxbuf`) cut the visible failure rate but never eliminated
the underlying mis-alignment — they only reduced the rate at which
the misread happened to land on bytes that parsed as an obviously
invalid header. Molecule loopback tests never reproduced the bug
because loopback TCP has effectively infinite buffers and zero RTT.

These scripts remain useful for any future H2 demuxer regression that
manifests as an unexpected `bad_hdr` count, and as a reference for
collecting a `verbosity complete` H2 trace from a running HAProxy pod
without disturbing live clients.

The trigger event is captured in the H2 trace ring sink and reported by
`analyze.sh` as `bad_hdr` count.

## Files

| script | runs on | purpose |
|---|---|---|
| `start-capture.sh` | target | spawn N-second tcpdump (host + pod-netns) + journal-follow + ss/nstat sampler into a fresh capture dir |
| `h2trace-start.sh` | target | enable HAProxy H2 trace into a configurable ring sink with error/lifecycle/flow-control filter set |
| `stop-capture-dump-h2.sh` | target | terminate the 4 capture watchers, dump `show trace h2` and `show events <sink>` into the capture dir |
| `analyze.sh` | target | turn one capture dir into a structured text report (counters, time histograms, per-h2c stats, TCP pressure, term-state breakdown, historical TSV) |
| `upload-via-tty.sh` | operator | base64-stream a local file to a TCP-bridged TTY (e.g. `socat ... TCP:127.0.0.1:5555,...`) and verify SHA-256 |
| `download-via-tty.sh` | operator | the reverse: pull a file off the target through the same TTY |

All target-side scripts read the same configuration knobs from env vars
or matching `--flag` CLI options — flag wins. Defaults match the role's
unmodified rendering (container `naive-haproxy`, units
`podman-naive-{pod,haproxy,backend,decoy}.service`, etc.).

| knob | env var | default | scripts |
|---|---|---|---|
| Public NIC name | `NIC` | `enp1s0f1` | start-capture |
| Capture duration (s) | `DURATION` | `300` | start-capture |
| HAProxy container | `HAPROXY` | `naive-haproxy` | start-capture, h2trace-start, stop-capture-dump-h2 |
| Public listen port | `LISTEN_PORT` | `443` | start-capture |
| Pod-netns ports | `POD_PORTS` | `"443 8444 8080"` | start-capture |
| journalctl units | `UNITS` | `"podman-naive-haproxy.service podman-naive-backend.service"` | start-capture |
| systemd units to snapshot | `INSPECT_UNITS` | all four podman-naive units | start-capture |
| Capture dir prefix | `OUT_PREFIX` | `/tmp/naive-debug-` | start-capture |
| Capture dir glob | `OUT_GLOB` | `/tmp/naive-debug-*` | stop-capture-dump-h2, analyze |
| Admin socket | `ADMIN` | `"127.0.0.1 19999"` | h2trace-start, stop-capture-dump-h2 |
| Trace ring sink | `SINK` | `h2trace` | h2trace-start, stop-capture-dump-h2 |
| Trace verbosity | `VERBOSITY` | `advanced` | h2trace-start (use `complete` for full hexdump — see warning below) |
| Trace level | `LEVEL` | `developer` | h2trace-start |
| Cumulative TSV | `HISTORY_FILE` | `/tmp/naive-history.tsv` | analyze |
| TTY proxy address | `TTY_ADDR` | `127.0.0.1:5555` | upload-via-tty, download-via-tty |

## Prerequisites on the target host

1. **podman + naive_proxy pod running.** Standard role deployment.

2. **`stats socket ipv4@127.0.0.1:19999 level admin`** in HAProxy's
   `global` section. The CLI commands (`show events`, `trace h2 …`) all
   speak to this socket from inside the haproxy container via
   `podman exec naive-haproxy nc 127.0.0.1 19999`. The role does not
   render this directive by default — add it as a temporary debug knob
   when you need the toolkit, or override `--admin <H P>` if a
   different bind is in use.

3. **`ring h2trace { format timed; size 134217728 }`** declared as a
   top-level section in haproxy.cfg (the role renders this when
   `naive_proxy_haproxy_diagnostics_enabled: true`). Without it,
   `h2trace-start.sh` falls back to the built-in 1 MiB `buf0` sink and
   the trace ring rolls over within ~10 speedtest runs, biasing every
   counter you read out of it. 128 MiB is the default because at
   `verbosity complete` (full frame hex-dump per event) the ring fills
   ~10x faster than at the `advanced` default. Override `--sink <name>`
   if a different ring is declared.

   **`verbosity complete` warning.** Setting `--verbosity complete` on
   `h2trace-start.sh` makes HAProxy include the full frame contents
   (HEADERS payload, DATA payload bytes, etc.) in every trace event.
   For an HTTPS-terminating frontend this means **plaintext HTTP/2
   headers — including `Proxy-Authorization`, `Cookie`, `Authorization`
   — land in the trace ring**. Before sharing such a trace publicly,
   either (a) rotate any credentials that may have been seen during
   the capture window, or (b) substitute placeholder users in
   `naive_proxy_users` for the duration of the capture. The
   sanitiser at the bottom of `analyze.sh`'s output stack does not
   redact these by default — you must scrub the raw event log
   yourself.

4. **`tshark`** installed on the target. Used by `analyze.sh` for
   TCP-level zero-window / retransmission counts. Not strictly required:
   the script degrades to "(tshark not installed)" and skips that section.

5. **Passwordless sudo** for the operator user. Every script calls
   `sudo -n ...` for tcpdump, `nsenter`, and `journalctl`. If sudo
   prompts for a password they will fail silently.

## Bootstrapping the toolkit when you only have a TTY bridge

If your only access to the target is a TCP-bridged interactive shell
(common pattern: `socat - TCP:127.0.0.1:5555,...` against a tunnel),
the operator-side helpers are the way in. Both default to
`127.0.0.1:5555`, override with `--tty H:P` or `TTY_ADDR=H:P`.

```sh
# from the operator's machine (where the repo is checked out)
cd roles/naive_proxy/debug

# 1. push every target-side script into /tmp on the host
for s in start-capture h2trace-start stop-capture-dump-h2 analyze; do
    ./upload-via-tty.sh "${s}.sh" "/tmp/naive-${s}.sh"
done

# 2. then make them executable on the host
printf 'sudo chmod +x /tmp/naive-*.sh\n' | nc -w 5 127.0.0.1 5555
```

`upload-via-tty.sh` chunks base64 in 900-char windows because longer
single `nc` writes truncate on the wire. After the upload it asks the
remote to `sudo sha256sum` the file and prints both checksums so you
can verify integrity.

To pull artifacts back (capture dirs are typically <50 MB):

```sh
./download-via-tty.sh /tmp/naive-debug-<RUN_ID>/journal-follow.log ./journal-follow.log
./download-via-tty.sh /tmp/naive-debug-<RUN_ID>/haproxy-h2-events.log ./events.log
# raise --timeout for big pcaps:
./download-via-tty.sh --timeout 600 /tmp/naive-debug-<RUN_ID>/host-enp1s0f1-port443.pcap ./host.pcap
```

For ad-hoc commands that don't move files, `nc` directly works:

```sh
printf '%s\n' 'date -u; sudo systemctl is-active podman-naive-haproxy.service' \
    | nc -w 8 127.0.0.1 5555
```

Common gotchas:

- `nc` closes its end of the socket as soon as stdin EOFs. Pick a `-w`
  big enough for the remote command to finish writing back. For a
  multi-step shell pipeline expect to need `-w 20` or more.
- The remote bash echoes its prompt and bracketed-paste sequences
  (`[?2004h`, `[?2004l`) into your stdout. `download-via-tty.sh`
  strips them from the base64 stream; for ad-hoc commands you may
  want to wrap output in `__START__` / `__DONE__` markers and
  `sed -n '/__START__/,/__DONE__/p'`.
- Multi-line shell content (`<<EOF`, multi-line `bash -c`) tends to
  desync because the TTY treats each newline as a separate input.
  Always go through `upload-via-tty.sh` for multi-line scripts.

## Typical session

The session is driven by a human (or LLM agent) coordinating with
whoever fires the load test on the client side. **Do not start a
capture unilaterally** — captures are worthless without a load running,
and the load is generated externally (e.g. an Android speedtest app).
Always wait for explicit approval from the human controlling the
client before arming a capture.

```sh
# (on target — invoke directly or via TTY bridge with `printf … | nc …`)

# 1) Confirm haproxy.cfg has the debug knobs and admin socket. If not,
#    patch and `systemctl restart podman-naive-haproxy.service`.
sudo grep -E 'ring h2trace|stats socket' /opt/naive-proxy/haproxy.cfg

# 2) Enable H2 trace. Trace state resets on HAProxy restart, so re-run
#    this script every time you restart the haproxy container.
sudo /tmp/naive-h2trace-start.sh
# override defaults if needed:
# sudo HAPROXY=naive-edge ADMIN="10.0.0.1 9999" SINK=mytrace /tmp/naive-h2trace-start.sh
# sudo /tmp/naive-h2trace-start.sh --haproxy naive-edge --sink mytrace
# full hex-dump of frame contents (slows the demuxer + leaks plaintext
# HTTP/2 headers; see warning above):
# sudo /tmp/naive-h2trace-start.sh --verbosity complete

# 3) Start the capture. Output: __CAPTURE_STARTED__ ... pids ...
sudo /tmp/naive-start-capture.sh
# different NIC / shorter run:
# sudo /tmp/naive-start-capture.sh --nic eth0 --duration 120

# 4) The human now triggers the load test. Wait for them to confirm done.

# 5) Stop the capture and dump trace state into the capture dir.
sudo /tmp/naive-stop-capture-dump-h2.sh /tmp/naive-debug-<RUN_ID>

# 6) Analyze.
sudo /tmp/naive-analyze.sh /tmp/naive-debug-<RUN_ID>
```

If you have only the TTY bridge, replace each `sudo /tmp/naive-...sh`
with `printf '%s\n' 'sudo /tmp/naive-...sh' | nc -w N 127.0.0.1 5555`
and adjust `N` to the expected runtime of that step.

`analyze.sh` also appends one row per session to `/tmp/naive-history.tsv`
(override with `HISTORY_FILE=...` or `--history-file ...`) and reprints
the deduped table at the end, so multiple sessions are directly
comparable. Delete the TSV to start fresh.

## What the analyze report tells you

```
## RAW COUNTERS (the headline numbers per session)
  BADREQ (journal):                X   — H2 connections killed by GOAWAY
  ERR_CONNECTION_RESET (naive):    Y   — upstream connections reset by HAProxy
  PROTOCOL_ERROR (h2 trace):       Z   — every line in the GOAWAY chain
  bad_hdr (h2 trace, the trigger): N   — actual unique 'invalid H2 frame header' triggers
  unique h2c connections w/ trigger: M — number of distinct H2 connections that died
  wait_room (mbuf to client full): A   — output-buffer pressure events
  demux_full (dbuf from client):   B   — input-buffer pressure events
  rxbuf_full (per-stream rxbuf):   C   — per-stream-buffer pressure events (HAProxy ≤2.8)
  trace ring dropped events:       0   — must be 0 for trustworthy counts
```

Counter interpretation:

- **`BADREQ`** is the customer-visible failure count if you assume
  every load-test run that triggers GOAWAY shows up as a BADREQ session
  in HAProxy's log. With `tune.h2.fe.max-concurrent-streams 100`
  (default) one fail run produces ~30 BADREQs because the GOAWAY kills
  ~30 active streams at once.
- **`bad_hdr` and `unique h2c`** are the most direct measure of "did
  the bug fire?". If `unique h2c == N test runs that fail`, every fail
  is one bug fire. If `unique h2c < N` you have flaky tests not all of
  which were caused by HAProxy.
- **`wait_room` >> `demux_full`** → bottleneck is the SERVER → CLIENT
  direction (HAProxy can't push response data fast enough to a client
  that's busy uploading). Classic real-internet asymmetric load.
- **`demux_full` >> `wait_room`** → bottleneck is CLIENT → SERVER
  (HAProxy can't drain the upload from a slow / lossy client).
- **`trace ring dropped events != 0`** → ring overflowed. Counters
  above are biased downward (you only see the tail). Either shorten the
  capture or trim the H2 trace filter set in `h2trace-start.sh`.

The histograms answer the "fast fail vs slow fail" question:
- `BADREQ time histogram` shows when GOAWAYs fired during the capture
  window. Tweaks that push the first BADREQ later (t+50s, t+100s,
  ...) are buying the test more time before the bug bites. Tweaks
  with no effect leave first-BADREQ at t+1s.
- `ERR_CONNECTION_RESET time histogram` is the same trend viewed from
  the naive backend's side; spikes correlate with GOAWAY events
  because every active CONNECT tunnel gets reset.

The per-h2c block extracts `txw=` and `rxw=` at the time of each
bad_hdr. `txw` near 67108864 (= 64 MiB - 1) means the client granted
the maximum H2 stream window — the bottleneck is in HAProxy's mbuf,
not in H2 flow control. `rxw` matches `tune.h2.fe.initial-window-size`
plus any granted WINDOW_UPDATEs.

The TCP-level section is the only one that survives even if the trace
ring is misconfigured. Server-side `zero_window` count and client-side
retransmits give you a HAProxy-independent measure of how stressed the
TCP path was.

## Caveats

- **Pcap is encrypted.** The captures use `-s 96` to keep just the TCP
  headers; the H2 payload is unreadable without TLS keys. To decrypt,
  enable SSLKEYLOGFILE on the client side and merge with the pcap.
  `analyze.sh` does not depend on plaintext, only TCP metadata.
- **H2 trace state is not persistent.** It resets every time the
  haproxy container restarts. Always re-run `h2trace-start.sh` after a
  restart.
- **Capture self-terminates after `DURATION` seconds.** If the operator
  forgets to call `stop-capture-dump-h2.sh`, the tcpdumps and
  journal-follow exit on their own but you lose the H2 trace dump and
  the meta.after stats.
- **Sample sizes matter.** With `n < 10` test runs per phase, fail-rate
  estimates have ±20 percentage points of noise. Compare phases at
  `n = 10` minimum.
- **Counter normalisation.** "More wait_room events" doesn't directly
  mean "worse". Shrinking `tune.bufsize` makes each event smaller and
  more granular, inflating counts. Always interpret in conjunction
  with the user-reported pass/fail rate, which is the ground truth.
- **The role does not render any of these debug knobs.** Adding
  `ring h2trace`, the admin socket, or the `tune.h2.*` workarounds is
  manual editing of `/opt/naive-proxy/haproxy.cfg` followed by
  `systemctl restart podman-naive-haproxy.service`. Always back up the
  current cfg first; the role's idempotent re-run will overwrite your
  edits the next time the playbook runs.
- **Two-sided sync.** When you fix a target-side script directly on
  the host (e.g. `install -m 0755 /tmp/foo.sh.new /tmp/foo.sh`),
  immediately mirror the change back to this folder so the next
  operator who pulls the repo gets the fixed version. Same applies in
  reverse.
