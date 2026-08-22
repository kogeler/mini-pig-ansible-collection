# Debugging

Start with the narrowest layer that can explain the symptom. Keep a failed
Molecule instance alive until evidence is collected.

## Local Molecule triage

Run through Make and preserve the full log:

```bash
cd roles/naive_proxy/molecule
make default-podman-converge > /tmp/naive-converge.log 2>&1
make default-podman-verify > /tmp/naive-verify.log 2>&1

rg -n 'fatal:|FAILED|ERROR|PLAY RECAP|SCENARIO RECAP' \
  /tmp/naive-converge.log /tmp/naive-verify.log
```

Do not immediately run `destroy`. Inspect the instance from the outer host:

```bash
podman exec molecule-naive-proxy systemctl --failed --no-pager
podman exec molecule-naive-proxy systemctl status \
  podman-naive-pod.service podman-naive-haproxy.service \
  podman-naive-backend.service podman-naive-anytls.service --no-pager -l
podman exec molecule-naive-proxy journalctl -u podman-naive-haproxy.service \
  --no-pager -n 200
podman exec molecule-naive-proxy podman ps -a --no-trunc
podman exec molecule-naive-proxy ss -tlnp
```

For stress failures also collect the current client invocation and routes:

```bash
podman exec molecule-naive-proxy journalctl \
  -u podman-singbox-naive-molecule-client.service --no-pager -n 300
podman exec molecule-naive-proxy podman exec singbox-naive-molecule-client \
  ip route show table all
podman exec molecule-naive-proxy podman exec singbox-naive-molecule-client \
  cat /proc/net/dev
```

The authoritative test topology and bypass protections are documented in
[`../contracts/verification.md`](../contracts/verification.md).

## Symptom routing

| Symptom | First evidence |
|---|---|
| unit inactive or `219/CGROUP` | unit status, child `cgroup.procs`, handler output; do not add blind sleeps |
| HAProxy fails at startup | rendered `/opt/naive-proxy/haproxy.cfg`, `haproxy -c`, image banner, AppArmor/audit logs |
| decoy works but authenticated proxy fails | HAProxy auth/stage log, `be_naive`, backend journal, connection-reuse directives |
| certificate never replaces bootstrap | `naive-acme-renew.service`, acme.sh output, SNI/ALPN route, Pebble/CA reachability |
| AnyTLS certificate/session fails | AnyTLS unit journal, `anytls.json`, SNI route, ALPN probes, ACME data directory |
| stress throughput high but tunnel suspect | TUN byte assertion and `/32 route_address`; bridge throughput alone proves nothing |
| H2 `PROTOCOL_ERROR` / `bad_hdr` on real network | enable diagnostics and use the production scripts below |
| nested tcpdump/tshark loader denial | retain the test-only copied entrypoints under `/usr/local/bin`; Debian path-based AppArmor is the cause |

## HAProxy diagnostics surface

Set and re-apply:

```yaml
naive_proxy_haproxy_diagnostics_enabled: true
naive_proxy_haproxy_diagnostics_port: 19999
naive_proxy_haproxy_diagnostics_ring_size: 134217728
```

The role then renders `ring h2trace`, exposes the admin socket only on host
loopback, and recreates the pod to publish that port. Verify it before a
capture:

```bash
printf 'show info\nquit\n' | nc 127.0.0.1 19999
printf 'show events h2trace\nquit\n' | nc 127.0.0.1 19999
```

Implementation: [haproxy.cfg.j2](../../templates/haproxy.cfg.j2) and
[pod.service.j2](../../templates/pod.service.j2). Molecule evidence:
[verify-diagnostics.yml](../../molecule/shared/tasks/verify-diagnostics.yml).

## Production H2 capture

The scripts under [`../scripts/`](../scripts/README.md) capture host and pod
network metadata, journal output, HAProxy H2 trace events, and build a
structured report. They are not deployed by the role.

Coordinate with the person generating the external load:

1. Enable the diagnostics variables and re-apply the role.
2. Copy the target-side scripts to `/tmp` on the server.
3. Run `h2trace-start.sh` after every HAProxy restart.
4. Arm `start-capture.sh` only after the load operator agrees on the window.
5. Trigger the client load.
6. Run `stop-capture-dump-h2.sh`, then `analyze.sh`.
7. Retrieve artifacts and scrub secrets before sharing.

`verbosity complete` includes plaintext HTTP/2 payload and headers after TLS
termination, including credentials/cookies. Prefer `advanced`; if complete
traces are necessary, use disposable credentials and rotate them afterwards.

## CI failure collection

On a disposable GitHub Actions runner, collect state after failure rather than
rerunning a bare downloaded client on the runner. A minimal first pass is:

```bash
set +e
cat /etc/os-release
podman --version
ansible --version
make -C roles/naive_proxy/molecule env-info || true
systemctl --failed --no-pager
podman pod ls --no-trunc
podman ps -a --no-trunc
ss -tlnp

for unit in \
  podman-naive-pod.service \
  podman-naive-haproxy.service \
  podman-naive-decoy.service \
  podman-naive-backend.service \
  podman-naive-anytls.service \
  podman-naive-pebble.service \
  naive-acme-renew.service; do
  journalctl -u "$unit" --no-pager -n 300 || true
done
```

Attach the complete Molecule log and targeted journals as artifacts. Host-level
security relaxation (AppArmor sysctls, stopping AppArmor) is an experiment for
a disposable runner only; capture the before/after values and never make it the
default workflow.

The full, copyable packet-capture, runner-security, and post-failure snippets
are in [CI debugging](ci-debugging.md). The supported client reproduction is
the containerised official client in `default`/`bookworm` or the TUN stress
scenarios; do not revive the obsolete bare-host client workflow.

## Safety and cleanup

- Do not run `gha-native` locally.
- Do not run downloaded test binaries on the development host.
- Packet captures and complete H2 traces may contain sensitive metadata.
- Stop capture units/processes and remove temporary credentials after use.
- Changes made to a copied `/tmp/naive-*.sh` MUST be mirrored back to
  [`../scripts/`](../scripts/) before the investigation ends.
