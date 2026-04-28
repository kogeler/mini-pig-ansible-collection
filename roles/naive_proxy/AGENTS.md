# naive_proxy — Agent Context

## Rules for AI agents running Molecule

1. **Use the Makefile wrapper at `molecule/Makefile`, not bare `molecule` commands.** It hides three env-var workarounds (`GIT_DIR=/dev/null`, `MP_DRIVER=<driver>`, `ANSIBLE_LIBRARY=.../molecule_plugins/vagrant/modules`). Target schema is `make <scenario>-<driver>-<action>`; run `make help` for the full list.
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
   For the vagrant driver use `make default-vagrant-login` (drops you into SSH on the VM).
5. **Keep the Molecule instance alive** between iterations. Re-run the `converge` and `verify` actions without destroying. Only destroy when the instance state is suspect or you need to test from scratch.
6. **Always activate the venv** before any make/molecule/ansible command: `source /media/data/app/python/venv3/bin/activate`
7. **Do not switch drivers against a live instance.** If a vagrant VM is up, `make default-podman-*` will route to the vagrant driver (molecule keeps driver state per scenario). Run `make default-vagrant-destroy` first, then the podman target.

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
| `naive-haproxy` | `haproxy:3.2-alpine` | long-running | :443 TCP, :8444 HTTPS | TLS termination, auth routing, speed tuning |
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
└── molecule/
    ├── Makefile           # Thin wrapper: <scenario>-<driver>-<action>, hides MP_DRIVER/GIT_DIR/ANSIBLE_LIBRARY
    ├── default/           # Dual-driver scenario (podman-in-podman + vagrant-libvirt), Debian trixie
    │   ├── molecule.yml
    │   ├── Dockerfile.j2
    │   ├── prepare.yml
    │   └── ENABLE_CI      # Marker: include in CI matrix
    ├── debian-bookworm/   # Local podman-in-podman scenario (Debian bookworm)
    │   ├── molecule.yml
    │   ├── Dockerfile.j2
    │   ├── prepare.yml
    │   └── ENABLE_CI
    ├── gha/               # GitHub Actions localhost scenario (ansible-native)
    │   ├── molecule.yml
    │   ├── prepare.yml
    │   ├── inventory/hosts.yml
    │   └── ENABLE_CI
    └── shared/            # Common playbooks and tasks for all scenarios
        ├── converge.yml
        ├── verify.yml
        ├── benchmark.yml
        ├── utils.yml
        ├── tasks/
        │   ├── prepare.yml
        │   ├── wait-services.yml
        │   └── benchmark.yml
        └── vars/
            ├── common.yml     # Shared variables (domain, ports, naive version)
            └── benchmark.yml
```

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
- `naive_proxy_users` — dict `{ name: password }`, at least one user

### Important

- `naive_proxy_listen_port` — default `443`
- `naive_proxy_external_port` — public port advertised to clients
- `naive_proxy_naive_version` — release tag, for example `v143.0.7499.109-2`
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

The role defaults to a speed-first profile for a dedicated VPN edge:

- `naive_proxy_haproxy_cpu_policy: "performance"`
- `naive_proxy_haproxy_ssl_cache_size: 40000`
- `naive_proxy_haproxy_expected_bandwidth_mbps: 1000`
- `naive_proxy_haproxy_expected_rtt_ms: 100`
- `naive_proxy_haproxy_h2_frontend_rxbuf: ""` — empty means "derive automatically from bandwidth and RTT"
- `naive_proxy_haproxy_notsent_lowat: 0` — optional, disabled by default

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
- `_naive_proxy_haproxy_h2_frontend_rxbuf_effective` — final H2 frontend receive buffer after applying auto-sizing or explicit override

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
| `default` | vagrant-libvirt (VM) | `default-vagrant-` | Local dev on a real VM, Debian trixie |
| `debian-bookworm` | podman | `bookworm-podman-` | Local dev, podman-in-podman, Debian 12 |
| `gha` | ansible-native (delegated) | `gha-native-` | GitHub Actions, role applied to runner VM |

The `default` scenario supports two drivers selected at runtime via `MP_DRIVER` (podman | vagrant). The platforms block carries keys for both drivers in the same `molecule.yml`; each driver reads only what it understands.

All scenarios share playbooks and tasks from `molecule/shared/`. Each has its own `molecule.yml` and `prepare.yml`. Shared variables live in `molecule/shared/vars/common.yml`.

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

# default scenario on vagrant-libvirt
make default-vagrant-converge
make default-vagrant-verify
make default-vagrant-destroy

# debian-bookworm (podman-only)
make bookworm-podman-test

# gha (localhost, no containers)
make gha-native-test
```

During iterative work, do not destroy the instance between changes. Re-run `make ...-converge` and `make ...-verify` against the same instance. Use `make ...-test` only at the end of a session.

### Driver conditionals and env-var plumbing

The Makefile is the only place that knows about env-var workarounds:

- `GIT_DIR=/dev/null` — makes molecule stop misidentifying the role as a collection (collections/ is gitignored).
- `MP_DRIVER=<podman|vagrant>` — selects the driver for the `default` scenario at runtime. Prefix is `MP_` because molecule silently drops env vars named `MOLECULE_*` (see `MOLECULE_KEEP_STRING` in `molecule.config`).
- `ANSIBLE_LIBRARY=.../molecule_plugins/vagrant/modules` — required only for the vagrant driver. molecule 26 no longer auto-injects driver module paths (see [molecule-plugins#301](https://github.com/ansible-community/molecule-plugins/issues/301)). The Makefile resolves the path from the active Python env.

Inside the playbooks, the single source of truth for driver-conditional behavior is the `mp_driver` host_var, set in `molecule/default/molecule.yml`:

```yaml
provisioner:
  inventory:
    host_vars:
      molecule-naive-proxy:
        mp_driver: '{{ lookup("env", "MP_DRIVER") | default("podman", true) }}'
        ansible_become: '{{ mp_driver != "podman" }}'
```

- `ansible_become` follows from `mp_driver` — podman container runs as root (no sudo), vagrant VM needs sudo.
- Tasks that must branch on driver use `when: mp_driver != 'podman'` (see the `/etc/hosts` patch in `shared/tasks/prepare.yml`, needed for SSH-based drivers because podman's own `etc_hosts` mechanism handles the container case and `/etc/hosts` there is a bind-mount that `lineinfile` cannot atomic-replace).
- `host_vars` (not `group_vars.all`) so that localhost — used by vagrant's `create.yml` and `destroy.yml` — does not inherit become.

Other `MP_*` env vars tune the vagrant platform: `MP_VAGRANT_PROVIDER` (default `libvirt`), `MP_BOX` (default `debian/trixie64`), `MP_VM_MEMORY`, `MP_VM_CPUS`. `MP_NETWORK` sets the podman platform network mode.

### Vagrant driver prerequisites

Host-side one-time setup:

- `python-vagrant` installed in the molecule venv (`pip install python-vagrant`).
- `vagrant` CLI with `vagrant-libvirt` plugin.
- libvirt with nftables firewall backend (`firewall_backend = "nftables"` in `/etc/libvirt/network.conf`, then `systemctl restart libvirtd`). Default libvirt network must be active (`virsh net-start default`).
- User in groups `libvirt` and `kvm`.

Box comes from Vagrant Cloud on first `create`; cached afterwards. `generic/debian13` does not exist — use `debian/trixie64` (the default).

### What `molecule verify` checks

1. Pod, HAProxy, decoy, and backend services are active
2. Decoy site is served through HAProxy TLS
3. Pebble-issued certificate replaces the bootstrap self-signed cert
4. `naive-acme-renew.timer` is enabled
5. Forced renewal changes the certificate serial and HAProxy serves the new cert
6. Direct HTTPS proxy mode works: `curl -x`
7. naive SOCKS5 mode works through HAProxy and receives padding
8. The benchmark task moves real traffic through the SOCKS5 tunnel with `iperf3`

### SOCKS5 test client

The naive SOCKS5 client runs inside a container (Debian/Ubuntu based, `--network host`) rather than as a bare host binary. This is required because the naive binary (Chromium networking stack) fails on some host environments (Ubuntu 24.04 / kernel 6.17) while working correctly inside containers. The client container image and tag are configurable via `naive_client_image` and `naive_client_image_tag` in `molecule/shared/vars/benchmark.yml`.

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
iperf3 through SOCKS5 tunnel: 3039.7 Mbps
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

The test domain `naive.test` is mapped to `127.0.0.1` via `etc_hosts` in molecule.yml (podman driver) or via a `lineinfile` task gated on `when: mp_driver != 'podman'` in `shared/tasks/prepare.yml` (vagrant/gha). Two mechanisms because `/etc/hosts` inside the podman container is a bind-mount that `lineinfile` cannot atomic-replace.

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
