# Verification contract

## Built-in post-deploy checks

Unless Ansible is in check mode, every enabled role run MUST:

1. wait for the pod, Caddy, HAProxy, Naive backend, renewal timer, optional
   AnyTLS service, and Molecule-only Pebble service to be active;
2. request the decoy through HAProxy on loopback and require HTTP 200.

The probe intentionally skips certificate validation because it can run while
the bootstrap certificate is active. It connects to
`127.0.0.1:naive_proxy_listen_port` without an ambient proxy and sends the
Naive domain as `Host`, appending `naive_proxy_external_port` only when that
advertised port is not 80/443. It proves service/routing readiness, not
certificate trust, authenticated tunnelling, or exact decoy content.

Implementation: [healthchecks.yml](../../tasks/healthchecks.yml).

## Molecule scenarios

| Scenario | Environment | Primary evidence |
|---|---|---|
| `default` | Debian trixie, nested rootless Podman | full role, Naive TLS/renewal, direct HTTPS proxy, official-Naive SOCKS5, client JSON, decoy modes, diagnostics, benchmark |
| `debian-bookworm` | Debian 12, nested rootless Podman | same full contract on the older supported base |
| `gha` | delegated native runner | CI-only host integration; MUST NOT be run on a developer workstation |
| `singbox-stress` | Debian trixie + TUN | released-SFA sing-box Naive outbound, ssl_router → HAProxy topology, real TUN byte proof, H2 error scan |
| `anytls-stress` | Debian trixie + TUN | real Pebble ACME for AnyTLS, SNI/ALPN, uTLS ClientHello, real AnyTLS traffic and log scan |

Scenario definitions: [molecule](../../molecule/). Shared assertions:
[verify.yml](../../molecule/shared/verify.yml),
[singbox-verify.yml](../../molecule/shared/singbox-verify.yml),
[singbox-anytls-verify.yml](../../molecule/shared/singbox-anytls-verify.yml).

## Behaviour-to-test matrix

| Behaviour | Test evidence |
|---|---|
| services and built-in decoy route | [wait-services.yml](../../molecule/shared/tasks/wait-services.yml), [verify.yml](../../molecule/shared/verify.yml) |
| HAProxy CPU/cache/FIN/rxbuf directives | [verify.yml](../../molecule/shared/verify.yml) |
| built-in decoy content and `Host: domain:external_port` handling | [verify.yml](../../molecule/shared/verify.yml) |
| reverse-proxied decoy mode | reconfiguration block in [verify.yml](../../molecule/shared/verify.yml) |
| generated auto/manual client files, server-map expansion, protocol options, selection mode, DNS detour/final route | [verify-clients.yml](../../molecule/shared/tasks/verify-clients.yml) |
| diagnostics ring/admin socket | [verify-diagnostics.yml](../../molecule/shared/tasks/verify-diagnostics.yml) |
| Naive certificate issuance, timer, forced renewal, live reload | [verify.yml](../../molecule/shared/verify.yml) |
| direct HTTPS proxy and official-Naive SOCKS5 | [verify.yml](../../molecule/shared/verify.yml), [benchmark.yml](../../molecule/shared/tasks/benchmark.yml) |
| Naive padding negotiation (`Variant1`) | [benchmark.yml](../../molecule/shared/tasks/benchmark.yml) |
| Naive H2 transport under released-SFA sing-box/TUN | [singbox-benchmark.yml](../../molecule/shared/tasks/singbox-benchmark.yml) |
| AnyTLS server config, SNI, Pebble cert, ALPN compatibility | [singbox-anytls-verify.yml](../../molecule/shared/singbox-anytls-verify.yml) |
| AnyTLS uTLS fingerprint and real TUN traffic | [singbox-anytls-benchmark.yml](../../molecule/shared/tasks/singbox-anytls-benchmark.yml) |
| opt-in HAProxy/Caddy runtime image refresh and restarts | [utils.yml](../../molecule/shared/utils.yml), run via the Make runtime-refresh target |
| handler/task idempotence | each scenario's `idempotence` action |
| maintained upstream pins | [version_audit.py](../../molecule/scripts/version_audit.py) via `make versions-check` |

Both TUN stress tests pin a `/32` route to the target and assert the interface
byte delta is at least half of the bytes reported by iperf. Throughput alone is
not accepted as proof because traffic can otherwise bypass the tunnel through
the client container's connected bridge route.

## Known verification gaps

- Preflight, tag aliases, disable/decommission semantics, and production unit
  hardening flags have no dedicated negative/static assertion matrix; they are
  covered by code review, rendered units during converge, and lint.
- Client verification checks file count/content and the shared DNS/final proxy
  selection, but not controller file modes or every TUN leak-prevention rule
  (IPv6, DoT, UDP/443, and remaining UDP). Those remain implementation/review
  evidence until structural assertions are added.
- HAProxy tuning verification proves selected directives render, not the
  performance or memory effect of alternate values on a real network.
- The current role does not prune stale controller profiles or disabled-AnyTLS
  artifacts, so tests assert current outputs but do not treat unrelated old
  files as failures.
- Public DNS equality and public CA reachability cannot be proven in the local
  sandbox.
- Production AnyTLS uses a public ACME CA. Molecule proves the same protocol
  with Pebble and separately runs `sing-box check` on a production-shaped
  provider configuration.
- Real-internet latency/backpressure is outside loopback Molecule coverage.
  Use [the production diagnostic runbook](../maintenance/debugging.md) and
  [scripts](../scripts/README.md) for H2 regressions that need a real network.

## Test execution rules

All local/agent Molecule and role-toolchain actions MUST be invoked through
[molecule/Makefile](../../molecule/Makefile). The disposable repository CI
workflow invokes the selected scenario directly. Commands, log-handling
policy, host-isolation rules, and the impact matrix are in
[Testing](../maintenance/testing.md).
