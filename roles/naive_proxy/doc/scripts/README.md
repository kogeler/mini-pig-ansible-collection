# Production debug scripts

These scripts collect and analyse HAProxy HTTP/2 evidence from an already
deployed `naive_proxy` stack. The role does not install them on managed hosts;
copy only the scripts needed for an investigation.

For the decision tree and safe collection procedure, start with the
[debugging runbook](../maintenance/debugging.md). The role/runtime contract is
documented separately in [Runtime](../contracts/runtime.md).

They originated during an intermittent production failure with this signature:

```text
received invalid H2 frame header ... glitches=1
PROTOCOL_ERROR/01 GOAWAY
ERR_CONNECTION_RESET
```

The root cause was HAProxy's `h2_frt_transfer_data()` leaving padded-DATA
padding bytes in its demux buffer, so the next iteration parsed those bytes as
another frame header. The upstream fix is
[`faf3e9a`](https://github.com/haproxy/haproxy/commit/faf3e9ac3a5df7258b0abbc06b0e0378617a18e5),
tracked in [haproxy/haproxy#3354](https://github.com/haproxy/haproxy/issues/3354)
and backported to 3.3 as `043db34`. Larger H2 buffers/windows reduced the
visible failure rate but did not fix the misalignment. The scripts remain
useful for later H2 demultiplexer and flow-control regressions.

## Script map

| Script | Runs on | Purpose |
|---|---|---|
| `h2trace-start.sh` | target host | enable HAProxy H2 tracing through the admin socket |
| `start-capture.sh` | target host | collect bounded host/pod pcaps, journals, socket counters, and service state |
| `stop-capture-dump-h2.sh` | target host | stop collectors and dump the trace ring |
| `analyze.sh` | target host | report H2 errors, pressure events, timing, TCP counters, and history |
| `upload-via-tty.sh` | operator host | copy a script through a TCP-bridged TTY and verify its SHA-256 |
| `download-via-tty.sh` | operator host | retrieve an artifact through the same bridge |

Every script supports `--help`. Command-line flags override matching
environment variables.

## Configuration reference

| Setting | Environment variable | Default | Scripts |
|---|---|---|---|
| Public NIC | `NIC` | `enp1s0f1` | `start-capture.sh` |
| Capture duration | `DURATION` | `300` seconds | `start-capture.sh` |
| HAProxy container | `HAPROXY` | `naive-haproxy` | capture/trace/stop |
| Public listen port | `LISTEN_PORT` | `443` | `start-capture.sh` |
| Pod-netns ports | `POD_PORTS` | `443 8444 8080` | `start-capture.sh` |
| Followed journals | `UNITS` | HAProxy and backend units | `start-capture.sh` |
| Snapshotted units | `INSPECT_UNITS` | pod, HAProxy, backend, decoy | `start-capture.sh` |
| Capture prefix | `OUT_PREFIX` | `/tmp/naive-debug-` | `start-capture.sh` |
| Capture lookup glob | `OUT_GLOB` | `/tmp/naive-debug-*` | stop/analyse |
| Admin endpoint inside pod netns | `ADMIN` | `127.0.0.1 19999` | trace/stop |
| Trace sink | `SINK` | `h2trace` | trace/stop |
| Trace verbosity | `VERBOSITY` | `advanced` | `h2trace-start.sh` |
| Trace level | `LEVEL` | `developer` | `h2trace-start.sh` |
| Trace event set | `EVENTS` | `lifecycle` | `h2trace-start.sh` |
| History table | `HISTORY_FILE` | `/tmp/naive-history.tsv` | `analyze.sh` |
| TTY bridge | `TTY_ADDR` | `127.0.0.1:5555` | upload/download |

Add port `8445` when the investigation needs AnyTLS traffic. The default set
is intentionally focused on the Naive H2 frontend/backend path.

## Prerequisites

Enable the role-supported HAProxy diagnostics before deploying:

```yaml
naive_proxy_haproxy_diagnostics_enabled: true
```

This renders the admin socket and the `h2trace` ring used by the scripts.
HAProxy binds the admin socket in the pod network namespace; the pod publishes
it only on host `127.0.0.1:19999`. The default ring is 128 MiB. See the
[HAProxy contract](../contracts/haproxy.md#diagnostics).

The target host also needs:

- a running role deployment and passwordless `sudo` for the operator;
- `podman`, `tcpdump`, `nsenter`, `journalctl`, `ss`, and `nstat`;
- `tshark` for TCP-level analysis (optional; `analyze.sh` degrades cleanly).

Do not edit `/opt/naive-proxy/haproxy.cfg` by hand to add these settings. A
subsequent role run will overwrite manual changes; use the role variables.

## Typical capture

A capture is useful only when it overlaps the failing client workload. Agree
on the start/stop window with the person driving the client before arming it.

```bash
# Run on the target host after deploying with diagnostics enabled.
sudo ./h2trace-start.sh
sudo ./start-capture.sh --nic eth0 --duration 300

# Trigger the external workload, then use the directory printed above.
sudo ./stop-capture-dump-h2.sh /tmp/naive-debug-<RUN_ID>
sudo ./analyze.sh /tmp/naive-debug-<RUN_ID>
```

Tracing is reset whenever HAProxy restarts, so run `h2trace-start.sh` again
after every restart. Capture processes are wrapped in `timeout`; forgetting the
stop command does not leave them running indefinitely, but it does omit the
final trace dump.

Useful overrides:

```bash
sudo ./h2trace-start.sh --events all
sudo ./start-capture.sh --listen-port 8443 --pod-ports "8443 8444 8080"
sudo ./analyze.sh --history-file /tmp/naive-history.tsv /tmp/naive-debug-<RUN_ID>
```

The default `advanced` trace verbosity is normally sufficient. `complete`
includes frame payloads and plaintext HTTP/2 headers, including credentials
such as `Proxy-Authorization`, and fills the ring much faster. Treat such
output as secret, rotate exposed credentials, and redact it before sharing.

`analyze.sh` appends one row per capture to its history TSV and prints the
deduplicated table. Delete or move that file to begin a new comparison series.

## TCP-bridged TTY

When the only access path is a shell bridged to `127.0.0.1:5555`, run these on
the operator host:

```bash
for script in h2trace-start start-capture stop-capture-dump-h2 analyze; do
  ./upload-via-tty.sh "${script}.sh" "/tmp/naive-${script}.sh"
done

printf '%s\n' 'sudo chmod +x /tmp/naive-*.sh' | nc -w 5 127.0.0.1 5555
./download-via-tty.sh --timeout 600 \
  /tmp/naive-debug-<RUN_ID>/journal-follow.log ./journal-follow.log
```

The transfer helpers use base64 chunks because long writes can be truncated by
interactive bridges. They also strip common prompt/ANSI noise and verify the
uploaded checksum. Increase `--timeout` for large pcaps.

For short read-only commands, direct `nc` is sufficient:

```bash
printf '%s\n' \
  'date -u; sudo systemctl is-active podman-naive-haproxy.service' \
  | nc -w 8 127.0.0.1 5555
```

TTY bridges have three recurring traps:

- `nc` closes when stdin reaches EOF; choose `-w` long enough for all output;
- prompts and bracketed-paste sequences can contaminate stdout, so use
  explicit start/end markers for ad-hoc extraction;
- multiline heredocs and `bash -c` payloads desynchronise easily; upload a
  script instead. The helpers chunk base64 at 900 characters for this reason.

## Reading the report

The headline counters are:

- `bad_hdr` and unique `h2c`: direct evidence of an invalid H2 frame-header
  trigger and the number of affected connections;
- `BADREQ` and `ERR_CONNECTION_RESET`: client-visible HAProxy failures and
  corresponding backend resets;
- `wait_room`: pressure sending data toward the client;
- `demux_full`: pressure receiving data from the client;
- `rxbuf_full`: per-stream receive-buffer pressure on older HAProxy branches;
- dropped trace events: a non-zero value makes trace-derived counts incomplete.

`BADREQ` is the customer-visible HAProxy session failure count, while one
`bad_hdr` can kill many concurrent streams and produce many BADREQ entries.
`bad_hdr` plus unique `h2c` is therefore the best answer to “how many H2
connections triggered the parser failure?”.

`wait_room` much greater than `demux_full` points to pressure sending responses
toward the client; the inverse points to pressure draining client uploads. A
non-zero dropped-event count means the ring rolled over and all trace-derived
counts are lower bounds. Shorten the capture or narrow the event filter before
comparing runs.

The time histograms show whether a change delays the first failure. The per-H2
connection block records `txw` and `rxw`: `txw` near `67108864` means the client
has already granted the maximum stream window, while `rxw` reflects the
configured initial window plus updates. The TCP section remains useful even
when HAProxy tracing is missing: zero windows and retransmits describe network
pressure independently of the demuxer.

Interpret counters together with workload pass/fail results. Changing buffer
sizes also changes event frequency, so raw counts are not directly comparable
across every tuning. Use at least ten comparable runs when estimating a failure
rate; smaller samples have large uncertainty.

## Caveats and evidence handling

- Host and pod pcaps use a short snap length and normally contain encrypted TLS
  plus TCP metadata, not readable H2 payloads. Decryption requires client-side
  TLS keys and a separate workflow.
- HAProxy trace state resets on every HAProxy restart. Re-run
  `h2trace-start.sh` after each restart.
- Collectors self-terminate after `DURATION`, but only the stop script records
  final state and dumps the trace ring.
- `verbosity complete` can record plaintext authorization headers. The tools
  do not guarantee redaction of the raw event log.
- Buffer/event counter frequency depends on configuration; workload outcome is
  the ground truth.
- If a copied target-side script is fixed during an incident, mirror the same
  change back into this directory before the investigation ends.
- Never start a capture without coordinating the external workload window;
  an idle capture is not useful evidence.
