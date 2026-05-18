# naive_proxy — Agent Context

## Rules for AI agents running Molecule

1. **Use the Makefile wrapper at `molecule/Makefile`, not bare `molecule` commands.** It hides the `GIT_DIR=/dev/null` workaround molecule needs in this collection layout. Target schema is `make <scenario>-<driver>-<action>`; run `make help` for the full list.
2. **Never pipe molecule output through `tail`.** Always redirect full output to a temporary file, then inspect it:
   ```bash
   cd molecule && make default-podman-converge > /tmp/mol-converge.log 2>&1; echo "exit=$?"
   grep -E "fatal:|FAILED" /tmp/mol-converge.log
   ```
3. **Never use the `test` action during development.** It destroys the instance at the end, making debugging impossible. Always run `converge` and `verify` as separate actions. Use the `destroy` action explicitly only when you need a clean slate.
4. **When a failure occurs**, first check the full log file (`grep`, `tail`, targeted reads). If the log does not contain enough information, exec into the running Molecule instance to collect data (podman scenarios):
   ```bash
   podman exec molecule-naive-proxy journalctl -u <unit> --no-pager -n 100
   podman exec molecule-naive-proxy podman logs <container>
   podman exec molecule-naive-proxy ss -tlnp
   ```
5. **Keep the Molecule instance alive** between iterations. Re-run the `converge` and `verify` actions without destroying. Only destroy when the instance state is suspect or you need to test from scratch.
6. **Always activate the venv** before any make/molecule/ansible command: `source /media/data/app/python/venv3/bin/activate`

## What this role does

Deploys a [NaiveProxy](https://github.com/klzgrad/naiveproxy) censorship-resistant VPN service using Podman containers managed by systemd. The role follows the [official HAProxy setup](https://github.com/klzgrad/naiveproxy/wiki/HAProxy-Setup) and includes Molecule coverage for ACME issuance, SOCKS5 tunneling, and throughput benchmarking.

## Architecture

```text
Internet :443
    |
    v
+--- Pod: naive-pod ------------------------------------------------+
|                                                                    |
|  HAProxy TCP frontend (:443)                                       |
|    |-- ALPN acme-tls/1 --> acme.sh (:10443, oneshot via timer)    |
|    +-- default ----------> HAProxy HTTPS frontend (:8444)          |
|                             TLS termination                        |
|                             |                                      |
|                             |-- auth OK --> naive backend (:8080)  |
|                             |              (standalone binary,     |
|                             |               plain HTTP listen)     |
|                             |                                      |
|                             +-- no auth --> Caddy decoy (:8081)    |
|                                            (static website)        |
+--------------------------------------------------------------------+
```

### Traffic routing

HAProxy routes by **HTTP authentication** (`http_auth` + `Proxy-Authorization` header), not by HTTP method.

- Authenticated requests go to the naive backend, which handles CONNECT tunnels and padding.
- Unauthenticated requests go to the Caddy decoy site.
- Users live only in HAProxy `userlist`; the naive backend runs without auth.
- HAProxy strips `proxy-authorization` only in `backend be_naive`, after routing has already used `http_auth`.

### Decoy modes

Caddy decoy has two mutually exclusive modes selected at template time:

- **Local stub (default)** — `file_server` rooted at `/srv`. Content comes from `naive_proxy_decoy_index_html` or the bundled placeholder.
- **Reverse-proxy to a remote site** — enabled by setting `naive_proxy_decoy_upstream_url`. Caddy terminates upstream TLS itself and rewrites the request `Host` to the upstream hostname (`{upstream_hostport}`). Response bodies and `Location` headers are not rewritten — picking a static-style upstream avoids URL leaks. Future option: build a custom Caddy image with `caddy-replace-response` for body rewriting (tracked as TODO in README).

### Backend hop detail

The public client side is HTTP/2 over TLS on the HAProxy HTTPS frontend. The internal HAProxy -> naive backend hop is plain HTTP to `127.0.0.1:8080` and does **not** use `proto h2`.

### Backend connection reuse (critical)

The naive backend expects **one proxy request per accepted TCP socket**. After the first request completes the handshake (`completed_handshake_ = true`), the backend does not accept further proxy transactions on the same connection. If HAProxy reuses an idle backend connection for a second CONNECT request, the backend falls through to a raw HTTP proxy path instead of establishing a tunnel, resulting in `padding type: None` and `ERR_TUNNEL_CONNECTION_FAILED`.

To prevent this, `backend be_naive` includes:

```haproxy
option http-server-close
http-reuse never
```

- `option http-server-close` — closes the backend TCP connection after each response
- `http-reuse never` — prevents HAProxy from reusing idle backend connections

This was discovered during GHA CI debugging. Without these directives, the SOCKS5 tunnel works on Debian 12 (where timing/connection patterns differ) but fails on Ubuntu 24.04 where HAProxy reuses backend connections more aggressively. See `bug.md` for the full investigation.

### QUIC

HAProxy 3.2 alpine image is built with `USE_QUIC=1`. The `quic_test_socketopts()` call at startup fails with `Permission denied` in some container environments (rootful podman on Ubuntu 24.04). The role adds `no-quic` in the HAProxy global section since QUIC is not used.

### Container security options

All container systemd units include `--security-opt=apparmor=unconfined`. This is required on Ubuntu 24.04 where the default `containers-default` AppArmor profile blocks socket operations inside rootful podman containers.

### Containers

| Container | Image | Lifecycle | Port | Purpose |
|-----------|-------|-----------|------|---------|
| `naive-haproxy` | `haproxy:3.3.10-alpine` | long-running | :443 TCP, :8444 HTTPS | TLS termination, auth routing, speed tuning |
| `naive-backend` | `localhost/naive-backend:VERSION` (configurable base image) | long-running | :8080 HTTP | Standalone `naive` proxy backend, no auth |
| `naive-decoy` | `caddy:latest` | long-running | :8081 HTTP | Decoy website |
| `naive-acme` | `neilpang/acme.sh:latest` | oneshot (timer) | :10443 during renewal | TLS-ALPN-01 certificate management |
| `naive-pebble` | `ghcr.io/letsencrypt/pebble:latest` | long-running | :14000, :15000 | `molecule_mode` only test ACME CA |

### Certificate flow

```text
First deploy:
  bootstrap self-signed cert -> HAProxy starts -> acme.sh systemd unit issues real cert -> restart HAProxy

Timer renewal:
  naive-acme-renew.timer -> naive-acme-renew.service -> acme.sh --issue -> restart HAProxy

Molecule:
  pebble (local CA, real TLS-ALPN-01 validation via HAProxy) -> acme.sh -> restart HAProxy
```

HAProxy uses separate files via `ssl-f-use`:

```text
ssl-f-use crt /certs/fullchain.pem key /certs/key.pem
```

No combined PEM is needed.

## SOCKS5 tunnel status

The official naive client in SOCKS5 mode works through HAProxy.

- Padding negotiation reaches the client (`Variant1`)
- SOCKS5 test client runs inside a Debian/Ubuntu container with `--network host`
- `molecule verify` passes the SOCKS5 tunnel test and iperf3 benchmark

## Role structure

```text
roles/naive_proxy/
├── AGENTS.md
├── bug.md
├── defaults/main.yml
├── vars/main.yml
├── tasks/
│   ├── main.yml
│   ├── preflight.yml
│   ├── install.yml
│   ├── image.yml
│   ├── utils.yml
│   ├── config.yml
│   ├── services.yml
│   ├── acme.yml
│   ├── clients.yml
│   └── healthchecks.yml
├── handlers/main.yml
├── templates/
│   ├── pod.service.j2
│   ├── haproxy.service.j2
│   ├── haproxy.cfg.j2
│   ├── backend.service.j2
│   ├── decoy.service.j2
│   ├── Caddyfile.j2
│   ├── Containerfile.j2
│   ├── acme-renew.service.j2
│   ├── acme-renew.timer.j2
│   ├── pebble.service.j2
│   ├── pebble-config.json.j2
│   └── singbox-client.json.j2
├── files/index.html
├── debug/                    # Operator-side toolkit for prod H2 diagnostics (NOT applied by the role)
│   ├── README.md             # Workflow, prerequisites, parametrisation, output interpretation
│   ├── start-capture.sh      # tcpdump (host + pod-netns) + journal-follow + ss/nstat sampler
│   ├── h2trace-start.sh      # enable HAProxy H2 trace into a custom 32 MiB ring sink
│   ├── stop-capture-dump-h2.sh  # stop watchers + dump trace events into the capture dir
│   ├── analyze.sh            # structured report (counters, time histograms, TCP zero-window, term-states)
│   ├── upload-via-tty.sh     # operator-side: base64-stream a local file to a TCP-bridged TTY
│   └── download-via-tty.sh   # operator-side: pull a file off the target through the same TTY
└── molecule/
    ├── Makefile           # Thin wrapper: <scenario>-<driver>-<action>, hides MP_DRIVER/GIT_DIR/ANSIBLE_LIBRARY
    │
    │ # Cross-scenario base config: `molecule/shared/base.yml`.
    │ # Loaded explicitly via `molecule -c molecule/shared/base.yml ...`
    │ # (Makefile + CI workflow inject the flag) and deep-merged below
    │ # each scenario's molecule.yml. Holds dependency, verifier,
    │ # provisioner.{name, options, inventory.host_vars defaults},
    │ # ansible.cfg.defaults, and ansible.playbooks (default paths to
    │ # `../shared/{prepare,converge,verify}.yml`). Lists are NOT
    │ # deep-merged by molecule, so `driver` and `platforms` MUST stay
    │ # in each scenario's molecule.yml.
    │
    ├── default/           # Local podman-in-podman scenario, Debian trixie
    │   ├── molecule.yml
    │   ├── Dockerfile.j2
    │   └── ENABLE_CI      # Marker: include in CI matrix
    ├── debian-bookworm/   # Local podman-in-podman scenario (Debian bookworm)
    │   ├── molecule.yml
    │   ├── Dockerfile.j2
    │   └── ENABLE_CI
    ├── gha/               # GitHub Actions localhost scenario (ansible-native)
    │   ├── molecule.yml
    │   ├── inventory/hosts.yml   # sets mp_driver: native so the shared prepare task fires its lineinfile branch
    │   └── ENABLE_CI
    ├── singbox-stress/    # Opt-in: sing-box TUN-mode H2 reproducer (no ENABLE_CI)
    │   ├── molecule.yml   # like default + binds /dev/net/tun and swaps verify playbook
    │   └── converge.yml   # scenario-local: discover bridge gateway, override external_ip, apply naive_proxy + ssl_router
    └── shared/            # Common playbooks and tasks for all scenarios
        ├── base.yml                # shared molecule base config (loaded via -c)
        ├── prepare.yml             # single prepare entry-point used by every scenario
        ├── converge.yml            # default converge (used by default / debian-bookworm / gha)
        ├── verify.yml              # full verify (cert renewal, decoy modes, official-naive SOCKS5, benchmark)
        ├── singbox-verify.yml      # verify entry-point for singbox-stress
        ├── benchmark.yml           # standalone official-naive benchmark
        ├── singbox-benchmark.yml   # standalone sing-box stress benchmark
        ├── utils.yml
        ├── tasks/
        │   ├── prepare.yml               # etc_hosts + deps + naive client download
        │   ├── converge-naive-proxy.yml  # shared: include_role naive_proxy with the molecule role-vars dict
        │   ├── wait-services.yml
        │   ├── benchmark.yml             # official-naive client + shared bench tasks
        │   ├── singbox-benchmark.yml     # sing-box client + shared bench tasks
        │   ├── socks-decoy-smoke.yml     # shared: curl decoy via SOCKS5 + assert
        │   ├── iperf-server.yml          # shared: iperf3 server unit in naive-pod
        │   ├── iperf-bench.yml           # shared: proxychains + iperf3 + CPU + assert
        │   ├── verify-diagnostics.yml    # shared: HAProxy admin socket + ring h2trace
        │   └── verify-clients.yml        # shared: per-user×IP sing-box config assertions
        └── vars/
            ├── common.yml     # Shared variables (domain, ports, naive version, role-vars dict, test users)
            ├── benchmark.yml
            └── singbox-benchmark.yml
```

The two benchmarks (`tasks/benchmark.yml`, `tasks/singbox-benchmark.yml`) own only client-specific bits (which binary, which systemd unit, which journal markers to scan). All shared transport-level steps — the SOCKS5 smoke test, the iperf3 server unit inside `naive-pod`, the proxychains4 + iperf3 client run with CPU counters and throughput assertion — live in `tasks/socks-decoy-smoke.yml`, `tasks/iperf-server.yml`, and `tasks/iperf-bench.yml`. `iperf-bench.yml` is parameterized through `_iperf_bench_*` vars (socks host/port, parallel streams, duration, label, min Mbps); `socks-decoy-smoke.yml` through `_socks_smoke_*` vars.

## Tags

| Tag | Scope |
|-----|-------|
| `naive-proxy` | All tasks |
| `naive-proxy-preflight` | Validation only |
| `naive-proxy-install` | Packages and directories |
| `naive-proxy-image` | Backend image build |
| `naive-proxy-utils` | Optional runtime image refresh for long-running services |
| `naive-proxy-config` | Configs, certs, systemd units |
| `naive-proxy-services` | Start and enable services |
| `naive-proxy-acme` | ACME issuance and timer |
| `naive-proxy-clients` | Client config generation |
| `naive-proxy-healthchecks` | Post-deploy runtime checks through systemd and HAProxy |

## Key variables

### Required

- `naive_proxy_domain` — server FQDN
- `naive_proxy_external_ip` — **map** of `<suffix>: <ip>`, at least one entry. Each entry is a public IP that `naive_proxy_domain` resolves to. The role renders **one sing-box client config per user** at `singbox-<host>-<user>.json`; inside the file every entry becomes a separate naive outbound (`naive-<user>-<suffix>`) with its own `server` IP, and a `urltest` selector (`naive-<user>`) on top probes them every `naive_proxy_singbox_urltest_interval` and routes traffic through the lowest-latency one. DNS detour and route default both reference the urltest tag, so failover between IPs is automatic. SNI stays the FQDN via `tls.server_name` so sing-box / cronet skips bootstrap DNS for the proxy itself. DNS through the tunnel still flows via `dns-remote-cloudflare` (DoH detoured through the urltest tag). Use multiple entries to give clients automatic IPv4 / IPv6 or primary / fallback failover. Probe URL is `naive_proxy_singbox_urltest_url`, default `https://www.gstatic.com/generate_204`
- `naive_proxy_users` — dict `{ name: password }`, at least one user

### Important

- `naive_proxy_listen_port` — default `443`
- `naive_proxy_external_port` — public port advertised to clients
- `naive_proxy_naive_version` — release tag, for example `v148.0.7778.96-5`
- `naive_proxy_padding` — default `true`; enables `--padding` on the backend
- `naive_proxy_backend_base_image` — default `docker.io/library/ubuntu`; base image for backend container build
- `naive_proxy_backend_base_image_tag` — default `22.04`
- `naive_proxy_acme_server` — default `letsencrypt`; explicit ACME CA passed to `acme.sh` outside `molecule_mode`
- `naive_proxy_molecule_mode` — default `false`; enables Pebble and verbose HAProxy stage logging in Molecule
- `naive_proxy_update_runtime_images` — default `false`; force-pulls fresh runtime images for HAProxy and decoy, then queues restarts only when the pulled image ID actually changed

The utils refresh path is intentionally limited to long-running runtime services:

- HAProxy image refresh can queue `podman-naive-haproxy.service`
- decoy image refresh can queue `podman-naive-decoy.service`
- ACME and molecule-only Pebble are excluded
- the backend image is excluded because the role builds it locally

### HAProxy tuning defaults

The role defaults to a speed-first profile for a dedicated VPN edge.

The H2 demuxer bug we used to work around — haproxy/haproxy#3354 (PADDED-DATA padding never drained from `dbuf`, leading to `received invalid H2 frame header : dft=DATA/00 dfl=0` → `PROTOCOL_ERROR/01` GOAWAY) — is fixed upstream by `faf3e9a` ("BUG/MEDIUM: mux-h2: Properly consume padding for DATA frames"), backported to the 3.3 maintenance branch as `043db34` and shipped in `v3.3.10` (Docker Hub image `library/haproxy:3.3.10-alpine` rebuilt 2026-05-13). The role pins this exact minor; the H2 knobs below are now throughput tunings, not mitigations.

- `naive_proxy_haproxy_image_tag: "3.3.10-alpine"` — pinned to the explicit minor (not the rolling `3.3-alpine`) because the official Docker Hub image carries no `org.opencontainers.image.version` label, so an exact tag is the only way to guarantee a known-good build. `v3.3.10` is the first 3.3.x release with the #3354 fix; do NOT drop the pin below it (3.3.9 and earlier, plus the entire 3.2 / 3.0 / 2.8 lines, still carry the bug). Bumping UPWARDS to a later 3.3.x as releases ship is the expected maintenance path — verify `haproxy -v` reports a build dated after 2026-05-07 before bumping.
- `naive_proxy_haproxy_cpu_policy: "performance"`
- `naive_proxy_haproxy_ssl_cache_size: 40000`
- `naive_proxy_haproxy_h2_frontend_rxbuf: "6m"` — sets `tune.h2.fe.rxbuf <size>` in HAProxy `global`. Units: HAProxy size syntax (bytes default, `k`/`m`/`g` for KiB/MiB/GiB, base 1024 — e.g. `1638400`, `1600k`, `12m`). Empty → directive omitted, HAProxy uses its built-in default `1600k` (1638400 bytes ≈ 1.6 MiB, ~130 Mbps × 100 ms RTT). Sized to a real BDP target — rough rule `BDP_bytes ≈ bandwidth_mbps × rtt_ms × 125`. RAM cost is per-stream × concurrent H2 streams.
- `naive_proxy_haproxy_h2_initial_window_size: 1048576` — sets `tune.h2.fe.initial-window-size` (1 MiB). Cuts WINDOW_UPDATE round-trips on long-lived bidirectional streams; sized to match `h2_frontend_rxbuf`. Set to `0` to keep RFC default of 65535. Requires HAProxy 3.0+.
- `naive_proxy_haproxy_h2_max_frame_size: 0` — directive omitted, HAProxy keeps its default of 16 KiB. Previously defaulted to `1048576` (1 MiB) as a secondary mitigation for #3354 (fewer frames per byte → fewer PADDED+END_STREAM markers → lower trigger rate); justification gone now that the upstream fix exists. Re-raise only with a benchmark proving a real parsing-overhead win on your workload — values up to RFC max 16777215 are accepted but cause head-of-line blocking on multiplexed connections. Requires HAProxy 3.0+.
- `naive_proxy_haproxy_notsent_lowat: 0` — optional, disabled by default

### HAProxy diagnostics (opt-in)

Used to enable the `roles/naive_proxy/debug/` toolkit against a production deployment. Off by default.

- `naive_proxy_haproxy_diagnostics_enabled: false` — when `true`, adds `ring h2trace { format timed; size 32 MiB }` and `stats socket ipv4@*:<port> level admin` to `haproxy.cfg`, and adds `--publish 127.0.0.1:<port>:<port>` to the pod so the admin socket is reachable from the host's loopback (and only there). Toggling forces the pod to be recreated, not just the haproxy container.
- `naive_proxy_haproxy_diagnostics_port: 19999` — TCP port on `127.0.0.1` of the host.
- `naive_proxy_haproxy_diagnostics_ring_size: 134217728` — trace ring sink size in bytes (128 MiB default; sized for `verbosity complete` runs that dump full frame hex). 32 MiB suffices at `verbosity advanced`.

The `no-quic` global directive is rendered conditionally based on `naive_proxy_haproxy_image_tag` — emitted on `2.x` and `3.0`/`3.1`/`3.2` builds (defensive against `quic_test_socketopts()` startup crashes on Ubuntu 24.04 + rootful podman), omitted on `3.3+` where the directive was removed and QUIC is opt-in via listener.

### HAProxy timeout defaults

Tuned for VPN/proxy workloads:

- `naive_proxy_haproxy_timeout_connect: "5s"` — backend on localhost
- `naive_proxy_haproxy_timeout_client: "60s"` — H2 persistent connection idle gap
- `naive_proxy_haproxy_timeout_server: "60s"` — backend response wait
- `naive_proxy_haproxy_timeout_tunnel: "3600s"` — idle VPN tunnel (CONNECT); 1 hour allows SSH, long-polling, idle tabs
- `naive_proxy_haproxy_timeout_client_fin: "30s"` — graceful close
- `naive_proxy_haproxy_timeout_server_fin: "30s"` — graceful close

`timeout tunnel` replaces `timeout client`/`timeout server` after a CONNECT tunnel is established.

### Internal

All internals are `_naive_proxy_*`.

- `_naive_proxy_haproxy_https_port: 8444` — internal HTTPS frontend
- `_naive_proxy_backend_port: 8080` — naive backend
- `_naive_proxy_decoy_port: 8081` — Caddy decoy
- `_naive_proxy_acme_alpn_port: 10443` — ACME TLS-ALPN responder
- `_naive_proxy_pebble_port: 14000` — Pebble ACME directory

## Handler cascade

```text
restart naive-pod
  -> notifies: restart naive-haproxy, restart naive-decoy, restart naive-backend

restart naive-haproxy    triggered by haproxy.cfg or haproxy.service.j2
restart naive-decoy      triggered by Caddyfile, index.html, or decoy.service.j2
restart naive-backend    triggered by image rebuild or backend.service.j2
```

## Systemd unit dependency graph

```text
podman-naive-pod.service (oneshot, RemainAfterExit)
├── podman-naive-decoy.service    (Requires=pod, After=pod, Before=haproxy)
├── podman-naive-haproxy.service  (Requires=pod, After=pod)
├── podman-naive-backend.service  (Requires=pod, Wants=haproxy+decoy, After=all)
├── podman-naive-pebble.service   (molecule_mode only)
└── naive-acme-renew.service      (Requires=pod, After=pod+haproxy)
      ^
naive-acme-renew.timer (daily, RandomizedDelaySec=3600)
```

## Molecule testing

### Scenarios

| Scenario | Driver | Make target prefix | Purpose |
|----------|--------|--------------------|---------|
| `default` | podman (container) | `default-podman-` | Local dev, podman-in-podman, Debian trixie |
| `debian-bookworm` | podman | `bookworm-podman-` | Local dev, podman-in-podman, Debian 12 |
| `gha` | ansible-native (delegated) | `gha-native-` | GitHub Actions, role applied to runner VM |
| `singbox-stress` | podman | `singbox-stress-podman-` | Opt-in sing-box Naive H2 reproducer (no `ENABLE_CI` marker) |

All scenarios share playbooks and tasks from `molecule/shared/`. Each has its own `molecule.yml`. Shared variables live in `molecule/shared/vars/common.yml`.

A scenario is included in the CI matrix only if its directory contains an `ENABLE_CI` marker file.

### Commands

Actions: `test create converge verify idempotence destroy login reset prepare check`. All run through `molecule/Makefile`; activate the venv first.

```bash
cd naive_proxy/molecule
make help

# default scenario on podman
make default-podman-converge
make default-podman-verify
make default-podman-login

# debian-bookworm (podman-only)
make bookworm-podman-test

# gha (localhost, no containers)
make gha-native-test

# sing-box stress reproducer (opt-in; same converge as default)
make singbox-stress-podman-converge
make singbox-stress-podman-verify
```

During iterative work, do not destroy the instance between changes. Re-run `make ...-converge` and `make ...-verify` against the same instance. Use `make ...-test` only at the end of a session.

### Driver conditionals and env-var plumbing

The Makefile injects `GIT_DIR=/dev/null` so molecule does not misidentify the role as a collection (`collections/` is gitignored). `MP_NETWORK` (default `slirp4netns`) selects the rootless podman network mode. Both are surfaced as env vars in scenario `molecule.yml` files via `${VAR:-default}` shell substitution.

Inside the playbooks, the single source of truth for driver-conditional behaviour is the `mp_driver` host_var. The shared base config (`molecule/shared/base.yml`, loaded by Makefile + CI via `molecule -c molecule/shared/base.yml ...`) sets `mp_driver: podman` as the default for every scenario; the gha scenario overrides it to `native` in `gha/inventory/hosts.yml`:

```yaml
# molecule/shared/base.yml — shared default for podman scenarios
provisioner:
  inventory:
    host_vars:
      molecule-naive-proxy:
        mp_driver: podman

# molecule/gha/inventory/hosts.yml — runs ansible-native on the runner
all:
  hosts:
    localhost:
      mp_driver: native
```

- `mp_driver` is intentionally NOT defined in `shared/vars/common.yml`. Ansible's `vars_files` precedence (14) is higher than inventory host_vars (9), so a default in `vars/common.yml` would silently mask the gha scenario's `native` value and the lineinfile branch would never fire.
- Tasks that must branch on driver use `when: mp_driver != 'podman'` (see the `/etc/hosts` patch in `shared/tasks/prepare.yml`, needed for the gha scenario because podman's own `etc_hosts` mechanism handles the container case and `/etc/hosts` there is a bind-mount that `lineinfile` cannot atomic-replace).

### What `molecule verify` checks

1. Pod, HAProxy, decoy, and backend services are active
2. **Per-user sing-box client configs (`tasks/verify-clients.yml`)** — one config per user at `singbox-<host>-<user>.json`. The file must contain one naive outbound per `naive_proxy_external_ip` entry (`naive-<user>-<suffix>` with the matching `server` IP) plus one `urltest` outbound (`naive-<user>`) listing every per-suffix tag and referencing the configured probe URL + interval. DNS detour and route default both target the urltest tag. Reused by both `shared/verify.yml` and `shared/singbox-verify.yml`. Singbox-stress goes further and asserts urltest events actually fire in the sing-box journal during the iperf3 run (probe URL pointed at the molecule decoy so probes are reachable inside the sandbox)
3. Decoy site is served through HAProxy TLS
4. Pebble-issued certificate replaces the bootstrap self-signed cert
5. `naive-acme-renew.timer` is enabled
6. Forced renewal changes the certificate serial and HAProxy serves the new cert
7. Direct HTTPS proxy mode works: `curl -x`
8. naive SOCKS5 mode works through HAProxy and receives padding
9. The benchmark task moves real traffic through the SOCKS5 tunnel with `iperf3`

### SOCKS5 test client

The naive SOCKS5 client runs inside a container (Debian/Ubuntu based, `--network host`) rather than as a bare host binary. This is required because the naive binary (Chromium networking stack) fails on some host environments (Ubuntu 24.04 / kernel 6.17) while working correctly inside containers. The client container image and tag are configurable via `naive_client_image` and `naive_client_image_tag` in `molecule/shared/vars/benchmark.yml`.

### Sing-box stress reproducer (`singbox-stress`)

A separate Molecule scenario uses a **scenario-local `converge.yml`** (not `shared/converge.yml`) that applies `kogeler.mini_pig.naive_proxy` followed by `kogeler.mini_pig.ssl_router` — mirroring the production topology where `ssl_router` (nginx with `ssl_preread`) sits on `:443` in front of HAProxy and SNI-routes incoming traffic to the HAProxy frontend. Verify is `shared/singbox-verify.yml` which imports `shared/tasks/singbox-benchmark.yml`. Used to reproduce the HTTP/2 protocol errors reported by real sing-box / SFA users:

- A Linux sing-box client runs in its **own podman netns** (no `--network=host`) with `--cap-drop=ALL --cap-add=NET_ADMIN --security-opt=no-new-privileges --device=/dev/net/tun`. The molecule instance binds `/dev/net/tun` into itself (`devices:` in `molecule/singbox-stress/molecule.yml`) so the inner container can obtain a tun fd. `NET_ADMIN` is scoped to the sing-box container's network namespace — tun device creation, `ip rule`/`ip route` programming, and the iptables/nft rules sing-box adds for `auto_route`+`strict_route` all stay inside that netns. The molecule host's netns is untouched.
- The client config is shaped after `templates/singbox-client.json.j2` (`tun` inbound + `urltest` outbound + full `route.rules`) so the test exercises the same code paths Android sing-box / SFA users hit. Molecule-specific deviations:
  - The naive outbound's `server` is the inner podman default-bridge gateway IP (discovered at runtime via `containers.podman.podman_network_info` and threaded in through `naive_proxy_external_ip`), not the public IP it would be in prod. Loopback addresses would point at the sing-box container's own loopback (the container is in its own netns), so a routable bridge IP is the only thing the sing-box can use to reach ssl-router (which runs `--network=host` on the molecule instance and therefore listens on every host interface including the bridge gateway).
  - The `molecule_naive_proxy_external_ip_map` is collapsed to a single entry in this scenario (multi-IP urltest coverage stays in `default` / `debian-bookworm`). The override is applied via `set_fact` in both `singbox-stress/converge.yml` (so the role generates configs against the right IP) and `shared/singbox-verify.yml` (so `verify-clients.yml` asserts against the same map after the separate verify playbook run drops the converge-time fact).
  - `dns` block dropped entirely. The prod template detours `dns-remote-cloudflare` (DoH to 1.1.1.1) through the naive tunnel; the molecule sandbox has no internet and the iperf3 destination is an IP literal (the naive-pod's bridge IP), so no DNS lookups go through sing-box at all. The `hijack-dns` route rule is dropped with it.
  - `tls.certificate_path` added for the Pebble test CA so cronet trusts the molecule chain.
  - `log.level=debug` (prod uses `info`) so H2 stream errors surface in the journal.
- The sing-box client reaches ssl-router on `<bridge_gateway>:443` (i.e. the molecule host's interface on the inner bridge). ssl-router does pure TCP/SNI forwarding to `127.0.0.1:{{ molecule_naive_proxy_listen_port }}` (HAProxy on `:8443`) inside the molecule host netns, so the TLS handshake is end-to-end between cronet and HAProxy — exactly like in prod.
- iperf3 client + curl smoke-test client are one-shot containers launched with `--network=container:{{ singbox_client_container_name }}` so they share the sing-box client's netns. Their sockets enter the tun device first and reach the iperf3 server / decoy only through the full sing-box → naive → ssl-router → HAProxy → backend chain. No proxychains4 in this scenario; the tun device IS the transport hijack. Target IP for both is the naive-pod's bridge IP (discovered via `containers.podman.podman_container_info: name=naive-backend`, since pod containers share the pod's netns and that container always exists post-converge); the naive backend resolves the same IP back to itself via lo in the pod's netns and reaches the iperf3 server (which listens on `0.0.0.0:5201`).
- `iperf3 -P {{ iperf_parallel }}` (shared between both benchmarks via `shared/vars/benchmark.yml`, default 16) drives parallel CONNECT streams to surface H2 multiplexing failures and keeps throughput numbers comparable across the official-naive control and the sing-box reproducer.
- The task fails when `stream failed: http2 protocol error`, `connection upload closed: http2 protocol error`, `connection download closed: http2 protocol error`, `unexpected EOF`, `ERR_PROXY`, or `ERR_TUNNEL` appears in the sing-box journal, or when iperf3 reports per-stream errors / sub-1Mbps throughput.
- The sing-box binary is built into the molecule instance image by `molecule/singbox-stress/Dockerfile.j2` (`go install ... -tags=...,with_naive_outbound github.com/sagernet/sing-box/cmd/sing-box@${SINGBOX_VERSION}`). Build-time pins (`singbox_build_version`, `singbox_build_tags`, `singbox_build_go_version`) live in `molecule/singbox-stress/molecule.yml` under `provisioner.inventory.group_vars.all` so molecule passes them to both the create play (renders `Dockerfile.j2`) and the converge / verify plays. Defaults track [SFA](https://github.com/SagerNet/sing-box-for-android)'s `version.properties` (`VERSION_NAME` + `GO_VERSION`); bumping SFA means bumping those vars and rebuilding the molecule image (`molecule destroy` + `molecule converge`). `with_naive_outbound` in sing-box ≥ 1.14 pulls in `github.com/sagernet/cronet-go`, whose linux/amd64 backend is gated by `// +build cgo`, so the Dockerfile builds with `CGO_ENABLED=1` and the client container runs the same Debian trixie base as the molecule instance for glibc symbol compatibility.
- The official-naive benchmark in `default` / `debian-bookworm` remains the control test. Do not delete or replace it — both must coexist.

### Built-in post-deploy healthchecks

At the end of `tasks/main.yml` the role runs `tasks/healthchecks.yml` unless Ansible is in check mode.

- It waits with retries for the managed units to report `ActiveState=active`
- It covers pod, decoy, haproxy, backend, and the ACME renewal timer
- In `molecule_mode` it also waits for Pebble
- It then probes the decoy page through the public HAProxy listener on `127.0.0.1:{{ naive_proxy_listen_port }}` with a manually constructed `Host` header based on `naive_proxy_domain` and `naive_proxy_external_port`
- The built-in role healthcheck only requires a successful HTTP 200 response there; exact decoy page content is validated separately in Molecule
- TLS validation is intentionally disabled for this probe so the initial bootstrap certificate does not fail the deploy

### Standalone benchmark workflow

`molecule/shared/benchmark.yml` runs the throughput benchmark without the rest of `verify`.

Expected output includes lines like:

```text
iperf3 -P 1 through official-naive SOCKS5 tunnel: 3039.7 Mbps
naive-haproxy avg_cpu=156.06% cpu_time=55.793s
naive-backend avg_cpu=58.72% cpu_time=20.991s
```

### `molecule_mode`

When `naive_proxy_molecule_mode: true`:

- Pebble is deployed inside the pod
- Pebble uses a custom config with `tlsPort` matching `naive_proxy_listen_port`
- `acme-renew.service` uses `--server`, `--insecure`, and `--force`
- Pebble ports `14000` and `15000` are published
- HAProxy enables verbose stage-wise request and response header logging for debugging

### Host isolation policy

Never run downloaded binaries on the host and do not mutate host state outside the project tree. All validation and benchmarking happen inside the Molecule instance and its nested Podman environment. The SOCKS5 test client runs inside a container, not as a bare host binary.

### Molecule network configuration (default scenario, podman driver)

```yaml
network: "${MP_NETWORK:-slirp4netns}"
```

Supported values: `host`, `slirp4netns` (rootless default), `bridge`, `pasta` (podman >= 5.0). The molecule-podman create playbook reads `network`, not `network_mode` — using the wrong key silently falls back to the default.

The test domain `naive.test` is mapped to `127.0.0.1` via `etc_hosts` in molecule.yml (podman scenarios) or via a `lineinfile` task gated on `when: mp_driver != 'podman'` in `shared/tasks/prepare.yml` (gha scenario). Two mechanisms because `/etc/hosts` inside the podman container is a bind-mount that `lineinfile` cannot atomic-replace.

## Debug toolkit (`debug/`)

Operator-side scripts for diagnosing **production** HAProxy H2 issues that Molecule does not reproduce — specifically the
`received invalid H2 frame header : dft=DATA/00 dfl=0 glitches=1 → PROTOCOL_ERROR/01` GOAWAY storm that real-internet
TCP backpressure can trigger on the H2 demuxer (confirmed on HAProxy 2.8 / 3.0 / 3.2 / 3.3, not a single-version regression).
Loopback-only Molecule tests cannot reproduce the bug because loopback TCP has effectively infinite buffers and zero RTT.

Use this toolkit when an external user reports H2 connection drops on a deployed naive_proxy stack, not for development testing.

- `debug/start-capture.sh` — host + pod-netns tcpdump, journal-follow, ss/nstat sampler into `/tmp/naive-debug-<RUN_ID>/`. All knobs (NIC, container, ports, units, duration) are env vars or `--flag` CLI args.
- `debug/h2trace-start.sh` — turn on HAProxy H2 trace into a custom 32 MiB `ring h2trace` sink (must be declared in `haproxy.cfg` first, the role does not render it). Trace state resets on container restart, re-run after every restart.
- `debug/stop-capture-dump-h2.sh` — terminate the capture watchers and dump `show events <sink>` + `show trace h2` from the HAProxy admin socket (`stats socket ipv4@127.0.0.1:19999 level admin`, also not rendered by the role by default).
- `debug/analyze.sh` — turn one capture dir into a structured text report: counters (BADREQ / ERR_CONNECTION_RESET / `bad_hdr` / `wait_room` / `demux_full`), bug-trigger frame distribution, time-to-first-failure, per-h2c stream-kill counts with `txw=`/`rxw=` at error time, BADREQ + RESET histograms, TCP-level zero-window/retransmits from the host pcap, term-state breakdown per backend. Appends one row per session to `/tmp/naive-history.tsv` (override with `HISTORY_FILE`).
- `debug/upload-via-tty.sh` and `debug/download-via-tty.sh` — operator-side helpers for moving files when the only access is a TCP-bridged interactive shell (`socat - TCP:127.0.0.1:5555,...`). Stream base64 in 900-char chunks (longer single `nc` writes truncate on the wire) and verify SHA-256 on the remote side.

`debug/README.md` has the full workflow, prerequisites, parametrisation table, and gotchas. **Important**: the toolkit also requires manual additions to `/opt/naive-proxy/haproxy.cfg` (the admin socket and the `ring h2trace` declaration) — these are not rendered by the role. Always back up the cfg before patching; the next idempotent role run will overwrite the edits.

When iterating on the toolkit, fixes made directly to the live `/tmp/naive-*.sh` on a target host **must** be mirrored back to `roles/naive_proxy/debug/` in the same turn so the two copies do not drift. Same in reverse.

## Key references

- [NaiveProxy](https://github.com/klzgrad/naiveproxy) — project, releases, protocol docs
- [HAProxy Setup wiki](https://github.com/klzgrad/naiveproxy/wiki/HAProxy-Setup) — official frontend architecture
- [HAProxy 3.2 configuration manual](https://docs.haproxy.org/3.2/configuration.html) — `ssl-f-use`, H2 receive buffer, `cpu-policy`, `tune.notsent-lowat`, TLS cache, `http-reuse`, `option http-server-close`
- [Pebble](https://github.com/letsencrypt/pebble) — test ACME CA
- [acme.sh](https://github.com/acmesh-official/acme.sh) — ACME client
- [bug.md](bug.md) — full investigation of SOCKS5 tunnel failure on Ubuntu 24.04 GHA runner

## Validated technical decisions

| Decision | Validation | Status |
|----------|------------|--------|
| HAProxy forwards authenticated traffic to naive backend | Tested with `curl -x` and naive SOCKS5 client | OK |
| naive backend runs without auth | HAProxy handles `http_auth` and strips `proxy-authorization` before backend forwarding | OK |
| HAProxy strips `proxy-authorization` before forwarding authenticated traffic to the backend | Implemented in the active HAProxy config and covered by Molecule SOCKS5 tests | OK |
| HAProxy frontend H2 with plain HTTP backend hop works for naive | Covered by direct proxy and SOCKS5 Molecule tests | OK |
| Backend connection reuse disabled (`http-reuse never` + `option http-server-close`) | Required because naive backend expects one proxy request per TCP socket. Without this, CONNECT falls through to raw HTTP proxy path. Discovered via GHA CI debugging on Ubuntu 24.04 | OK |
| `no-quic` in HAProxy global config | Prevents `quic_test_socketopts()` crash in rootful podman on Ubuntu 24.04 | OK |
| `apparmor=unconfined` on all containers | Required on Ubuntu 24.04 where `containers-default` AppArmor profile blocks socket ops | OK |
| HAProxy 3.2 `req.ssl_alpn` routing for ACME works | Confirmed with HAProxy build flags and Molecule ACME issuance | OK |
| HAProxy uses `ssl-f-use crt ... key ...` with separate files | Confirmed on HAProxy 3.x | OK |
| Pebble real TLS-ALPN-01 validation in Molecule works | Custom `pebble-config.json` with `tlsPort`; no `ALWAYS_VALID` shortcut | OK |
| ACME cert issuance and renewal via systemd works | `naive-acme-renew.service` re-issues certs and HAProxy serves the new serial | OK |
| naive client SOCKS5 tunnel through HAProxy works | `negotiated padding type: Variant1`, tunnel closes `OK` | OK |
| naive SOCKS5 client runs in container, not on host | Required: bare binary fails on Ubuntu 24.04 / kernel 6.17 (Chromium networking stack issue) | OK |
| Speed-first HAProxy defaults improve client throughput | Molecule benchmark validated `cpu-policy performance` plus auto BDP H2 rxbuf sizing | OK |
| `tune.notsent-lowat` should stay optional | It helped some non-top profiles but regressed the best `1000 Mbps + cpuperf` profile | OK |
| VPN-appropriate timeouts (`timeout tunnel 3600s`) | 1 hour idle tunnel allows SSH, long-polling, idle browser tabs | OK |
| sing-box TUN-mode stress runs the client in its own podman netns (not `--network=host`) | `NET_ADMIN` + `/dev/net/tun` scoped to the sing-box container's namespace; iperf3/curl share the netns via `--network=container:<singbox>` so their traffic enters the tun device first | OK |
