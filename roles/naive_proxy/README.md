# naive_proxy

Ansible role for deploying [NaiveProxy](https://github.com/klzgrad/naiveproxy) using the official [HAProxy setup](https://github.com/klzgrad/naiveproxy/wiki/HAProxy-Setup). The role runs a small Podman pod under systemd:

- `HAProxy` on the public port for TLS termination, auth routing, and ACME ALPN/SNI dispatch
- `naive` standalone backend for authenticated CONNECT tunnels
- `sing-box` AnyTLS server, a second proxy reached on the same public port by a **separate domain** (SNI-routed), with its **own** Let's Encrypt certificate
- `Caddy` decoy site for unauthenticated naive traffic
- `acme.sh` renewal as a oneshot systemd service and timer (naive domain only — sing-box manages its own AnyTLS certificate)

The role also includes Molecule coverage for certificate issuance, HTTPS proxy mode, SOCKS5 tunneling, AnyTLS connectivity, and throughput benchmarking.

## Architecture

```text
Internet :443
    |
    v
+--- Pod: naive-pod -------------------------------------------------------+
|                                                                          |
|  HAProxy TCP frontend (:443) — inspects SNI + ALPN                       |
|    |-- SNI = anytls domain ----> sing-box AnyTLS (:8445, TCP passthrough)|
|    |                             (terminates TLS itself; own Let's       |
|    |                              Encrypt cert via inline TLS-ALPN-01)    |
|    |-- ALPN acme-tls/1 --------> acme.sh (:10443, oneshot via timer)     |
|    |   (naive domain)                                                    |
|    +-- default --------------->  HAProxy HTTPS frontend (:8444)          |
|                                  TLS termination                         |
|                                  |                                       |
|                                  |-- auth OK --> naive backend (:8080)   |
|                                  |              (standalone binary)      |
|                                  |                                       |
|                                  +-- no auth --> Caddy decoy (:8081)     |
|                                                 (static website)         |
+--------------------------------------------------------------------------+
```

Two servers share the public port, told apart by **SNI**:

- the **naive domain** is TLS-terminated by HAProxy and differentiated by HTTP auth — authenticated requests go to the naive backend, unauthenticated to the Caddy decoy
- the **AnyTLS domain** is passed straight through (TCP) to sing-box, which terminates TLS itself

The public client side is HTTP/2 over TLS on HAProxy. The internal HAProxy -> naive backend hop is plain HTTP on `127.0.0.1:8080`.

### AnyTLS server (sing-box)

The role deploys a second container in the same pod: a [sing-box](https://sing-box.sagernet.org) [AnyTLS](https://sing-box.sagernet.org/configuration/inbound/anytls/) inbound. It is reached on the same public port as naive but under a **separate domain**; HAProxy routes that SNI straight to sing-box as a raw TCP passthrough (`mode tcp`), so sing-box owns the TLS handshake end to end.

sing-box obtains and renews its **own** Let's Encrypt certificate using its built-in ACME (CertMagic), so no `acme.sh` or renewal timer is involved for the AnyTLS domain. Because sing-box runs ACME before its listener binds, CertMagic answers the `TLS-ALPN-01` challenge on the internal AnyTLS port (`alternative_tls_port`) during first issuance and inline on the same listener afterwards. HAProxy tells the **two independent ACME clients apart by SNI**: `acme-tls/1` for the naive domain reaches `acme.sh`, `acme-tls/1` for the AnyTLS domain reaches sing-box.

> **No decoy for AnyTLS.** Unlike the naive path, AnyTLS has no decoy site for unauthenticated visitors: sing-box's AnyTLS inbound has no fallback hook (the `sing-anytls` library supports one, but sing-box does not expose it), and the auth lives inside the protocol — not in HTTP — so HAProxy cannot route unauthenticated AnyTLS traffic to Caddy without breaking sing-box's own TLS/ACME. AnyTLS instead relies on a normal-looking TLS handshake plus traffic padding for camouflage. An active prober that completes TLS and sends a plain request gets the connection closed.

## Requirements

- Debian-based target host
- systemd
- Podman
- root privileges
- public reachability for the listen port
- for real ACME issuance, public port `443` must reach HAProxy for TLS-ALPN-01 (covers **both** the naive and the AnyTLS domain — each validated by SNI)
- a second FQDN for the AnyTLS server (`naive_proxy_anytls_domain`), resolving to the **same** public IP(s) as the naive domain
- outbound access to `ghcr.io` to pull the official sing-box image
- **controller-side** (only when `naive_proxy_client_qr_enabled`, the default — the client configs and their QR codes are written on the controller via `delegate_to: localhost`): the `qrcode` Python library with Pillow, installed for the **same Python that runs Ansible**. If you run Ansible from a virtualenv, install into that venv: `pip install "qrcode[pil]"` (the `[pil]` extra pulls in Pillow) — the system `apt install python3-qrcode python3-pil` packages are **not** visible inside a venv. Use the apt packages only when Ansible runs under the system Python. Set `naive_proxy_client_qr_enabled: false` to skip QR generation and drop this requirement

## Quick Start

```yaml
- hosts: proxy
  become: true
  roles:
    - role: kogeler.mini_pig.naive_proxy
      vars:
        naive_proxy_domain: "cdn.example.org"
        naive_proxy_anytls_domain: "edge.example.org"
        naive_proxy_external_ip_auto:
          helsinki: "203.0.113.10"
        naive_proxy_external_ip_manual:
          helsinki: "203.0.113.10"
        naive_proxy_users:
          alice: "s3cret-passw0rd"
        naive_proxy_acme_email: "admin@example.org"
```

`naive_proxy_external_ip_auto` and `naive_proxy_external_ip_manual` are maps of human-readable **server name → IP** — one feeding the automatic client config, the other the manual one (set both the same for identical server sets). For each entry the role generates one **Naive** and one **AnyTLS** client option, named after the server:

```text
helsinki - Naive
helsinki - AnyTLS
```

## What The Role Deploys

### Containers

| Container | Image | Purpose |
|---|---|---|
| `naive-haproxy` | `haproxy:3.3.10-alpine` | Public TLS endpoint, auth routing, ACME ALPN/SNI routing |
| `naive-backend` | `localhost/naive-backend:VERSION` | Standalone naive backend built locally by the role |
| `naive-anytls` | `ghcr.io/sagernet/sing-box:v1.13.13` | sing-box AnyTLS server (separate domain, own Let's Encrypt cert) |
| `naive-decoy` | `caddy:latest` | Static decoy site |
| `naive-acme` | `neilpang/acme.sh:latest` | Oneshot renewal container launched by systemd |
| `naive-pebble` | `ghcr.io/letsencrypt/pebble:latest` | Molecule-only ACME test CA |

### Systemd Units

| Unit | Purpose |
|---|---|
| `podman-naive-pod.service` | Pod lifecycle |
| `podman-naive-decoy.service` | Caddy decoy |
| `podman-naive-haproxy.service` | HAProxy frontend |
| `podman-naive-backend.service` | naive backend |
| `podman-naive-anytls.service` | sing-box AnyTLS server |
| `naive-acme-renew.service` | ACME issue/renew oneshot (naive domain) |
| `naive-acme-renew.timer` | Periodic renewal timer |

## Security And Runtime Notes

- Containers run with `--security-opt=no-new-privileges`.
- All containers include `--security-opt=apparmor=unconfined` for compatibility with Ubuntu 24.04 where the default `containers-default` AppArmor profile blocks socket operations.
- Long-running runtime containers drop all caps except `NET_BIND_SERVICE` where needed.
- Read-only rootfs is enabled by default.
- Container logs use Podman `--log-driver=passthrough` to avoid duplicated journal lines.
- The backend image is built locally and never pulled from a registry.
- HAProxy global section includes `no-quic` because QUIC is not used and `quic_test_socketopts()` fails in some container environments.
- `backend be_naive` uses `option http-server-close` and `http-reuse never` because the naive backend expects one proxy request per accepted TCP socket. Without this, HAProxy may reuse an idle backend connection for a CONNECT request, causing the backend to fall through to a raw HTTP proxy path instead of establishing a tunnel.

## Role Variables

### Required

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_domain` | `""` | Public server FQDN for the naive server |
| `naive_proxy_anytls_domain` | `""` | Public server FQDN for the AnyTLS server. Required when `naive_proxy_anytls_enabled` (the default). MUST differ from `naive_proxy_domain` and resolve to the **same** IP(s) — HAProxy tells the two servers apart by SNI |
| `naive_proxy_external_ip_auto` | `{}` | Map of **`<name>: <ip>`** (≥1 entry) for the **automatic** client config (`urltest`). Key = human-readable server name, value = publicly reachable IPv4 / IPv6 both domains resolve to. Used for option names and as the connect address. See [Generated Client Configs](#generated-client-configs) |
| `naive_proxy_external_ip_manual` | `{}` | Same as `_auto` but for the **manual** client config (`selector`). Set both maps to the same value for identical server sets, or differ them per mode |
| `naive_proxy_users` | `{}` | Dict of `name: password`, at least one user. Shared by both protocols — the password doubles as the AnyTLS secret |

### General

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_enabled` | `true` | Enable or disable the role |
| `naive_proxy_config_dir` | `/opt/naive-proxy` | Host path for config, certs, build context, and decoy data |
| `naive_proxy_client_config_dir` | `"{{ playbook_dir }}/naive-proxy-json-configs"` | Controller-side output path for generated sing-box configs |
| `naive_proxy_molecule_mode` | `false` | Enable Pebble and Molecule-specific ACME behavior; never use in production |

### Network

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_listen_port` | `443` | Public port bound by HAProxy |
| `naive_proxy_external_port` | `"{{ naive_proxy_listen_port }}"` | Public client-facing port advertised in generated client configs |

`naive_proxy_external_port` is useful when clients reach the server through forwarding or NAT and see a different public port than the local HAProxy bind.

### Naive Backend

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_naive_version` | `"v148.0.7778.96-5"` | Standalone naive release tag |
| `naive_proxy_padding` | `true` | Enable `--padding` on the backend |
| `naive_proxy_backend_base_image` | `"docker.io/library/ubuntu"` | Base image for the backend container build |
| `naive_proxy_backend_base_image_tag` | `"22.04"` | Base image tag |
| `naive_proxy_backend_extra_env` | `{}` | Extra environment for the backend container |
| `naive_proxy_backend_extra_volumes` | `[]` | Extra volumes for the backend container |
| `naive_proxy_backend_extra_args` | `[]` | Extra **podman** flags inserted before the image (e.g. `--shm-size`, `--ulimit`). NOT for the naive binary — those flags would be eaten by `podman container run` and produce `unknown flag` errors |
| `naive_proxy_backend_naive_args` | `[]` | Extra flags appended to the **naive** binary entrypoint (after the image). Use for chromium net-stack tuning: `["--v=1"]` for verbose logging, `["--vmodule=naive_proxy*=2"]` for module-targeted verbose, `["--log-net-log=/var/log/naive/netlog.json"]` for chrome://net-export dumps, etc. |

### Images

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_haproxy_image` | `"docker.io/library/haproxy"` | HAProxy image |
| `naive_proxy_haproxy_image_tag` | `"3.3.10-alpine"` | Pinned to the explicit HAProxy minor — `v3.3.10` is the first 3.3.x release shipping the [haproxy/haproxy#3354](https://github.com/haproxy/haproxy/issues/3354) PADDED-DATA fix backport (`043db34`, upstream `faf3e9a`). Do not drop below `3.3.10`: 3.3.9 and earlier 3.3.x, plus the entire 3.2 / 3.0 / 2.8 lines, still carry the bug. Bumping upwards to a newer 3.3.x is the expected maintenance path — verify `haproxy -v` on the new image reports a build dated after 2026-05-07. |
| `naive_proxy_decoy_image` | `"docker.io/library/caddy"` | Decoy image |
| `naive_proxy_decoy_image_tag` | `"latest"` | Decoy image tag |
| `naive_proxy_acme_image` | `"docker.io/neilpang/acme.sh"` | ACME image |
| `naive_proxy_acme_image_tag` | `"latest"` | ACME image tag |
| `naive_proxy_singbox_image` | `"ghcr.io/sagernet/sing-box"` | sing-box AnyTLS server image. The official image is Alpine-based and built with `with_acme` plus core AnyTLS |
| `naive_proxy_singbox_image_tag` | `"v1.13.13"` | Pinned explicit stable tag (not `latest`) for reproducibility. Bumping to a newer stable release is the expected maintenance path |

### AnyTLS (sing-box)

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_anytls_enabled` | `true` | Deploy the sing-box AnyTLS server. On by default, like the naive backend. Set `false` to ship naive only (client configs then carry Naive options only) |
| `naive_proxy_anytls_domain` | `""` | AnyTLS server FQDN (required when enabled). Separate from `naive_proxy_domain`, resolving to the same IP(s) |
| `naive_proxy_anytls_log_level` | `"info"` | sing-box log level: `debug` / `info` / `warn` / `error` |
| `naive_proxy_anytls_acme_enabled` | `true` | When `true`, sing-box obtains and renews its own certificate via ACME. When `false`, it serves a static certificate from `<config_dir>/anytls-certs/{fullchain,key}.pem` (drop your own there, or let `molecule_mode` generate a self-signed one). `molecule_mode` forces the static path |
| `naive_proxy_anytls_acme_provider` | `"letsencrypt"` | ACME CA for the AnyTLS certificate: `letsencrypt` / `zerossl` / a custom ACME directory URL |
| `naive_proxy_anytls_acme_email` | `"{{ naive_proxy_acme_email }}"` | ACME account email for the AnyTLS certificate. Defaults to the naive ACME email |
| `naive_proxy_anytls_utls_fingerprint` | `""` | uTLS browser-fingerprint mimicry for the AnyTLS **client** outbound. When non-empty, every `<name> - AnyTLS` option in the generated client configs gets a `tls.utls { enabled: true, fingerprint: <value> }` block so the client's TLS ClientHello imitates that browser. Empty (default) disables it. Valid: `chrome`, `firefox`, `edge`, `safari`, `360`, `qq`, `ios`, `android`, `random`, `randomized`. **Enabling this also makes the AnyTLS server advertise the matching ALPN (`h2,http/1.1`) automatically** — required because a browser ClientHello offers that ALPN while the ACME (TLS-ALPN-01) server would otherwise present only `acme-tls/1` (→ `no_application_protocol`). The coupling is internal; this is the only knob to set |

AnyTLS authentication reuses `naive_proxy_users` — the password doubles as the AnyTLS shared secret, so both protocols share one credential set.

### ACME / TLS

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_acme_email` | `""` | Optional ACME account email |
| `naive_proxy_acme_server` | `"letsencrypt"` | Explicit ACME CA passed to `acme.sh` outside `molecule_mode` |

The role defaults to `letsencrypt` explicitly so the first issuance does not depend on ZeroSSL account registration behavior inside `acme.sh`.

### Decoy Site

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_decoy_index_html` | `""` | Path to a custom decoy `index.html`; empty uses the built-in stub page. Ignored when `naive_proxy_decoy_upstream_url` is set |
| `naive_proxy_decoy_upstream_url` | `""` | When set (e.g. `https://example.com`), Caddy reverse-proxies unauthenticated traffic to this URL instead of serving a local static page. Caddy terminates HTTPS on the upstream side and rewrites the `Host` header to the upstream hostname |

### Container Options

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_read_only_rootfs` | `true` | Run long-lived containers with a read-only rootfs |
| `naive_proxy_selinux_relabel` | `false` | Add `:Z` relabeling to bind mounts |

### Optional Runtime Image Refresh

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_update_runtime_images` | `false` | Force-pull fresh runtime images for HAProxy and decoy near the start of the role |

When this is enabled:

- only runtime images are refreshed
- HAProxy and decoy are eligible
- backend is excluded because it is built locally
- ACME and Molecule-only Pebble are excluded
- a restart is queued only when the pulled image ID actually changed

### HAProxy Tuning

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_haproxy_timeout_connect` | `"5s"` | Backend connect timeout |
| `naive_proxy_haproxy_timeout_client` | `"60s"` | Client idle timeout (H2 persistent connection) |
| `naive_proxy_haproxy_timeout_server` | `"60s"` | Backend response timeout |
| `naive_proxy_haproxy_timeout_tunnel` | `"3600s"` | CONNECT tunnel idle timeout (1h for VPN sessions) |
| `naive_proxy_haproxy_timeout_client_fin` | `"30s"` | Client FIN timeout |
| `naive_proxy_haproxy_timeout_server_fin` | `"30s"` | Server FIN timeout |
| `naive_proxy_haproxy_global_maxconn` | `0` | Optional global connection cap; `0` keeps HAProxy defaults |
| `naive_proxy_haproxy_cpu_policy` | `"performance"` | HAProxy 3.2 CPU policy |
| `naive_proxy_haproxy_ssl_cache_size` | `40000` | SSL session cache blocks |
| `naive_proxy_haproxy_h2_frontend_rxbuf` | `"6m"` | Per-stream H2 frontend receive buffer. Sets `tune.h2.fe.rxbuf <size>` in HAProxy `global`. Units: HAProxy size syntax — bytes by default, with optional `k` / `m` / `g` suffixes (KiB / MiB / GiB, base 1024). Examples: `1638400`, `1600k`, `12500000`, `12m`. Empty omits the directive and HAProxy uses its own default of `1600k` (1638400 bytes ≈ 1.6 MiB, ~130 Mbps × 100 ms RTT). Raise on high-BDP links: rough sizing `BDP_bytes ≈ bandwidth_mbps × rtt_ms × 125`. RAM cost is per-stream × concurrent H2 streams. |
| `naive_proxy_haproxy_h2_initial_window_size` | `1048576` | Per-stream H2 initial flow-control window (bytes). Sets `tune.h2.fe.initial-window-size`. Default 1 MiB cuts WINDOW_UPDATE round-trips on long-lived bidirectional streams; sized to match `h2_frontend_rxbuf`. Set to `0` to keep HAProxy default (RFC 7540: 65535). Requires HAProxy 3.0+. Originally introduced as a mitigation for [haproxy/haproxy#3354](https://github.com/haproxy/haproxy/issues/3354); upstream-fixed by `faf3e9a` (3.3 backport `043db34`), so it now functions purely as a throughput knob. |
| `naive_proxy_haproxy_h2_max_frame_size` | `0` | H2 max frame size HAProxy advertises to clients. Sets `tune.h2.max-frame-size`. `0` omits the directive and HAProxy keeps its default of 16 KiB. RFC max is 16777215 but values that large cause head-of-line blocking on multiplexed connections. Requires HAProxy 3.0+. Previously defaulted to `1048576` as a secondary mitigation for [haproxy/haproxy#3354](https://github.com/haproxy/haproxy/issues/3354); justification removed now that the bug is upstream-fixed (`faf3e9a`, 3.3 backport `043db34`). Re-raise only with a benchmark proving a real parsing-overhead win. |
| `naive_proxy_haproxy_notsent_lowat` | `0` | Optional Linux-only low-water mark; disabled by default |

### HAProxy Diagnostics (opt-in)

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_haproxy_diagnostics_enabled` | `false` | Master switch. When `true`, the role declares a 32 MiB `ring h2trace` sink in `haproxy.cfg`, opens `stats socket ipv4@*:<port> level admin` in the `global` section, and adds `--publish 127.0.0.1:<port>:<port>` to the pod so the admin socket is reachable on the *host's loopback* (and only there). Toggling this var requires a *pod* restart, not just an haproxy restart. Off in production unless actively debugging. |
| `naive_proxy_haproxy_diagnostics_port` | `19999` | TCP port for the admin socket. Reachable as `127.0.0.1:<port>` from the host only. |
| `naive_proxy_haproxy_diagnostics_ring_size` | `134217728` | Trace ring sink size in bytes. Default 128 MiB keeps a full 5-minute capture without rolling over at `verbosity complete` (full frame hex-dumps, ~10x more bytes per event than `advanced`). 32 MiB suffices at `verbosity advanced`. |

The `roles/naive_proxy/debug/` toolkit (start-capture, h2trace-start, stop-capture-dump-h2, analyze) needs both the admin socket and the ring sink. Enable diagnostics in the role, re-apply, and the toolkit can speak to the running stack via `nc 127.0.0.1 <port>` from the host. See `debug/README.md` for the workflow.

## Tags

| Tag | Scope |
|---|---|
| `naive-proxy` | Entire role |
| `naive-proxy-preflight` | Validation |
| `naive-proxy-install` | Packages and directories |
| `naive-proxy-image` | Backend image build |
| `naive-proxy-utils` | Optional runtime image refresh |
| `naive-proxy-config` | Configs, certs, unit files |
| `naive-proxy-services` | Service start and enable |
| `naive-proxy-acme` | ACME issue and timer |
| `naive-proxy-clients` | Client config generation |
| `naive-proxy-healthchecks` | Built-in post-deploy healthchecks |

## Built-In Healthchecks

At the end of the role, `tasks/healthchecks.yml` runs automatically unless Ansible is in check mode.

It does two things:

1. waits with retries until all managed units are active
2. sends an HTTPS probe to HAProxy on `127.0.0.1:{{ naive_proxy_listen_port }}` with the expected `Host` header built from `naive_proxy_domain` and `naive_proxy_external_port`

The probe intentionally ignores certificate validation so the initial bootstrap self-signed cert does not fail the deployment. It only requires a successful `200` response; exact decoy content is checked in Molecule, not in production runs.

## Examples

### Minimal Production Setup

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_external_ip_auto:
  v4: "203.0.113.10"
naive_proxy_external_ip_manual:
  v4: "203.0.113.10"
naive_proxy_users:
  alice: "pass1"
```

### Explicit ACME Email

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_external_ip_auto:
  v4: "203.0.113.10"
naive_proxy_external_ip_manual:
  v4: "203.0.113.10"
naive_proxy_acme_email: "admin@example.org"
naive_proxy_users:
  alice: "pass1"
```

### Port Forwarding In Front Of HAProxy

HAProxy may listen locally on one port while clients see another public port.

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_external_ip_auto:
  v4: "203.0.113.10"
naive_proxy_external_ip_manual:
  v4: "203.0.113.10"
naive_proxy_listen_port: 8443
naive_proxy_external_port: 443
naive_proxy_users:
  alice: "pass1"
```

### Custom Decoy Page

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_external_ip_auto:
  v4: "203.0.113.10"
naive_proxy_external_ip_manual:
  v4: "203.0.113.10"
naive_proxy_users:
  alice: "pass1"
naive_proxy_decoy_index_html: "{{ playbook_dir }}/files/decoy-index.html"
```

### Remote Decoy (Reverse-Proxied Site)

Caddy reverse-proxies all unauthenticated traffic to a remote site instead of
serving a local stub page. Useful when you want the public TLS endpoint to look
like an actual site under your control without hosting one. Pick an upstream
that does not leak its own domain through absolute URLs or redirects (see
[Limitations](#limitations)).

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_external_ip_auto:
  v4: "203.0.113.10"
naive_proxy_external_ip_manual:
  v4: "203.0.113.10"
naive_proxy_users:
  alice: "pass1"
naive_proxy_decoy_upstream_url: "https://example.com"
```

When set, `naive_proxy_decoy_index_html` is ignored.

### Override HAProxy Tuning

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_external_ip_auto:
  v4: "203.0.113.10"
naive_proxy_external_ip_manual:
  v4: "203.0.113.10"
naive_proxy_users:
  alice: "pass1"
naive_proxy_haproxy_cpu_policy: "performance"
# Per-stream H2 frontend rx buffer. HAProxy size syntax (bytes / k / m / g);
# this is BDP for 500 Mbps × 50 ms ≈ 3.125 MB. Empty / unset would leave
# HAProxy's own 1600k (1638400 bytes ≈ 1.6 MiB) default.
naive_proxy_haproxy_h2_frontend_rxbuf: "3125k"
```

### Force-Pull Runtime Images On An Existing Host

```yaml
naive_proxy_update_runtime_images: true
```

## Generated Client Configs

The role writes **two sing-box JSON configs per user** on the controller:

- `singbox-<host>-<user>-auto.json` — automatic selection (a `urltest` outbound picks the lowest-latency option)
- `singbox-<host>-<user>-manual.json` — manual selection (a `selector` outbound the user switches in the GUI)

> **Manual vs automatic.** Earlier versions shipped only the automatic-failover config. Automatic switching proved awkward in real use, so the role now also generates a manual-selection config. Pick whichever file fits the client; both contain the full set of options.

### Server map → options

Each config draws its servers from its own `<name>: <ip>` map —
`naive_proxy_external_ip_auto` for the automatic file, `naive_proxy_external_ip_manual` for the manual file. For **each** entry the role generates one option **per protocol**, named after the server and the protocol:

```text
<name> - Naive
<name> - AnyTLS
```

So the number of options in a file is:

```text
2 × (number of entries in that file's map)
```

(or `1 ×` when `naive_proxy_anytls_enabled: false`). Within a file, the same IP backs both protocols.

- The **display name** comes from the **map key**, never the IP.
- The IP from the matching map entry is used as the connect address (the outbound's `server` field), **not** the FQDN — this skips bootstrap DNS for the proxy itself.
- TLS uses the right domain per protocol via `tls.server_name`: the **naive domain** for `<name> - Naive`, the **AnyTLS domain** for `<name> - AnyTLS`. So a single IP can serve both, told apart by SNI.
- When `naive_proxy_anytls_utls_fingerprint` is set, each `<name> - AnyTLS` option also carries a `tls.utls` block so the client's TLS ClientHello mimics the chosen browser (e.g. `firefox`).

Both files share a `tun` inbound and a top `proxy` outbound (the `urltest`/`selector`) that the route default and DNS detour target. The generated TUN profile is IPv4-only: global IPv6 destinations are rejected to avoid IPv6 leaks. DNS inside the tunnel goes through Cloudflare DoH (`dns-remote-cloudflare`) detoured through `proxy`.

The `urltest` probe URL and interval (auto config only) are tunable via `naive_proxy_singbox_urltest_url` (default `https://www.gstatic.com/generate_204`) and `naive_proxy_singbox_urltest_interval` (default `3m`).

The generated config requires sing-box 1.13.0+ with Naive outbound support (and core AnyTLS); on Linux use an official build variant that includes Cronet for the naive outbound.

### QR codes

For every JSON config the role also writes a PNG **QR code** with the config embedded, next to the file and sharing its name (`singbox-<host>-<user>-auto.png`, …) — scan it to import a profile instead of copying a file. The QR carries the **minified** config (the pretty-printed JSON never fits a single symbol). Generation is handled by the collection's own `kogeler.mini_pig.qr_code` module (with a pure-Python companion decoder `kogeler.mini_pig.qr_decode` used by the test suite), so it needs `python3-qrcode` + `python3-pil` on the controller (see [Requirements](#requirements)).

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_client_qr_enabled` | `true` | Write a `.png` QR next to each generated JSON config. Set `false` to skip it (and drop the controller-side `qrcode`/Pillow requirement) |
| `naive_proxy_client_qr_error_correction` | `"L"` | QR error-correction level (`L`/`M`/`Q`/`H`). `L` holds the most data (~2953 bytes) and is the right default for these configs; higher levels survive more damage but hold less |
| `naive_proxy_client_qr_box_size` | `8` | Pixels per QR module |
| `naive_proxy_client_qr_border` | `4` | Quiet-zone width in modules (QR spec minimum is 4) |

> **Capacity.** A single QR symbol holds ~2953 bytes at level `L`. With AnyTLS on, each server is two outbounds, so a config fits up to **~4 servers**; beyond that the module fails the run with a clear message rather than emitting an unscannable code — generate fewer servers per file, or set `naive_proxy_client_qr_enabled: false` and distribute the JSON directly.

### Example

Input:

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_anytls_domain: "edge.example.org"
naive_proxy_external_ip_auto:
  helsinki: "203.0.113.10"
  stockholm: "203.0.113.20"
naive_proxy_external_ip_manual:
  helsinki: "203.0.113.10"
naive_proxy_users:
  alice: "s3cret-passw0rd"
```

Generated files for `alice`:

```text
# singbox-cdn-alice-auto.json    (urltest) — from the auto map, 4 options
helsinki - Naive      server=203.0.113.10  SNI=cdn.example.org
helsinki - AnyTLS     server=203.0.113.10  SNI=edge.example.org
stockholm - Naive     server=203.0.113.20  SNI=cdn.example.org
stockholm - AnyTLS    server=203.0.113.20  SNI=edge.example.org

# singbox-cdn-alice-manual.json  (selector) — from the manual map, 2 options
helsinki - Naive      server=203.0.113.10  SNI=cdn.example.org
helsinki - AnyTLS     server=203.0.113.10  SNI=edge.example.org
```

The auto file has `2 servers × 2 protocols = 4 options`; the manual file, drawing from its own (smaller) map, has `1 × 2 = 2`. Set both maps to the same value for identical configs.

For direct naive CLI usage:

```json
{
  "listen": "socks://127.0.0.1:1080",
  "proxy": "https://alice:s3cret-passw0rd@cdn.example.org"
}
```

## Service Management

```bash
# Status
systemctl status podman-naive-pod.service
systemctl status podman-naive-haproxy.service
systemctl status podman-naive-backend.service
systemctl status podman-naive-anytls.service
systemctl status podman-naive-decoy.service
systemctl status naive-acme-renew.timer

# Logs
journalctl -u podman-naive-haproxy.service -f
journalctl -u podman-naive-backend.service -f
journalctl -u podman-naive-anytls.service -f

# Manual certificate issue/renew
systemctl start naive-acme-renew.service

# Restart runtime services
systemctl restart podman-naive-haproxy.service
systemctl restart podman-naive-backend.service
systemctl restart podman-naive-decoy.service
```

## Idempotency

The role is designed to be idempotent:

- backend image is rebuilt only when its Containerfile changes
- config and unit changes notify the corresponding handlers
- runtime image refresh is opt-in and restart-sensitive to image ID changes
- ACME certs are kept on the host and reused between runs
- repeated runs with unchanged inputs should not produce runtime churn

## Debug Toolkit

`debug/` contains operator-side shell scripts for diagnosing HAProxy H2
issues against a deployed stack — packet captures (host + pod-netns),
H2 trace ring dumps from the HAProxy admin socket, structured analysis
reports, and bootstrap helpers for sending/receiving files when the
only access to the target is a TCP-bridged TTY (e.g. `socat ... TCP:127.0.0.1:5555,...`).

Not rendered by the role at apply time. Copy the scripts to `/tmp/` on
the target host and invoke manually. All knobs (NIC name, container
name, ports, admin socket, etc.) are exposed as env vars and `--flag`
CLI options so the toolkit works against any naive_proxy deployment.

See [`debug/README.md`](debug/README.md) for the full workflow,
prerequisites, parametrisation table, output interpretation, and
gotchas. Quick index of files:

| script | runs on | purpose |
|---|---|---|
| `start-capture.sh` | target | tcpdump (host + pod-netns) + journal-follow + ss/nstat sampler |
| `h2trace-start.sh` | target | enable HAProxy H2 trace into a configurable ring sink |
| `stop-capture-dump-h2.sh` | target | stop watchers + dump trace events into the capture dir |
| `analyze.sh` | target | structured report on a capture dir + cumulative TSV |
| `upload-via-tty.sh` | operator | base64-stream a local file to a TCP-bridged TTY |
| `download-via-tty.sh` | operator | the reverse — pull a file off the target |

## Molecule

The role ships with multiple Molecule scenarios sharing common playbooks from `molecule/shared/`.

### Scenarios

| Scenario | Driver | Purpose |
|----------|--------|---------|
| `default` | podman | Local dev, podman-in-podman, Debian trixie |
| `debian-bookworm` | podman | Local dev, podman-in-podman, Debian 12 |
| `gha` | ansible-native | GitHub Actions, role applied directly to runner VM |
| `singbox-stress` | podman | Reproduce sing-box / SFA HTTP/2 errors with `iperf3 -P` over a Linux sing-box `naive` outbound |
| `anytls-stress` | podman | End-to-end AnyTLS: sing-box issues a real cert from the local Pebble CA via ACME (HAProxy routes the TLS-ALPN-01 challenge to it), then a Linux sing-box `anytls` outbound through `tun` → ssl-router → HAProxy (SNI) → sing-box AnyTLS server carries `iperf3 -P` traffic |

A scenario is included in the CI matrix only if its directory contains an `ENABLE_CI` marker file.

### What `molecule verify` Covers

1. runtime services are active
2. decoy is served through HAProxy TLS
3. Pebble-issued cert replaces the bootstrap cert
4. `naive-acme-renew.timer` is enabled
5. forced renewal rotates the certificate
6. direct HTTPS proxy mode works
7. SOCKS5 tunneling through the official naive client works (client runs in a container)
8. padding negotiation reaches the client (`Variant1`)
9. requests with `Host: <domain>:<external_port>` are accepted even when HAProxy listens on a different local port
10. `iperf3` traffic passes through the SOCKS5 tunnel
11. the sing-box AnyTLS container deploys and HAProxy SNI-routes the AnyTLS domain to it
12. per-user client configs carry the two-file (auto + manual) layout with one Naive and one AnyTLS option per server, named from the map key
13. (`default`) each config's `.png` QR code decodes back — via the pure-Python `kogeler.mini_pig.qr_decode` module, no external tools — to the exact same object as its JSON (QR generation + decode round-trip is scoped to this one scenario)
14. (`anytls-stress`) the AnyTLS server obtains a **real certificate from the local Pebble CA via ACME** — the served cert's issuer is asserted to be Pebble, proving HAProxy routes the AnyTLS domain's TLS-ALPN-01 challenge to sing-box (two independent ACME clients, acme.sh + sing-box, told apart by SNI); the production Let's Encrypt config is additionally validated with `sing-box check`
15. (`anytls-stress`) a real AnyTLS connection carries `iperf3` traffic end to end through the sing-box server
16. (`anytls-stress`) the client converges with `naive_proxy_anytls_utls_fingerprint: firefox`; the AnyTLS ClientHello is captured on the wire and asserted to carry Firefox's uTLS markers (ffdhe2048 supported-group + record_size_limit extension)

### Running Tests

All runs go through the wrapper Makefile at `molecule/Makefile`. It exposes a uniform `<scenario>-<driver>-<action>` target schema.

```bash
cd naive_proxy/molecule

# list available targets
make help

# default scenario on podman
make default-podman-test
make default-podman-converge
make default-podman-verify
make default-podman-login

# Debian 12 scenario
make bookworm-podman-test

# GHA scenario (localhost)
make gha-native-test

# sing-box stress scenario (reproducer for sing-box / SFA H2 errors)
make singbox-stress-podman-converge
make singbox-stress-podman-verify

# AnyTLS stress scenario (real AnyTLS connection + traffic)
make anytls-stress-podman-converge
make anytls-stress-podman-verify
```

`<action>` is forwarded verbatim to `molecule` and may be any of: `test`, `create`, `converge`, `verify`, `idempotence`, `destroy`, `login`, `reset`, `prepare`, `check`.

Why the wrapper exists:

- `GIT_DIR=/dev/null` — `collections/` is gitignored at the repo root and without this shim molecule misidentifies the role as a collection.
- `MP_NETWORK` (default `slirp4netns`) — selects the rootless podman network mode for the molecule instance. Use `MP_NETWORK=host` to share the runner's network stack.

### Standalone Benchmark

The benchmark playbook runs the throughput portion without the rest of `verify`:

```bash
cd naive_proxy/molecule
make default-podman-converge

INV=/home/verstak/.ansible/tmp/molecule.<id>.default/inventory
ANSIBLE_COLLECTIONS_PATH=/media/data/git/ansible-v2/collections \
  ansible-playbook -i "$INV" shared/benchmark.yml
```

### Sing-box Stress Scenario

`singbox-stress` is an opt-in scenario for reproducing the HTTP/2 protocol
errors reported by real sing-box / SFA clients against a deployed
naive_proxy stack:

```text
outbound/naive: stream failed: http2 protocol error
connection upload closed: http2 protocol error
connection download closed: http2 protocol error
```

It mirrors the `default` scenario (podman-in-podman, Debian trixie) but
uses a **scenario-local `converge.yml`** that applies the
`kogeler.mini_pig.naive_proxy` role followed by
`kogeler.mini_pig.ssl_router` — replicating the production topology
where `ssl_router` (nginx with `ssl_preread`) sits on `:443` in front
of HAProxy and SNI-routes incoming traffic to the HAProxy frontend on
`{{ molecule_naive_proxy_listen_port }}`. The sing-box client targets
`naive.test:443` (ssl-router), so the TLS handshake is end-to-end
between cronet and HAProxy through nginx's TCP/SNI proxy. Verify swaps
the official-naive SOCKS5 path for a Linux sing-box client with a
`naive` outbound and a [`mixed` inbound](https://sing-box.sagernet.org/configuration/inbound/mixed/),
then drives `iperf3 -P {{ iperf_parallel }}` (shared with the official-naive benchmark via `shared/vars/benchmark.yml`, default 16) through
the same proxychains4 + SOCKS5 setup as the official-naive benchmark.

The sing-box client config is shaped after `templates/singbox-client.json.j2`
(`direct` outbound + full `route.rules`). The naive outbound's `server`
is the molecule's `molecule_naive_proxy_external_ip` (127.0.0.1 — same
idea as the prod per-mode maps `naive_proxy_external_ip_auto` / `_manual`,
no bootstrap DNS for the proxy itself). Molecule-specific deviations: `tun` inbound → `mixed`;
the `dns` block is dropped entirely (prod-only `dns-remote-cloudflare`
targets 1.1.1.1 which is unreachable in the molecule sandbox) along
with the `hijack-dns` route rule that depends on it — `mixed` inbound
is TCP-only so no DNS queries flow through the proxy anyway;
`tls.certificate_path` points at the Pebble test CA; `log.level: debug`
for failure-surface visibility.

`mixed` is used instead of `tun` because iperf3 already exercises the
SOCKS5 path through proxychains4, and the failure surface is the Naive
outbound HTTP/2 stream (not Android `VpnService` / TUN). The scenario is
intentionally **TCP-only**; UDP-over-Naive is not exercised.

**TODO:** rework `singbox-stress` to drive traffic through a sing-box
`tun` inbound instead of the current `mixed`/SOCKS5 + proxychains path.
The current harness still validates the Naive outbound H2 stream, but
HAProxy issue #3354 investigation showed that matching the Android/SFA
client path more closely matters for reproducing client-generated
PADDED DATA / END_STREAM frame patterns.

The sing-box client container runs unprivileged: `--cap-drop=ALL`,
`--security-opt=no-new-privileges`, `--security-opt=apparmor=unconfined`,
no `/dev/net/tun` mount.

#### Sing-box binary

The sing-box binary is compiled inside `molecule/shared/Dockerfile.j2`
(symlinked from both `singbox-stress/` and `anytls-stress/`, so the two stress
scenarios share a single build definition) and baked into the molecule instance
image at `/usr/local/bin/sing-box`.
Standard SagerNet linux releases ship without `with_naive_outbound`, so the
Dockerfile installs the same Go toolchain SFA uses and runs:

```text
go install -tags=with_clash_api,with_quic,with_utls,with_naive_outbound \
    github.com/sagernet/sing-box/cmd/sing-box@${SINGBOX_VERSION}
```

The build-time pins (`singbox_build_version`, `singbox_build_tags`,
`singbox_build_go_version`, `singbox_cronet_version`) are centralised in
`molecule/shared/base.yml` under `provisioner.inventory.group_vars.all`,
shared by both the `singbox-stress` and `anytls-stress` scenarios (which
symlink the one `molecule/shared/Dockerfile.j2`). Molecule loads those
group_vars into both the create play (which renders `Dockerfile.j2`) and
the converge / verify plays, so the Dockerfile picks them up via Jinja2
at template time. That base.yml block also carries the full "where to
look to refresh these" comment (SFA `version.properties`, sing-box
`DEFAULT_BUILD_TAGS`/`go.mod`, cronet-go releases). Bumping SFA → bump
those vars and run `molecule destroy` + `molecule converge` to rebuild.
The `with_naive_outbound` build pulls in `github.com/sagernet/cronet-go`;
the `with_purego` tag makes it dlopen `libcronet.so` at runtime, so the
image builds `CGO_ENABLED=0` (a static Go binary) and downloads the
matching `libcronet.so` from the cronet-go release into `/usr/local/lib`.

#### Running

```bash
cd naive_proxy/molecule

# converge once, run the sing-box stress reproducer, keep instance alive
make singbox-stress-podman-converge
make singbox-stress-podman-verify

# raise concurrency to chase the bug locally — bump `iperf_parallel`
# in molecule/shared/vars/benchmark.yml (default 16) to 32+
```

#### Standalone sing-box benchmark

```bash
cd naive_proxy/molecule
make singbox-stress-podman-converge

INV=/home/verstak/.ansible/tmp/molecule.<id>.singbox-stress/inventory
ANSIBLE_COLLECTIONS_PATH=/media/data/git/ansible-v2/collections \
  ansible-playbook -i "$INV" shared/singbox-benchmark.yml
```

#### Failure conditions

The stress task fails when any of the following appears in the sing-box
client journal:

- `stream failed: http2 protocol error`
- `connection upload closed: http2 protocol error`
- `connection download closed: http2 protocol error`
- `unexpected EOF`
- `ERR_PROXY`
- `ERR_TUNNEL`

It also fails when iperf3 returns no JSON, when iperf3 reports
per-stream errors, or when measured throughput drops below
`singbox_iperf_min_bps` (default 1 Mbps — high enough to detect a stall,
low enough to avoid flagging slow CI nodes).

The official-naive benchmark in `default` and `debian-bookworm`
remains the control test; both should pass on a healthy stack.

### Standalone Runtime Image Refresh Test

```bash
cd naive_proxy/molecule
make default-podman-converge

INV=/home/verstak/.ansible/tmp/molecule.<id>.default/inventory
ANSIBLE_COLLECTIONS_PATH=/media/data/git/ansible-v2/collections \
  ansible-playbook -i "$INV" shared/utils.yml
```

## Limitations

- TLS-ALPN-01 still requires public reachability from the CA to port `443`; if you listen elsewhere, you need upstream forwarding to that port. This applies to both domains — HAProxy routes each domain's `acme-tls/1` challenge to its owner (`acme.sh` for naive, sing-box for AnyTLS) by SNI.
- **AnyTLS has no decoy site.** sing-box's AnyTLS inbound exposes no fallback for unauthenticated/invalid connections, and AnyTLS auth is in-protocol (not HTTP), so HAProxy cannot route unauthenticated AnyTLS traffic to Caddy without breaking sing-box's own TLS/ACME. AnyTLS relies on a normal-looking TLS handshake plus traffic padding for camouflage instead. The decoy site applies to the naive domain only.
- The backend image is built on the target host; there is no prebuilt multi-arch image in this role.
- `naive_proxy_haproxy_notsent_lowat` is intentionally left off by default because it is a Linux-specific tuning knob that should be benchmarked on the real host first.
- `naive_proxy_decoy_upstream_url` does not rewrite response bodies or `Location` headers. Absolute URLs and redirects from the upstream site continue to point at the upstream's real domain, which leaks the decoy origin to the client. For static-style upstreams with relative links this is usually fine; for anything dynamic, prefer an upstream you control or a custom local decoy page. **TODO**: optional response rewriting via the [`caddy-replace-response`](https://github.com/caddyserver/replace-response) plugin (requires building a custom Caddy image with `xcaddy`).

## License

Apache-2.0
