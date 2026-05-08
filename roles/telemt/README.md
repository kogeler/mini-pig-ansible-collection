# telemt

Ansible role for deploying [telemt](https://github.com/telemt/telemt) — a Rust MTProxy for Telegram — in a Podman container managed by systemd.

By default, the role starts telemt in **Fake TLS** mode (`tls = true`), which is the recommended anti-censorship deployment.

## Requirements

- Debian-based OS (apt)
- Root access (systemd unit deployed system-wide)

## Quick start

```yaml
- hosts: proxy
  roles:
    - role: kogeler.mini_pig.telemt
      vars:
        telemt_domain: "example.org"
        telemt_users:
          main: "0123456789abcdef0123456789abcdef"
```

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
| `telemt_image_tag` | `latest` | Image tag |

### Paths

| Variable | Default | Description |
|---|---|---|
| `telemt_config_dir` | `/opt/telemt` | Host directory for config file |
| `telemt_container_config_path` | `/run/telemt/config.toml` | Config path inside container |

### Network

| Variable | Default | Description |
|---|---|---|
| `telemt_listen_port` | `443` | Main proxy listen port (published via pod) |

### Proxy modes

| Variable | Default | Description |
|---|---|---|
| `telemt_modes_classic` | `false` | Enable classic MTProto mode |
| `telemt_modes_secure` | `false` | Enable secure (`dd` prefix) mode |
| `telemt_modes_tls` | `true` | Enable Fake TLS (`ee` prefix) mode |

At least one mode must be enabled.

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
| `telemt_decoy_image_tag` | `latest` | Caddy image tag |
| `telemt_decoy_domain` | `""` | Domain for Let's Encrypt cert (defaults to `telemt_domain`) |
| `telemt_decoy_acme_email` | `""` | ACME email for Let's Encrypt (optional) |
| `telemt_decoy_index_html` | `""` | Path to custom `index.html` for decoy site. When empty, the role uses its built-in stub page |
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
telemt_image_tag: "3.3.24"
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
telemt_image_tag: "3.3.24"
telemt_publish_metrics: true
telemt_users:
  alice: "0123456789abcdef0123456789abcdef"
  bob: "fedcba9876543210fedcba9876543210"
```

## Service management

```bash
# Pod status
systemctl status podman-telemt-pod.service

# Telemt status
systemctl status podman-telemt.service

# Decoy status
systemctl status podman-telemt-decoy.service

# Logs
journalctl -u podman-telemt.service -f

# Restart (restarts the container, pod stays up)
systemctl restart podman-telemt.service

# Stop everything (pod + containers)
systemctl stop podman-telemt-pod.service
```

## Proxy links

The role prints ready-to-use `tg://proxy` links for each user and each enabled mode at the end of the play. By default `server=` is set to `telemt_domain`. Set `telemt_link_endpoints` to a map of `label: ip` to emit one link per user per endpoint — the SNI embedded in the Fake TLS secret remains `telemt_domain`, only the connect address changes.

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

## Idempotency

The role is fully idempotent:

- Systemd units and config are templated — changes trigger a restart via handlers.
- Pod unit changes cascade to dependent containers (decoy and telemt).
- `flush_handlers` prevents double restart on first deploy.
- Repeated runs with unchanged variables produce no `changed` tasks.
- The container is not recreated unless the unit file or config changes.

## License

Apache-2.0
