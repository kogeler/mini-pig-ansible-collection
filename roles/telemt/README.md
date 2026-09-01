# telemt

Ansible role for deploying [telemt](https://github.com/telemt/telemt) — a Rust
MTProxy for Telegram — in Podman containers managed by systemd.

The default `direct` deployment preserves the historical Fake TLS setup. The
optional `web` deployment uses the WEB transport introduced in Telemt 3.5,
with HAProxy terminating public TLS and Caddy serving camouflage traffic.

The default identities are intentionally different: direct uses `telemt` and
`/opt/telemt`, while WEB uses `telemt-web` and `/opt/telemt_web`. This lets the
two topologies coexist on one host once their published sockets are distinct.

## Requirements

- Debian-based OS (apt)
- Root access (systemd unit deployed system-wide)
- A canonical FQDN resolving to the server
- WEB mode additionally requires public TCP/443, a concrete public IPv4, and
  one operator-owned decoy source

## Quick start

### Direct Fake TLS

```yaml
- hosts: proxy
  roles:
    - role: kogeler.mini_pig.telemt
      vars:
        telemt_domain: "example.org"
        telemt_users:
          main: "0123456789abcdef0123456789abcdef"
```

### WEB + HAProxy

```yaml
- hosts: proxy
  roles:
    - role: kogeler.mini_pig.telemt
      vars:
        telemt_deployment_mode: web
        telemt_domain: "proxy.example.org"
        telemt_web_public_ip: "203.0.113.10"
        telemt_acme_email: "admin@example.org"
        telemt_decoy_site_dir: "{{ playbook_dir }}/files/decoy"
        telemt_users:
          alice: "0123456789abcdef0123456789abcdef"
          bob: "fedcba9876543210fedcba9876543210"
        telemt_web_profiles:
          - user: alice
            secret_mode: plain
          - user: bob
            secret_mode: dd
```

The WEB topology is deliberately split into two HAProxy listeners:

```text
Internet :443
    -> HAProxy TCP/ALPN router
       |-- acme-tls/1 -> acme.sh challenge responder
       `-- TLS -> HAProxy inner TLS terminator
                    |-- Host proxy.example.org -> Telemt WEB :18080
                    `-- any other Host -> Caddy :18081

Telemt WEB invalid capability/user -> Caddy :18081
```

HAProxy only separates ACME, the canonical host, and foreign hosts. For the
canonical host, the complete request goes to Telemt. Telemt validates the WEB
capability/user and sends invalid traffic to its fallback upstream, Caddy.
This keeps credential decisions out of HAProxy and prevents it from logging
user secrets.

## Colocated deployments

The role does not inspect other deployments or fail preflight because a host
port, name, or directory is already in use. The inventory is the source of
truth. For every instance on the same host, make these values unique:

| Scope | Variables that must not collide |
|---|---|
| Runtime and systemd identity | `telemt_instance_name` |
| Persistent files, certificates, and rendered units' bind sources | `telemt_config_dir` |
| Public listener | The `(telemt_listen_bind, telemt_listen_port)` socket tuple |
| Public DNS and certificates | `telemt_domain` (and `telemt_decoy_domain` when explicitly set) |
| Published API | `(telemt_api_bind, telemt_api_port)` when `telemt_publish_api: true` |
| Published metrics | `(telemt_metrics_bind, telemt_metrics_port)` when `telemt_publish_metrics: true` |

`telemt_web_public_ip` must identify the public IPv4 that reaches that WEB
instance, but it does not bind a host socket. `telemt_pod_network`, image
names, ACME server, and ACME email may be shared.

WEB links always use TCP/443, so `telemt_listen_port` remains `443` in WEB
mode. A direct + WEB pair can use direct port `9443` and WEB port `443`, as in
this example. Two WEB instances need different host IPv4 addresses and
corresponding `telemt_listen_bind` values so each can own port 443.

```yaml
- hosts: proxy
  tasks:
    - name: Deploy direct instance
      ansible.builtin.include_role:
        name: kogeler.mini_pig.telemt
      vars:
        telemt_domain: direct.example.org
        telemt_listen_port: 9443
        telemt_users:
          main: "0123456789abcdef0123456789abcdef"

    - name: Deploy colocated WEB instance
      ansible.builtin.include_role:
        name: kogeler.mini_pig.telemt
      vars:
        telemt_deployment_mode: web
        telemt_domain: web.example.org
        telemt_web_public_ip: "203.0.113.10"
        telemt_decoy_site_dir: "{{ playbook_dir }}/files/web-decoy"
        telemt_users:
          main: "fedcba9876543210fedcba9876543210"
```

The defaults distinguish one direct instance from one WEB instance. For a
second instance of the same mode, explicitly set both a unique
`telemt_instance_name` and a unique `telemt_config_dir`, in addition to the
network values above.

Existing direct deployments keep their historical names. Existing WEB
deployments created before the isolated defaults can retain their old identity
during an in-place upgrade with:

```yaml
telemt_deployment_mode: web
telemt_instance_name: telemt
telemt_config_dir: /opt/telemt
```

That compatibility override cannot coexist with the default direct instance.
To adopt the new WEB identity instead, stop the old generic WEB units before
the first deployment; the role deliberately does not discover or remove them.

Generate user secrets (any of these commands produces a valid 32-char hex string):

```bash
# OpenSSL
openssl rand -hex 16

# /dev/urandom (no dependencies)
head -c 16 /dev/urandom | xxd -p

# Python one-liner
python3 -c "import secrets; print(secrets.token_hex(16))"
```

## Security model

The role applies hardening by default — no manual `telemt_extra_args` required:

- **Telemt container**: `--cap-drop=ALL`, `--cap-add=NET_BIND_SERVICE` (only when listen port < 1024), `--read-only`, `--security-opt=no-new-privileges`, tmpfs for runtime cache.
- **Decoy (Caddy) container**: `--cap-drop=ALL`, `--cap-add=NET_BIND_SERVICE` (required because the official Caddy binary has file capabilities set via `setcap`; without this, `--security-opt=no-new-privileges` blocks exec), `--read-only`, `--security-opt=no-new-privileges`.
- **API is disabled by default** (`telemt_api_enabled: false`). The API provides full control over proxy management — enable it only when needed and restrict access with `telemt_api_whitelist`.
- **Pod-based networking only**. All containers share a single pod network namespace. The pod unit is the sole point for publishing ports to the host.

## Role variables

### General

| Variable | Default | Description |
|---|---|---|
| `telemt_enabled` | `true` | Enable/disable the role |
| `telemt_image` | `ghcr.io/telemt/telemt` | Container image |
| `telemt_image_tag` | `3.5.2` | Image tag; WEB mode requires 3.5.2 or newer |
| `telemt_deployment_mode` | `direct` | `direct` or `web` topology |
| `telemt_instance_name` | `telemt` (direct), `telemt-web` (WEB) | Prefix for pod, container, systemd, handler, and ACME identities; unique per colocated instance |

### Paths

| Variable | Default | Description |
|---|---|---|
| `telemt_config_dir` | `/opt/telemt` (direct), `/opt/telemt_web` (WEB) | Isolated host directory for configuration, state, and certificates |
| `telemt_container_config_path` | `/run/telemt/config.toml` | Config path inside container |

### Network

| Variable | Default | Description |
|---|---|---|
| `telemt_listen_port` | `443` | Main proxy listen port (published via pod) |
| `telemt_listen_bind` | `""` | Optional host IPv4 for the public publish; empty binds all host addresses |
| `telemt_web_public_ip` | `""` | Public IPv4 used in WEB relay/KDF data; required in WEB mode and not a host bind setting |

### WEB transport

| Variable | Default | Description |
|---|---|---|
| `telemt_web_carrier` | `https` | Telemt WEB carrier (`https` or `https-lanes`) |
| `telemt_web_default_secret_mode` | `dd` | Profile mode when `telemt_web_profiles` is empty |
| `telemt_web_profiles` | `[]` | Per-user WEB profiles; secrets support `plain` and `dd`, not Fake TLS `ee` |
| `telemt_web_limits` | `{}` | Allowlisted positive `[web.limits]` overrides |
| `telemt_web_timeouts` | `{}` | Allowlisted positive `[web.timeouts]` overrides |
| `telemt_web_haproxy_timeout_margin_secs` | `10` | Margin added to WEB long polling for HAProxy timeouts |
| `telemt_haproxy_image_tag` | `3.4.3-alpine` | Pinned HAProxy runtime |
| `telemt_acme_image_tag` | `3.1.4` | Pinned acme.sh runtime |
| `telemt_acme_email` | `""` | ACME account email |
| `telemt_acme_server` | `letsencrypt` | acme.sh CA selector |

WEB profiles may override `max_sessions`, `max_streams`, and
`max_streams_per_session`. Every profile must reference a unique key in
`telemt_users`.

### Proxy modes

| Variable | Default | Description |
|---|---|---|
| `telemt_modes_classic` | `false` | Enable classic MTProto mode |
| `telemt_modes_secure` | `false` | Enable secure (`dd` prefix) mode |
| `telemt_modes_tls` | `true` | Enable Fake TLS (`ee` prefix) mode |

At least one legacy mode must be enabled in `direct` deployment. WEB profiles
are configured separately.

### Domain

| Variable | Default | Description |
|---|---|---|
| `telemt_domain` | `""` | Server domain name (**required**). Used in proxy links and as `tls_domain` in Fake TLS mode |

### Link endpoints

| Variable | Default | Description |
|---|---|---|
| `telemt_link_endpoints` | `{}` | Map of `label: ip` (or hostname) used as `server=` in printed `tg://proxy` links. When non-empty, one link per user × endpoint is emitted; the SNI in the Fake TLS secret stays bound to `telemt_domain`. When empty, a single link per user is printed with `server=telemt_domain` |

### Fake TLS / anti-censorship

| Variable | Default | Description |
|---|---|---|
| `telemt_tls_mask` | `true` | TCP-splice unrecognized connections to real web server |
| `telemt_tls_emulation` | `true` | Emulate real TLS record lengths |
| `telemt_tls_front_dir` | `tlsfront` | Cache directory for TLS emulation data |

When `telemt_tls_mask` is enabled, connections without a valid secret are TCP-spliced (raw bytes, no TLS termination) to the decoy Caddy container running in the same pod (`127.0.0.1:8443`). The censor sees a real certificate and real content served by Caddy.

### Users

| Variable | Default | Description |
|---|---|---|
| `telemt_users` | `{}` | Dict of `name: secret` (**required**, at least one) |

### API

> **Warning:** The API provides full control over proxy management (add/remove users, change config). Keep it disabled unless you have a specific need.

| Variable | Default | Description |
|---|---|---|
| `telemt_api_enabled` | `false` | Enable REST API inside the container |
| `telemt_api_bind` | `127.0.0.1` | Host-side bind when API is published |
| `telemt_api_port` | `9091` | API port |
| `telemt_api_whitelist` | `[]` | CIDR whitelist for API access (empty = upstream default) |
| `telemt_publish_api` | `false` | Publish API port on the host (opt-in) |

### Metrics

| Variable | Default | Description |
|---|---|---|
| `telemt_metrics_bind` | `127.0.0.1` | Host-side bind when metrics are published |
| `telemt_metrics_port` | `9090` | Prometheus metrics port |
| `telemt_publish_metrics` | `false` | Publish metrics port on the host |

### Container options

| Variable | Default | Description |
|---|---|---|
| `telemt_read_only_rootfs` | `true` | Read-only container root filesystem |
| `telemt_tmpfs_enabled` | `true` | Mount tmpfs at `/run/telemt` for cache |
| `telemt_selinux_relabel` | `false` | Add `:Z` SELinux relabel to volume mounts |
| `telemt_apparmor_profile` | `unconfined` | AppArmor profile passed as `--security-opt=apparmor=<value>` to every container the role manages (proxy, decoy, pebble). Default `unconfined` because Ubuntu 24.04 + podman 4.9.3 ships a generated profile that denies `socket(AF_INET, SOCK_STREAM)` for confined containers — leaving the proxy unable to open TCP sockets. Defense-in-depth still has `--cap-drop=ALL`, `--read-only`, `--security-opt=no-new-privileges`, and pod-level network isolation. Override to a specific profile name on hosts that ship a custom AppArmor policy that allows inet socket creation, or set to empty string `""` to drop the flag entirely (then podman applies whatever default profile it has) |

### Extra options

| Variable | Default | Description |
|---|---|---|
| `telemt_extra_env` | `{}` | Additional environment variables |
| `telemt_extra_volumes` | `[]` | Additional volume mounts |
| `telemt_extra_args` | `[]` | Additional podman run arguments (appended after built-in hardening flags) |
| `telemt_rust_log` | `""` | RUST_LOG environment variable |
| `telemt_use_middle_proxy` | `true` | Use Telegram middle proxy infrastructure |

### Decoy site

| Variable | Default | Description |
|---|---|---|
| `telemt_decoy_image` | `docker.io/library/caddy` | Caddy container image |
| `telemt_decoy_image_tag` | `2.11.4-alpine` | Pinned Caddy image tag |
| `telemt_decoy_domain` | `""` | Domain for Let's Encrypt cert (defaults to `telemt_domain`) |
| `telemt_decoy_acme_email` | `""` | ACME email for Let's Encrypt (optional) |
| `telemt_decoy_index_html` | `""` | Path to custom `index.html` for decoy site. When empty, the role uses its built-in stub page. Ignored when `telemt_decoy_upstream_url` is set |
| `telemt_decoy_site_dir` | `""` | Complete operator-owned static site directory; recommended for WEB mode |
| `telemt_decoy_upstream_url` | `""` | When set (e.g. `https://example.com`), Caddy reverse-proxies splice-spliced unauthenticated traffic to this URL instead of serving a local static page. Caddy terminates HTTPS on the upstream side and rewrites the `Host` header to the upstream hostname. Absolute URLs and `Location` redirects from the upstream are not rewritten |
| `telemt_molecule_mode` | `false` | When true, deploys [Pebble](https://github.com/letsencrypt/pebble) (test ACME CA) into the pod and points Caddy at it via `acme_ca`. Caddy issues a real ACME cert through the same TLS-ALPN-01-through-splice path that production uses, so molecule scenarios exercise the full ACME chain. Never enable in production |

## Configuration examples

### Default Fake TLS (recommended)

```yaml
telemt_modes_classic: false
telemt_modes_secure: false
telemt_modes_tls: true
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
```

### Classic mode only

```yaml
telemt_domain: "proxy.example.org"
telemt_modes_classic: true
telemt_modes_secure: false
telemt_modes_tls: false
telemt_listen_port: 8443
telemt_users:
  user1: "0123456789abcdef0123456789abcdef"
```

### Secure mode only

```yaml
telemt_domain: "proxy.example.org"
telemt_modes_classic: false
telemt_modes_secure: true
telemt_modes_tls: false
telemt_listen_port: 8443
telemt_users:
  user1: "0123456789abcdef0123456789abcdef"
```

### Multiple modes enabled

```yaml
telemt_modes_classic: false
telemt_modes_secure: true
telemt_modes_tls: true
telemt_domain: "example.org"
telemt_users:
  user1: "0123456789abcdef0123456789abcdef"
  user2: "fedcba9876543210fedcba9876543210"
```

### Custom image tag

```yaml
telemt_image_tag: "3.5.2"
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
```

### Enable API (use with caution)

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_api_enabled: true
```

### Expose API externally (use with extreme caution)

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_api_enabled: true
telemt_publish_api: true
telemt_api_bind: "0.0.0.0"
telemt_api_whitelist:
  - "10.0.0.0/8"
```

### Custom decoy page

Place your `index.html` in the playbook's `files/` directory:

```
playbook/
├── files/
│   └── decoy-index.html
└── site.yml
```

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_decoy_index_html: "{{ playbook_dir }}/files/decoy-index.html"
```

### Expose Prometheus metrics

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_publish_metrics: true
telemt_metrics_bind: "127.0.0.1"
```

## Inventory example

```ini
[proxy]
proxy-1.example.com
proxy-2.example.com
```

```yaml
# group_vars/proxy.yml
telemt_domain: "cdn.example.org"
telemt_image_tag: "3.5.2"
telemt_publish_metrics: true
telemt_users:
  alice: "0123456789abcdef0123456789abcdef"
  bob: "fedcba9876543210fedcba9876543210"
```

## Service management

Default service identities are:

| Component | Direct | WEB |
|---|---|---|
| Pod | `podman-telemt-pod.service` | `podman-telemt-web-pod.service` |
| Telemt | `podman-telemt.service` | `podman-telemt-web.service` |
| Decoy | `podman-telemt-decoy.service` | `podman-telemt-web-decoy.service` |
| HAProxy | — | `podman-telemt-web-haproxy.service` |
| ACME timer | — | `telemt-web-acme-renew.timer` |

```bash
# Pod status
systemctl status podman-telemt-pod.service

# Telemt status
systemctl status podman-telemt.service

# Decoy status
systemctl status podman-telemt-decoy.service

# WEB ingress and certificate renewal
systemctl status podman-telemt-web-haproxy.service
systemctl status telemt-web-acme-renew.timer

# Logs
journalctl -u podman-telemt.service -f

# Stop everything (pod + containers)
systemctl stop podman-telemt-pod.service
```

Apply configuration changes through the role. Its restart handlers stop the
affected container, wait for all child cgroup processes to exit, and then start
the service again.

## Proxy links

The role prints ready-to-use links at the end of the play. Direct mode emits
`tg://proxy`; WEB mode emits `tg://webproxy?server=<domain>&secret=<secret>`.
WEB links intentionally omit `port` because the protocol uses HTTPS/443.

In direct mode, `server=` defaults to `telemt_domain`. Set
`telemt_link_endpoints` to a map of `label: ip` to emit one link per user per
endpoint — the SNI embedded in the Fake TLS secret remains `telemt_domain`.

| Mode | Secret format |
|---|---|
| TLS (Fake TLS) | `ee` + secret + hex-encoded domain |
| Secure | `dd` + secret |
| Classic | secret only |

Example output (TLS mode, default — no `telemt_link_endpoints`):

```
ok: [proxy-1] => (item=main@default) =>
  msg: >-
    [main@default] tg://proxy?server=example.org&port=443&secret=ee0123456789abcdef0123456789abcdef6578616d706c652e6f7267
```

Example output with multiple endpoints:

```yaml
telemt_domain: "example.org"
telemt_link_endpoints:
  primary: "203.0.113.10"
  backup:  "203.0.113.11"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
```

```
ok: [proxy-1] => (item=main@primary) =>
  msg: >-
    [main@primary] tg://proxy?server=203.0.113.10&port=443&secret=ee0123456789abcdef0123456789abcdef6578616d706c652e6f7267
ok: [proxy-1] => (item=main@backup) =>
  msg: >-
    [main@backup] tg://proxy?server=203.0.113.11&port=443&secret=ee0123456789abcdef0123456789abcdef6578616d706c652e6f7267
```

Send the link to Telegram users — they can open it directly to add the proxy.

## Local development and tests

The Molecule directory owns its Python environment. No external Ansible venv
or bare `molecule` command is required:

```bash
cd roles/telemt/molecule
make bootstrap
make lint

# Direct + WEB on one Debian host, including idempotence and isolation
make default-podman-converge
make default-podman-idempotence
make default-podman-verify

# The same combined deployment on a native GitHub Actions runner
make gha-native-converge
make gha-native-idempotence
make gha-native-verify
```

Use `make help` for maintenance targets. Both scenarios deploy direct and WEB
with their mode-derived default identities on the same machine. Converge
checks that applying either topology does not restart the other, and
idempotence covers the complete combined deployment.

The WEB verify flow checks certificate issuance through Pebble, ALPN, decoy routing,
wrong/malformed capabilities, session creation, uplink/downlink, and deletion
for both `plain` and `dd` profiles. It also performs a valid inner MTProxy
handshake and requires a nonce-matched Telegram `resPQ` for each profile. This
is not opt-in: both `default` and `gha` require real egress from Telemt to
Telegram middle proxies. `req_pq` is a pre-authentication
exchange, so the probe does not require a Telegram account, API ID, or API
hash. Verify exercises both public listeners and both internal health paths
while all direct and WEB units remain active.

## Idempotency

The role is fully idempotent:

- Systemd units and config are templated — changes trigger a restart via handlers.
- Pod unit changes cascade to dependent containers (decoy, Telemt, and HAProxy
  in WEB mode).
- `flush_handlers` prevents double restart on first deploy.
- Repeated runs with unchanged variables produce no `changed` tasks.
- The container is not recreated unless the unit file or config changes.

## License

Apache-2.0
