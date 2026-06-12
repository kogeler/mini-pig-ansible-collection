# iptables

Ansible role that programs the host firewall through one of two backends:

- **scoped iptables backend** (default): role-owned `MPIG-*` chains in
  `filter` and `nat` are loaded by `mini-pig-firewall.service` (which
  invokes the rule-rendering apply script) and the built-in
  `INPUT`/`FORWARD`/`OUTPUT`/`PREROUTING`/`POSTROUTING` chains receive
  append-only jump anchors. Foreign rules from Kubernetes / Docker /
  libvirt / Podman / CNI plugins / local operators are not touched.
- **nftables backend** (`iptables_use_nftables: true`): role-owned
  `table ip mpig_filter` / `table ip6 mpig_filter` / `table ip mpig_nat`
  rendered into `/etc/nftables.conf`, loaded via a flat `nft -f`. Each
  managed table is wrapped in the `add table … ; delete table … ;
  table … { … }` idiom so the whole batch is one atomic kernel
  transaction (in-file delete-then-create, nft 1.0.6 compatible, no
  shell wrapper). The file declares only the `mpig_*` tables — it
  never `flush ruleset` and never restarts external services.

The role is opinionated: every external-facing chain (`INPUT`, `FORWARD`,
container-side `DOCKER-USER`) defaults to deny-all on the listed external
interfaces, and only the rules generated from role variables are allowed.
Outbound traffic is permitted by default and can optionally be locked down
against RFC1918 / ULA destinations.

## Features

- IPv4 + IPv6 ruleset rendered from a single set of variables (works in both backends)
- Per-port `INPUT` allow list with optional source CIDR restriction and IPv6 skip
- `DOCKER-USER` chain rendered automatically when `docker.service` is present, so containers published with `-p` are not exposed past the firewall
- DNAT + matching `FORWARD` allow via `iptables_forwarded_ports` (with a FORWARD-only variant when `dst_*` is omitted)
- Static SNAT rules via `iptables_snat_rules` (rendered ahead of the masquerade block in `MPIG-POSTROUTING` (scoped iptables backend) / `chain postrouting` of `table ip mpig_nat` (nftables backend))
- Randomized SNAT pool over multiple external IPs via a native nft chain
  in its own table (`table ip mpig_randomized_snat`) at
  `priority srcnat - 10`. Backend-independent: the kernel evaluates this
  chain BEFORE every iptables-nft chain at `srcnat`, so randomized-pool
  traffic is rewritten and terminated before kube-proxy (k8s), Kilo CNI,
  or any other manager at `srcnat` sees the packet. Replaces the old
  timer-based mechanism — no recurring re-insert, no first-match race
- External-interface `MASQUERADE` for k8s / NAT gateway hosts
- `net.ipv4.ip_forward` sysctl toggle
- Optional rate-limited ICMP echo-request / echo-reply on the external interfaces
- Optional egress-to-local-networks blocking (RFC1918 + CGNAT for IPv4, ULA + link-local for IPv6) with per-range exceptions
- Seamless one-way migration `iptables → nftables` via `iptables_use_nftables: true`. The reverse direction is intentionally rejected

## Requirements

- Debian-based target host (`apt`, `iptables`)
- `community.general` and `ansible.posix` collections
- root privileges (rule restore + sysctl)

### Supported OS / nft version

| Backend | Distros |
|---|---|
| `iptables_use_nftables: false` (default) | Every Debian/Ubuntu the `iptables` CLI supports |
| `iptables_use_nftables: true` | Debian 12 (bookworm, nft 1.0.6) / Ubuntu 22.04 (jammy) and newer. The role uses the in-file `add table; delete table; table {…}` idiom for atomic delete-then-create (not `destroy table`, which 1.0.6 rejects), so a flat `nft -f` is one kernel transaction on the bookworm baseline. Both backends also need the `nftables` package because the randomized SNAT module is native nft (loaded into `table ip mpig_randomized_snat` at `priority srcnat - 10`) |

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
| `iptables_use_nftables` | `false` | Backend selector: `false` = scoped iptables, `true` = native nftables |

### Backend-specific value syntax

Address, port, and interface values are written **verbatim** into the
selected backend's native ruleset — the role does **not** translate between
the iptables and nftables dialects. Write every value in the syntax of the
backend you selected with `iptables_use_nftables`:

| Value kind | scoped iptables (`false`) | nftables (`true`) |
|---|---|---|
| Multiple source/destination CIDRs | bare comma list: `10.0.0.0/8,192.0.2.0/24` | nft set: `{ 10.0.0.0/8, 192.0.2.0/24 }` |
| Port range | colon: `60000:61000` | dash: `60000-61000` |
| Multiple ports in one entry | not supported — use one `iptables_ports` entry per port | nft set: `{ 80, 443 }` |
| Interface wildcard | trailing `+`: `eth+` | trailing `*`: `eth*` |

A single CIDR, single port, or plain interface name is identical on both
backends — only multi-value / range / wildcard forms differ. Using the wrong
dialect for the active backend is rejected at the start of the role (it would
otherwise fail the ruleset apply with a cryptic parser error, or — for an
interface wildcard — load silently and match nothing). The role also fails
fast on structurally invalid entries (a `port` without `protocol`, a DNAT
`dst_address` without `dst_port`, a SNAT `src_port`/`dst_port` without
`protocol`).

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
| `iptables_snat_rules` | `[]` | Static SNAT rules rendered into `MPIG-POSTROUTING` (scoped iptables backend) or `chain postrouting` of `table ip mpig_nat` (nftables backend), ahead of the generic masquerade block so the narrow match terminates first. Validated at apply time |

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
| `iptables_randomized_ext_ips` | `[]` | External IPs to spread outbound TCP/80,443 traffic across. When non-empty, the role installs a native nft chain in `table ip mpig_randomized_snat` at `priority srcnat - 10` that SNATs each new connection to a random pool IP |

The chain is loaded from `/etc/nftables.d/mpig-randomized-snat.conf` by `mpig-randomized-snat.service` (`Type=oneshot`, no `RemainAfterExit`). Each `systemctl start` re-runs `ExecStart`, which is a flat `nft -f` on that file — the conf carries the in-file `add table; delete table; table {…}` idiom so the apply is one atomic kernel transaction (delete-then-create in a single commit, no shell wrapper). Selection uses `numgen random mod N map { ... }` so the connection-NEW SNAT decision is uniformly distributed across the pool — no `-m statistic --mode random --probability` weighting.

The unit is fully decoupled from `nftables.service`: no `PartOf=` / `ReloadPropagatedFrom=`. Drift recovery (e.g. an `apt upgrade nftables` postinst that restarted the parent and lost our chain) is driven by `mpig-randomized-snat.timer`, which fires `mpig-randomized-snat.service` on the same `iptables_drift_check_interval` cadence as `mini-pig-firewall.timer`. To temporarily disable randomization from another role, stop the timer first (so it can't re-arm), then delete the table; reverse the order to resume:

```bash
systemctl stop mpig-randomized-snat.timer
nft delete table ip mpig_randomized_snat 2>/dev/null || true
# … window of no randomization …
systemctl start mpig-randomized-snat.timer
systemctl start mpig-randomized-snat.service
```

### Drift check

| Variable | Default | Description |
|---|---|---|
| `iptables_drift_check_interval` | `10` | Minutes between re-assertion runs. Drives two independent timers: `mini-pig-firewall.timer` (re-asserts MPIG state on the active backend) and, when `iptables_randomized_ext_ips` is non-empty, `mpig-randomized-snat.timer` (re-applies the randomized SNAT chain). Idempotent — only diverged hosts pay the reload cost. Set to `0` to disable both timers |

The unified service + timer doubles as the drift-check safety net for the active firewall backend (operator runs `iptables -F MPIG-INPUT`, a package postinst flushes nft state, etc.). `mpig-randomized-snat.timer` is the corresponding safety net for the SNAT chain. Each timer fires its own service — same code path the role invokes at apply time and at boot, no special drift-check binary.

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

When `/lib/systemd/system/docker.service` is present, the scoped iptables backend (via `/usr/local/sbin/mini-pig-firewall-apply`) manages a `MPIG-DOCKER-USER` chain that hangs off Docker's `DOCKER-USER`. The chain is populated from `iptables_docker_ports` and followed by a default `DROP` on the listed external interfaces, so containers published with `-p` are not exposed past the firewall. Docker itself is never restarted: the apply script touches only role-owned chains and the anchor jump, leaving Docker's own `DOCKER` / `DOCKER-ISOLATION-*` chains alone.

If Docker is not installed, the `MPIG-DOCKER-USER` chain is not rendered — the `stat` on the unit file is the only signal.

## Idempotency

- Backend templates re-render only when input variables change.
- Scoped iptables backend: the apply script uses `ensure_chain` (create-then-flush) + `ensure_anchor` (`-C` before `-A`) so re-applies with the same inputs produce no `changed`.
- nftables backend: `/etc/nftables.conf` is re-rendered only when variables change, and the apply step's `changed_when` is gated on the template's `changed`. Every apply is a flat `nft -f` against the rendered file; the in-file `add table; delete table; table {…}` idiom (see AGENTS.md pitfall P4) gives atomic delete-then-create within a single kernel transaction, keeping kernel state in lock-step with the file without ever flushing the global ruleset.
- Randomized SNAT: the service unit is `Type=oneshot` without `RemainAfterExit`, so each `systemctl start` re-fires `ExecStart` (flat `nft -f /etc/nftables.d/mpig-randomized-snat.conf`; the conf carries the in-file `add table; delete table; table {…}` idiom so the apply is one atomic kernel transaction). Ansible's `changed` reporting is gated on the managed config (the conf or the unit file) actually changing.
- Drift-check timers (`mini-pig-firewall.timer`, `mpig-randomized-snat.timer`) restart only when their unit files change.

## Migration

The role supports two migration paths:

1. **legacy `iptables-persistent` → scoped iptables** (first apply on a host coming from the old layout).
2. **scoped iptables → nftables** (flip `iptables_use_nftables: false → true`).

Both paths perform an **atomic, one-shot wipe** of the role-owned iptables-nft tables (`ip filter`, `ip nat`, `ip6 filter`; `mangle` is left untouched). The wipe replaces the contents in a single `iptables-restore` / `ip6tables-restore` transaction so the host is never observed without firewall coverage. Steady-state applies after the migration never wipe again — the surgical apply script (or the nft delete-then-load) is used instead.

> ⚠️ **The wipe also removes foreign chains in the affected iptables-nft tables** (`KUBE-FORWARD`, `KUBE-POSTROUTING`, `DOCKER`, `DOCKER-USER`, `DOCKER-ISOLATION-*`, libvirt, podman/netavark, operator hand-written chains, …). This is the accepted one-time migration cost — the same trade-off both paths take.
>
> If the host runs background managers that maintain their own iptables/nftables rules (kube-proxy, dockerd, libvirt, podman, custom systemd units), **restart those services after migration** to force an immediate reconcile. Most of them self-heal on their next periodic pass anyway, but a restart removes the window of partially-restored foreign state.
>
> For the lazy-but-safe option: **reboot the host** after migration completes. All managers come up clean and rebuild their iptables-nft state from scratch.
>
> The reverse direction (nftables → iptables) is intentionally rejected by the role's marker guard.

## Molecule

The role ships with three Molecule scenarios driven by `molecule/Makefile`.

### Scenarios

| Scenario | Driver | Purpose |
|----------|--------|---------|
| `default` | podman | Debian 12 (bookworm); exercises the scoped iptables backend, multiple `iptables_inf_ext` shapes, all four `iptables_snat_rules` template branches, the native nft randomized-SNAT chain at `priority srcnat - 10`, the drift-check timer, and a packet-level distribution probe (N=20 SNAT-routed TCP connects to a listener inside the peer netns — both pool IPs must appear, peer source must never leak through) |
| `scoped_migration` | podman | Debian 12 (bookworm); starts from the legacy iptables-persistent layout (with the old `iptables-custom-rules.timer/.service` + script) and proves the first apply migrates cleanly to scoped MPIG state while a second apply preserves foreign `KUBE-*` chains |
| `nftables` | podman | Debian 12 (bookworm); walks iptables backend → nftables migration → idempotent re-apply → changed-ruleset re-apply → forbidden reverse migration. Asserts ownership of `mpig_*` tables, decoupled SNAT drift recovery (delete table → fire service → chain restored), `nftables.service` reload independence (SNAT chain survives because no `PartOf=`/`ReloadPropagatedFrom=` coupling), absence of `destroy table` in the rendered config, and randomized-SNAT preemption |

The default scenario snapshots the rendered scoped apply script after each transition; the nftables scenario snapshots `/etc/nftables.conf` and `nft list ruleset`; the scoped_migration scenario asserts a clean migration. Always run via the Makefile — it pins `ANSIBLE_COLLECTIONS_PATH` to the in-repo install dir so a stale `~/.ansible/collections/` snapshot cannot shadow the version under test.

Idempotence is intentionally disabled for `default` and `nftables`: their converge plays walk through multiple role-variable transitions, which legitimately re-renders rules.

### Running tests

```bash
cd roles/iptables/molecule
make help

make default-podman-test
make scoped-migration-podman-test
make nftables-podman-test

make default-podman-converge
make default-podman-verify
make default-podman-login
```

## License

Apache-2.0
