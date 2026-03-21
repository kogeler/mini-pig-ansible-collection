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
| `telemt_config_dir` | `/etc/telemt` | Host directory for config file |
| `telemt_container_config_path` | `/run/telemt/config.toml` | Config path inside container |

### Network

| Variable | Default | Description |
|---|---|---|
| `telemt_listen_port` | `443` | Main proxy listen port |
| `telemt_network_mode` | `""` (bridge) | Podman network mode; set to `"host"` to skip port publishing |

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

### Fake TLS / anti-censorship

| Variable | Default | Description |
|---|---|---|
| `telemt_tls_mask` | `true` | TCP-splice unrecognized connections to real web server |
| `telemt_mask_host` | `""` | Backend host for mask relay. If empty, telemt resolves `tls_domain` via DNS |
| `telemt_mask_port` | `443` | Backend port for mask relay |
| `telemt_tls_emulation` | `true` | Emulate real TLS record lengths |
| `telemt_tls_front_dir` | `tlsfront` | Cache directory for TLS emulation data |

When `telemt_tls_mask` is enabled, connections without a valid secret are TCP-spliced (raw bytes, no TLS termination) to the mask backend. The censor sees a real certificate and real content from the impersonated site.

If `telemt_mask_host` is empty, telemt resolves `telemt_domain` via DNS and proxies there. **If your domain's DNS points to the proxy server itself, set `telemt_mask_host` to the real IP of the impersonated site to avoid a loop.**

### Users

| Variable | Default | Description |
|---|---|---|
| `telemt_users` | `{}` | Dict of `name: secret` (**required**, at least one) |

### API

| Variable | Default | Description |
|---|---|---|
| `telemt_api_enabled` | `true` | Enable REST API inside the container |
| `telemt_api_bind` | `127.0.0.1` | Host-side bind when API is published |
| `telemt_api_port` | `9091` | API port |
| `telemt_api_whitelist` | `[]` | CIDR whitelist for API access (empty = upstream default) |
| `telemt_publish_api` | `false` | Publish API port on the host |

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

Container runs with podman default capabilities which include `NET_BIND_SERVICE` (needed for port 443). `--read-only` and `--security-opt=no-new-privileges` are hardcoded. To add extra hardening (e.g. `--cap-drop=ALL --cap-add=NET_BIND_SERVICE`), use `telemt_extra_args`.

### Extra options

| Variable | Default | Description |
|---|---|---|
| `telemt_extra_env` | `{}` | Additional environment variables |
| `telemt_extra_volumes` | `[]` | Additional volume mounts |
| `telemt_extra_args` | `[]` | Additional podman run arguments |
| `telemt_rust_log` | `""` | RUST_LOG environment variable |
| `telemt_use_middle_proxy` | `true` | Use Telegram middle proxy infrastructure |

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

### Extra container hardening

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_extra_args:
  - "--cap-drop=ALL"
  - "--cap-add=NET_BIND_SERVICE"
```

### Expose API externally (use with caution)

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_publish_api: true
telemt_api_bind: "0.0.0.0"
telemt_api_whitelist:
  - "10.0.0.0/8"
```

### Expose Prometheus metrics

```yaml
telemt_domain: "example.org"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
telemt_publish_metrics: true
telemt_metrics_bind: "127.0.0.1"
```

### Host network mode

```yaml
telemt_domain: "example.org"
telemt_network_mode: "host"
telemt_users:
  main: "0123456789abcdef0123456789abcdef"
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
# Status
systemctl status podman-telemt.service

# Logs
journalctl -u podman-telemt.service -f

# Restart
systemctl restart podman-telemt.service

# Stop
systemctl stop podman-telemt.service
```

## Proxy links

The role prints ready-to-use `tg://proxy` links for each user and each enabled mode at the end of the play. Links use `telemt_domain` as the server address.

| Mode | Secret format |
|---|---|
| TLS (Fake TLS) | `ee` + secret + hex-encoded domain |
| Secure | `dd` + secret |
| Classic | secret only |

Example output (TLS mode):

```
ok: [proxy-1] => (item=main) =>
  msg: >-
    [main] tg://proxy?server=example.org&port=443&secret=ee0123456789abcdef0123456789abcdef6578616d706c652e6f7267
```

Send the link to Telegram users — they can open it directly to add the proxy.

## Idempotency

The role is fully idempotent:

- Systemd unit and config are templated — changes trigger a restart via handler.
- `flush_handlers` prevents double restart on first deploy.
- Repeated runs with unchanged variables produce no `changed` tasks.
- The container is not recreated unless the unit file or config changes.

## License

Apache-2.0
