# kogeler.mini_pig

> A small Ansible collection for stitching together a self-hosted, bare-metal stack — proxy, VPN, monitoring, and the glue around them.
>
> The result: a not-very-big, nimble pig.

Target OS: Debian / Ubuntu. Container workloads run under **Podman + systemd** (a few legacy ones still use Docker Compose).

## What's inside

**Network & traffic**

| Role | What it does |
| --- | --- |
| [`naive_proxy`](roles/naive_proxy) | HTTP/2 anti-censorship proxy — Podman pod with HAProxy (TLS + ALPN routing), `naive` backend, Caddy decoy, and `acme.sh` renewals. |
| [`telemt`](roles/telemt) | Telegram SOCKS / MTProto proxy with Fake TLS masking, Caddy decoy, and Let's Encrypt (or Pebble) issuance. |
| [`wireguard`](roles/wireguard) | WireGuard VPN with three modes: local config generation, server instance, or MikroTik router integration. |
| [`ssl_router`](roles/ssl_router) | Nginx reverse proxy in Podman for TLS termination and HTTP routing. |
| [`iptables`](roles/iptables) | IPv4/IPv6 firewall via `iptables-persistent` (default) or scoped `nftables` tables (opt-in via `iptables_use_nftables`, live migration from the legacy backend); optional periodic external-IP rotation across multiple interfaces. |
| [`cf_ddns`](roles/cf_ddns) | Cloudflare dynamic-DNS updater running as a Podman service. |

**Observability**

| Role | What it does |
| --- | --- |
| [`monitoring`](roles/monitoring) | Grafana + InfluxDB + Matrix alert webhook deployed via Docker Compose. |
| [`telegraf`](roles/telegraf) | InfluxData Telegraf agent — system, Docker, SMART, and Raspberry Pi GPU metrics. |
| [`systemd_health_controller`](roles/systemd_health_controller) | Python Prometheus exporter that watches systemd units and restarts failures (up to 3 attempts). |

**Platform**

| Role | What it does |
| --- | --- |
| [`init`](roles/init) | Base OS bootstrap: apt, DNS, users, time sync, kernel sysctls, SSH hardening, S3 mounts, log forwarding. |
| [`docker`](roles/docker) | Installs Docker + Compose and schedules a daily `docker system prune`. |
| [`scw_k8s_kosmos_agent`](roles/scw_k8s_kosmos_agent) | Joins a node to a Scaleway Kosmos-managed Kubernetes cluster. |

## Requirements

- `ansible-core` on the control node
- A Debian / Ubuntu managed host
- Collection dependencies (auto-installed by `ansible-galaxy`):
  `containers.podman`, `community.routeros`, `community.crypto`, `community.general`, `ansible.posix`

## Installation

Add the collection to your `requirements.yml`:

```yaml
collections:
  - name: https://github.com/kogeler/mini-pig-ansible-collection.git
    type: git
    version: main
```

Install into your project (recommended):

```bash
mkdir collections
ansible-galaxy collection install -f -r requirements.yml -p ./collections
```

Or install globally into `~/.ansible/collections`:

```bash
ansible-galaxy collection install -f -r requirements.yml
```

## Usage

Reference roles with their fully-qualified name in any playbook:

```yaml
- hosts: edge
  roles:
    - kogeler.mini_pig.init
    - kogeler.mini_pig.iptables
    - kogeler.mini_pig.naive_proxy
```

Per-role variables and defaults live under each `roles/<name>/defaults/main.yml`. Roles with non-trivial deployment topology (`naive_proxy`, `telemt`) ship their own README.

## Testing

CI runs `ansible-lint` and Molecule (Podman driver) for every role whose `molecule/<scenario>/` directory contains an `ENABLE_CI` marker. The workflow ([`.github/workflows/molecule.yml`](.github/workflows/molecule.yml)) picks scenarios based on what a PR actually touches; collection-wide changes (deps, lint config, workflow) trigger the full matrix.

Roles with Molecule scenarios today: `iptables`, `naive_proxy`, `telemt`.

Run a scenario locally:

```bash
cd roles/<role>
molecule -c molecule/shared/base.yml test -s <scenario>
```

## License

Licensed under the [Apache License 2.0](LICENSE).
