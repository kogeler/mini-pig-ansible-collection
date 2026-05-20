# iptables

Ansible role that renders a complete `iptables-save`-format ruleset for IPv4 and IPv6 into `/etc/iptables/rules.v{4,6}`, restores it through `community.general.iptables_state`, and persists it across reboots via `iptables-persistent`.

The role is opinionated: every external-facing chain (`INPUT`, `FORWARD`, container-side `DOCKER-USER`) defaults to deny-all on the listed external interfaces, and only the rules generated from role variables are allowed. Outbound traffic is permitted by default and can optionally be locked down against RFC1918 / ULA destinations.

## Features

- IPv4 + IPv6 ruleset rendered from a single set of variables
- Per-port `INPUT` allow list with optional source CIDR restriction and IPv6 skip
- `DOCKER-USER` chain rendered automatically when `docker.service` is present, so containers published with `-p` are not exposed past the firewall
- DNAT + matching `FORWARD` allow via `iptables_forwarded_ports` (with a FORWARD-only variant when `dst_*` is omitted)
- Static SNAT rules via `iptables_snat_rules` (rendered into `nat POSTROUTING`)
- Randomized SNAT pool over multiple external IPs via a systemd timer (`iptables-custom-rules.timer`), re-balancing weighted SNAT rules across all configured IPs on a tunable interval
- External-interface `MASQUERADE` for k8s / NAT gateway hosts
- `net.ipv4.ip_forward` sysctl toggle
- Optional rate-limited ICMP echo-request / echo-reply on the external interfaces
- Optional egress-to-local-networks blocking (RFC1918 + CGNAT for IPv4, ULA + link-local for IPv6) with per-range exceptions

## Requirements

- Debian-based target host (`apt`, `iptables-persistent`)
- `community.general` and `ansible.posix` collections
- root privileges (rule restore + sysctl)

## External interface selection

Most rules are scoped to one or more "external" interfaces. `iptables_inf_ext` accepts three shapes:

| Value | Result |
|---|---|
| String (e.g. `"eth0"`) | Single interface |
| List (e.g. `["eth0", "wg0"]`) | All listed interfaces |
| Empty string, empty list, or unset | Falls back to `ansible_facts['default_ipv4']['interface']` |

The normalization happens in `vars/main.yml` so include_role overrides — including the empty fall-back shapes — take effect on every render.

## Quick start

```yaml
- hosts: edge
  become: true
  roles:
    - role: kogeler.mini_pig.iptables
      vars:
        iptables_ports:
          - port: 22
            protocol: tcp
          - port: 443
            protocol: tcp
```

This deploys the default deny-all posture on the host's default IPv4 interface, with SSH and HTTPS allowed in.

## Role variables

### General

| Variable | Default | Description |
|---|---|---|
| `iptables_inf_ext` | `ansible_facts['default_ipv4']['interface']` | External interface(s). String, list, or empty/unset (fall back to default IPv4 interface) |

### Inbound ports

| Variable | Default | Description |
|---|---|---|
| `iptables_ports` | `[{port: 22, protocol: tcp}]` | List of `INPUT` allow entries on each external interface |

Each entry supports:

| Field | Default | Description |
|---|---|---|
| `port` | — | TCP/UDP port (required) |
| `protocol` | — | `tcp` or `udp` (required) |
| `src_v4` | `0.0.0.0/0` | IPv4 source CIDR restriction |
| `src_v6` | `::/0` | IPv6 source CIDR restriction |
| `skip_v6` | `false` | Skip rendering an IPv6 rule for this port |

### Docker chain

| Variable | Default | Description |
|---|---|---|
| `iptables_docker_ports` | `[]` | Per-port allow list rendered into `DOCKER-USER` when `/lib/systemd/system/docker.service` is present. Without an entry, all container-published ports are dropped on the listed external interfaces |

Each entry mirrors `iptables_ports` (`port`, `protocol`, optional `src_v4`). The role detects Docker via a `stat` on the unit file and only emits the chain when the unit exists.

### Port forwarding (DNAT + FORWARD)

| Variable | Default | Description |
|---|---|---|
| `iptables_forwarded_ports` | `[]` | DNAT rules with matching `FORWARD` allow, or FORWARD-only when `dst_*` is omitted |

Each entry supports:

| Field | Default | Description |
|---|---|---|
| `forwarded_inf` | — | Ingress interface (required) |
| `forwarded_port` | — | Inbound destination port (required) |
| `protocol` | — | `tcp` or `udp` (required) |
| `dst_address` | unset | When set, enables DNAT to this address. When omitted, the rule is `FORWARD`-only (no DNAT) |
| `dst_port` | — | Required when `dst_address` is set |
| `src_v4` | `0.0.0.0/0` | Source CIDR restriction (FORWARD-only variant) |

DNAT entries also add a matching `MASQUERADE` rule on `POSTROUTING` for the destination address, so return traffic is rewritten correctly.

### Static SNAT

| Variable | Default | Description |
|---|---|---|
| `iptables_snat_rules` | `[]` | Static SNAT rules rendered into `nat POSTROUTING`. Validated at apply time |

Each entry supports:

| Field | Default | Description |
|---|---|---|
| `to_source` | — | `--to-source` value (required) |
| `output_inf` | unset | Render `-o <iface>` when set; when omitted, matches every output interface |
| `dst_address` | unset | Destination CIDR scope |
| `protocol` | unset | `tcp` or `udp`. Required when `src_port` or `dst_port` is set |
| `src_port` | unset | Source port match |
| `dst_port` | unset | Destination port match |

Independent of the randomized SNAT pool — match scopes prevent overlap. Validation runs in `tasks/main.yml` and fails the play with the offending entry index if `to_source` is missing or `protocol` is required but absent.

### Randomized SNAT pool

| Variable | Default | Description |
|---|---|---|
| `iptables_randomized_ext_ips` | `[]` | External IPs to spread outbound TCP/80,443 traffic across. When non-empty, the role installs a systemd timer that re-balances weighted SNAT rules across all listed IPs |
| `iptables_randomized_ext_ips_timer` | `5` | Re-balance interval in minutes (`OnCalendar=*:0/N`) |

The script (`/usr/local/bin/reload_iptables_custom_rules.sh`) walks the IP list, deletes any existing matching rules, then re-inserts them with `-m statistic --mode random --probability` weights so the connection-NEW SNAT decision is uniformly distributed across the pool. The last IP in the list catches the remainder unconditionally.

### Egress and forwarding toggles

| Variable | Default | Description |
|---|---|---|
| `iptables_ipv4_forward_enable` | `false` | Sets `net.ipv4.ip_forward=1` via `ansible.posix.sysctl`. Required for DNAT / k8s nodes |
| `iptables_ext_inf_masquerade` | `false` | Add `-j MASQUERADE` for every packet leaving each external interface. Use on k8s nodes and NAT gateways |
| `iptables_disable_local_output` | `false` | Block egress to RFC1918 + CGNAT (IPv4) and ULA + link-local (IPv6) from each external interface |
| `iptables_disable_local_excluded_ipv4_ranges` | `[]` | IPv4 exceptions allowed through even when local-output blocking is on |
| `iptables_disable_local_excluded_ipv6_ranges` | `[]` | IPv6 exceptions allowed through even when local-output blocking is on |

### ICMP

| Variable | Default | Description |
|---|---|---|
| `iptables_external_ping_enable` | `false` | Allow ICMP echo-request / echo-reply on each external interface, rate-limited per source IP |
| `iptables_external_ping_limit` | `10` | Per-source-IP echo rate cap (per minute) |

When disabled, ICMP echo on the external interface(s) is dropped. Internal interfaces are unaffected.

## Tags

The whole role runs under a single tag:

| Tag | Scope |
|---|---|
| `iptables` | Entire role |

## Examples

### SSH only on the default interface

```yaml
iptables_ports:
  - port: 22
    protocol: tcp
```

### SSH from a bastion subnet plus public HTTPS

```yaml
iptables_ports:
  - port: 22
    protocol: tcp
    src_v4: "10.0.0.0/24"
    skip_v6: true
  - port: 443
    protocol: tcp
```

### k8s node — IPv4 forward + masquerade

```yaml
iptables_inf_ext: "eth0"
iptables_ipv4_forward_enable: true
iptables_ext_inf_masquerade: true
iptables_ports:
  - port: 22
    protocol: tcp
  - port: 6443
    protocol: tcp
```

### DNAT one public port to an internal host

```yaml
iptables_ipv4_forward_enable: true
iptables_forwarded_ports:
  - forwarded_inf: eth0
    forwarded_port: 8443
    protocol: tcp
    dst_port: 443
    dst_address: 192.168.1.10
```

### FORWARD-only allow (no DNAT) for a VPN ingress

```yaml
iptables_ipv4_forward_enable: true
iptables_forwarded_ports:
  - forwarded_inf: wg0
    forwarded_port: 443
    protocol: tcp
    src_v4: 10.0.0.0/8
```

### Static SNAT — pin WireGuard egress to a specific source IP

```yaml
iptables_snat_rules:
  - to_source: 203.0.113.10
    protocol: udp
    dst_port: 51820
```

### Randomized SNAT pool over three external IPs

```yaml
iptables_randomized_ext_ips:
  - 203.0.113.10
  - 203.0.113.11
  - 203.0.113.12
iptables_randomized_ext_ips_timer: 5
```

### Restrict outbound to LAN, with one exception

```yaml
iptables_disable_local_output: true
iptables_disable_local_excluded_ipv4_ranges:
  - 10.20.0.0/16
```

### Multiple external interfaces

```yaml
iptables_inf_ext:
  - eth0
  - wg0
iptables_ports:
  - port: 22
    protocol: tcp
```

### Docker host — only port 80 reaches containers

```yaml
iptables_ports:
  - port: 22
    protocol: tcp
iptables_docker_ports:
  - port: 80
    protocol: tcp
```

## Docker integration

When `/lib/systemd/system/docker.service` is present, the role:

1. Renders the `DOCKER-USER` chain with per-port allow entries from `iptables_docker_ports`, followed by a default `DROP` on the external interfaces.
2. Notifies a `restart docker` handler whenever the IPv4 or IPv6 ruleset changes — Docker's own chains (`DOCKER`, `DOCKER-ISOLATION-*`) are flushed by `iptables-restore` and must be re-installed by `dockerd`.

If Docker is not installed, both behaviors are skipped silently — the `stat` check is the only signal.

## Idempotency

- Templates rewrite the rule files only when input changes.
- `community.general.iptables_state` applies the saved rules via `iptables-restore`; subsequent runs with the same input produce no `changed`.
- The Docker handler fires only on rule changes, not on every play.
- `iptables_randomized_ext_ips_timer` ticks the SNAT re-balance script outside Ansible — repeated apply runs do not churn it.

## Molecule

The role ships with a single Molecule scenario.

### Scenarios

| Scenario | Driver | Purpose |
|----------|--------|---------|
| `default` | podman | Debian trixie in a privileged container; veth pairs in dedicated netns simulate external interfaces |

The scenario applies the role four times with different `iptables_inf_ext` shapes (string, empty string, empty list, list) and snapshots the rendered `/etc/iptables/rules.v4` after each apply. Verify reads the snapshots and asserts backward compatibility plus normalization across all four input forms. The final apply uses the list form so verify can drive real traffic through the veth peer netns against the loaded ruleset.

Idempotence is intentionally disabled: re-running converge would legitimately re-render rules as it walks the four interface shapes.

### Running tests

```bash
cd roles/iptables/molecule
make help

make default-podman-test
make default-podman-converge
make default-podman-verify
make default-podman-login
```

## License

Apache-2.0
