# naive_proxy

Ansible role for deploying [NaiveProxy](https://github.com/klzgrad/naiveproxy) using the official [HAProxy setup](https://github.com/klzgrad/naiveproxy/wiki/HAProxy-Setup). The role runs a small Podman pod under systemd:

- `HAProxy` on the public port for TLS termination, auth routing, and ACME ALPN dispatch
- `naive` standalone backend for authenticated CONNECT tunnels
- `Caddy` decoy site for unauthenticated traffic
- `acme.sh` renewal as a oneshot systemd service and timer

The role also includes Molecule coverage for certificate issuance, HTTPS proxy mode, SOCKS5 tunneling, and throughput benchmarking.

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
|                             |              (standalone binary)     |
|                             |                                      |
|                             +-- no auth --> Caddy decoy (:8081)    |
|                                            (static website)        |
+--------------------------------------------------------------------+
```

Traffic is differentiated by HTTP auth, not by method:

- authenticated requests go to the naive backend
- unauthenticated requests go to the decoy site

The public client side is HTTP/2 over TLS on HAProxy. The internal HAProxy -> naive backend hop is plain HTTP on `127.0.0.1:8080`.

## Requirements

- Debian-based target host
- systemd
- Podman
- root privileges
- public reachability for the listen port
- for real ACME issuance, public port `443` must reach HAProxy for TLS-ALPN-01

## Quick Start

```yaml
- hosts: proxy
  become: true
  roles:
    - role: kogeler.mini_pig.naive_proxy
      vars:
        naive_proxy_domain: "cdn.example.org"
        naive_proxy_users:
          alice: "s3cret-passw0rd"
        naive_proxy_acme_email: "admin@example.org"
```

## What The Role Deploys

### Containers

| Container | Image | Purpose |
|---|---|---|
| `naive-haproxy` | `haproxy:3.2-alpine` | Public TLS endpoint, auth routing, ACME ALPN routing |
| `naive-backend` | `localhost/naive-backend:VERSION` | Standalone naive backend built locally by the role |
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
| `naive-acme-renew.service` | ACME issue/renew oneshot |
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
| `naive_proxy_domain` | `""` | Public server FQDN |
| `naive_proxy_users` | `{}` | Dict of `name: password`, at least one user |

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
| `naive_proxy_naive_version` | `"v143.0.7499.109-2"` | Standalone naive release tag |
| `naive_proxy_padding` | `true` | Enable `--padding` on the backend |
| `naive_proxy_backend_base_image` | `"docker.io/library/ubuntu"` | Base image for the backend container build |
| `naive_proxy_backend_base_image_tag` | `"22.04"` | Base image tag |
| `naive_proxy_backend_extra_env` | `{}` | Extra environment for the backend container |
| `naive_proxy_backend_extra_volumes` | `[]` | Extra volumes for the backend container |
| `naive_proxy_backend_extra_args` | `[]` | Extra Podman arguments for the backend container |

### Images

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_haproxy_image` | `"docker.io/library/haproxy"` | HAProxy image |
| `naive_proxy_haproxy_image_tag` | `"3.2-alpine"` | Pinned HAProxy LTS major |
| `naive_proxy_decoy_image` | `"docker.io/library/caddy"` | Decoy image |
| `naive_proxy_decoy_image_tag` | `"latest"` | Decoy image tag |
| `naive_proxy_acme_image` | `"docker.io/neilpang/acme.sh"` | ACME image |
| `naive_proxy_acme_image_tag` | `"latest"` | ACME image tag |

### ACME / TLS

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_acme_email` | `""` | Optional ACME account email |
| `naive_proxy_acme_server` | `"letsencrypt"` | Explicit ACME CA passed to `acme.sh` outside `molecule_mode` |

The role defaults to `letsencrypt` explicitly so the first issuance does not depend on ZeroSSL account registration behavior inside `acme.sh`.

### Decoy Site

| Variable | Default | Description |
|---|---|---|
| `naive_proxy_decoy_index_html` | `""` | Path to a custom decoy `index.html`; empty uses the built-in stub page |

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
| `naive_proxy_haproxy_h2_frontend_rxbuf` | `""` | Optional explicit H2 frontend rx buffer; empty means auto-calculate |
| `naive_proxy_haproxy_expected_bandwidth_mbps` | `1000` | Expected symmetric bandwidth for H2 rx buffer auto-sizing |
| `naive_proxy_haproxy_expected_rtt_ms` | `100` | Expected RTT for H2 rx buffer auto-sizing |
| `naive_proxy_haproxy_notsent_lowat` | `0` | Optional Linux-only low-water mark; disabled by default |

The role defaults to a speed-first profile for dedicated VPN edges. Auto-sizing for `h2_frontend_rxbuf` is derived from bandwidth and RTT.

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
naive_proxy_users:
  alice: "pass1"
```

### Explicit ACME Email

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_acme_email: "admin@example.org"
naive_proxy_users:
  alice: "pass1"
```

### Port Forwarding In Front Of HAProxy

HAProxy may listen locally on one port while clients see another public port.

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_listen_port: 8443
naive_proxy_external_port: 443
naive_proxy_users:
  alice: "pass1"
```

### Custom Decoy Page

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_users:
  alice: "pass1"
naive_proxy_decoy_index_html: "{{ playbook_dir }}/files/decoy-index.html"
```

### Override HAProxy Tuning

```yaml
naive_proxy_domain: "cdn.example.org"
naive_proxy_users:
  alice: "pass1"
naive_proxy_haproxy_cpu_policy: "performance"
naive_proxy_haproxy_expected_bandwidth_mbps: 500
naive_proxy_haproxy_expected_rtt_ms: 50
```

### Force-Pull Runtime Images On An Existing Host

```yaml
naive_proxy_update_runtime_images: true
```

## Generated Client Configs

The role prints a `naive+https://` link for each user and writes sing-box JSON configs on the controller.

Example output:

```text
[alice] naive+https://alice:s3cret-passw0rd@cdn.example.org:443#alice
[alice] ./naive-proxy-json-configs/singbox-proxy-1-alice.json
```

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
systemctl status podman-naive-decoy.service
systemctl status naive-acme-renew.timer

# Logs
journalctl -u podman-naive-haproxy.service -f
journalctl -u podman-naive-backend.service -f

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

## Molecule

The role ships with multiple Molecule scenarios sharing common playbooks from `molecule/shared/`.

### Scenarios

| Scenario | Driver | Purpose |
|----------|--------|---------|
| `default` | podman | Local dev, podman-in-podman, Debian trixie |
| `debian-bookworm` | podman | Local dev, podman-in-podman, Debian 12 |
| `gha` | ansible-native | GitHub Actions, role applied directly to runner VM |

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

### Running Tests

```bash
cd naive_proxy

# iterative work (default scenario)
GIT_DIR=/dev/null molecule converge
GIT_DIR=/dev/null molecule verify
GIT_DIR=/dev/null molecule login

# Debian 12 scenario
GIT_DIR=/dev/null molecule converge -s debian-bookworm
GIT_DIR=/dev/null molecule verify -s debian-bookworm

# GHA scenario (localhost)
molecule converge -s gha
molecule verify -s gha
```

`GIT_DIR=/dev/null` is required for podman scenarios because `collections/` is gitignored at the repo root.

### Standalone Benchmark

The benchmark playbook runs the throughput portion without the rest of `verify`:

```bash
cd naive_proxy
GIT_DIR=/dev/null molecule converge

INV=/home/verstak/.ansible/tmp/molecule.<id>.default/inventory
ANSIBLE_COLLECTIONS_PATH=/media/data/git/ansible-v2/collections \
  ansible-playbook -i "$INV" molecule/shared/benchmark.yml
```

### Standalone Runtime Image Refresh Test

```bash
cd naive_proxy
GIT_DIR=/dev/null molecule converge

INV=/home/verstak/.ansible/tmp/molecule.<id>.default/inventory
ANSIBLE_COLLECTIONS_PATH=/media/data/git/ansible-v2/collections \
  ansible-playbook -i "$INV" molecule/shared/utils.yml
```

## Limitations

- TLS-ALPN-01 still requires public reachability from the CA to port `443`; if you listen elsewhere, you need upstream forwarding to that port.
- The backend image is built on the target host; there is no prebuilt multi-arch image in this role.
- `naive_proxy_haproxy_notsent_lowat` is intentionally left off by default because it is a Linux-specific tuning knob that should be benchmarked on the real host first.

## License

Apache-2.0
