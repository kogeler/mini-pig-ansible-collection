# telemt - Agent Context

## Rules for AI agents running Molecule

1. Use the Makefile wrapper at `molecule/Makefile`, not bare `molecule`
   commands. It injects the role-scoped base config and the `GIT_DIR=/dev/null`
   workaround needed in this collection layout.
2. Always activate the local venv before make/molecule/ansible commands:
   `source /media/data/app/python/venv3/bin/activate`
3. Avoid `make ...-test` during debugging because it destroys the instance at
   the end. Prefer separate `converge`, `idempotence`, and `verify` runs; use
   `destroy` explicitly when you need a clean slate.
4. Do not pipe Molecule output through `tail`. Redirect full logs to `/tmp`,
   then inspect them with `rg`, `grep`, `sed`, or `less`.
5. Prefer native Ansible modules over `shell`/`command`. For this role,
   legitimate command/shell cases are raw-byte TCP probes (`printf | nc`) and
   rescue-only diagnostics such as `journalctl`.

## What this role does

`telemt` deploys the Rust MTProto Fake-TLS proxy for Telegram in a Podman pod
managed by systemd. The proxy accepts Telegram MTProto client traffic on the
public listener and, when Fake-TLS masking is enabled, splices invalid or
unauthenticated TLS-looking traffic to a Caddy decoy site in the same pod.

The Molecule scenario validates the real ACME/TLS-ALPN-01 path with Pebble:
Caddy requests a certificate from Pebble, Pebble validates the TLS-ALPN-01
challenge through telemt's TCP splice, and verify polls the served certificate
until the Pebble-issued cert replaces Caddy's bootstrap cert.

## Architecture

```text
Client : telemt_listen_port
    |
    v
+--- Pod: telemt-pod --------------------------------------------------+
|                                                                      |
|  telemt (:telemt_listen_port)                                        |
|    |-- valid MTProto/Fake-TLS secret --> Telegram middle proxies     |
|    +-- invalid/vanilla TLS traffic ---> Caddy decoy (:8443)          |
|                                                                      |
|  Caddy decoy (:8443)                                                 |
|    |-- normal production mode: ACME issuer                           |
|    |-- molecule mode: Pebble ACME CA                                 |
|                                                                      |
|  Pebble (:14000 ACME, :15000 mgmt) - molecule_mode only              |
+----------------------------------------------------------------------+
```

Internal ports:

| Name | Port | Purpose |
|------|------|---------|
| `telemt_listen_port` | default `443`, molecule `9443` | public proxy listener |
| `_telemt_decoy_port` | `8443` | Caddy decoy HTTPS listener inside the pod |
| `_telemt_pebble_port` | `14000` | Pebble ACME directory, molecule only |
| `_telemt_pebble_mgmt_port` | `15000` | Pebble management endpoint, molecule only |
| `telemt_metrics_port` | default `9090` | metrics listener |
| `telemt_api_port` | default `9091` | API listener |

## Important role behavior

- The decoy always uses Caddy's ACME issuer path. Production uses the default
  public CA behavior; molecule mode overrides the global ACME CA to Pebble.
- Caddy runs with a read-only rootfs, so both `/data` and `/config` are
  writable bind mounts under `telemt_config_dir`.
- In molecule mode, Caddy's `acme_ca` is `https://localhost:14000/dir` because
  Pebble's bundled ACME endpoint certificate is valid for `localhost`.
  `acme_ca_root /etc/caddy/pebble-root.pem` points Caddy at the root that signs
  that endpoint certificate.
- Pebble's `pebble.minica.pem` is not exposed by the management API. The role
  starts the pod and Pebble before handler flush, copies
  `/test/certs/pebble.minica.pem` from the running Pebble container to a
  candidate file, compares content against the trusted host file, and updates
  `/opt/telemt/pebble-root.pem` only when it changed. This prevents stale CA
  files when a Pebble image changes and preserves idempotence.
- If Podman ever creates `pebble-root.pem` as a directory because the bind
  source was missing, the role removes that invalid bind source before
  installing the real PEM.
- `telemt_link_endpoints` is a map of `label: ip-or-hostname`. The role emits
  one `tg://proxy?...` debug link per user per endpoint. The Fake-TLS SNI tail
  always remains `telemt_domain`, even when `server=` is an endpoint IP.
- `telemt_decoy_upstream_url` switches the decoy from a local static page to a
  reverse-proxy mode. Caddy terminates upstream TLS itself and rewrites the
  request `Host` to the upstream hostname (`{upstream_hostport}`). Response
  bodies and `Location` headers are not rewritten — picking a static-style
  upstream avoids URL leaks. Mutually exclusive with `telemt_decoy_index_html`
  (which is ignored when upstream is set).
- `telemt_apparmor_profile` defaults to `unconfined` and is applied to every
  role-managed container (telemt, decoy, pebble) via `--security-opt=apparmor=`.
  Ubuntu 24.04 + podman 4.9.3 ships a generated default profile
  (`containers-default-0.57.4-apparmor1`) whose network rule denies
  `socket(AF_INET, SOCK_STREAM)` (audited as `apparmor="DENIED"
  operation="create" class="net" info="failed af match"`), leaving the proxy
  unable to open TCP sockets at all. Defense in depth still has
  `--cap-drop=ALL`, `--read-only`, `--security-opt=no-new-privileges`, and
  pod-level network isolation. Override to a specific profile name on hosts
  that ship a custom AppArmor policy permissive to inet socket creation.

## Handler cascade

```text
restart telemt-pod
  -> notifies: restart telemt-decoy, restart telemt

restart telemt-pebble   molecule-only Pebble unit
restart telemt-decoy    Caddy decoy container
restart telemt          telemt proxy container
```

In molecule mode the Pebble setup is deliberately before `meta: flush_handlers`
so the decoy container never starts with a missing `pebble-root.pem` bind
source.

## Molecule layout

```text
roles/telemt/molecule/
├── Makefile
├── default/              # podman-in-podman, Debian trixie
│   ├── molecule.yml
│   ├── Dockerfile.j2
│   └── ENABLE_CI
├── gha/                  # GitHub Actions native runner scenario
│   ├── molecule.yml
│   ├── inventory/hosts.yml
│   └── ENABLE_CI
└── shared/
    ├── base.yml          # loaded with `molecule -c molecule/shared/base.yml`
    ├── prepare.yml
    ├── converge.yml
    ├── verify.yml
    ├── tasks/
    │   ├── prepare.yml
    │   ├── converge-telemt.yml
    │   └── wait-services.yml
    └── vars/common.yml
```

Scenarios are included in the repository CI matrix when their directory has an
`ENABLE_CI` marker. The workflow runs:

```bash
molecule -c molecule/shared/base.yml converge -s <scenario>
molecule -c molecule/shared/base.yml idempotence -s <scenario>
molecule -c molecule/shared/base.yml verify -s <scenario>
```

## Make targets

Run from `roles/telemt/molecule`:

```bash
make help
make default-podman-converge
make default-podman-idempotence
make default-podman-verify
make gha-native-converge
make gha-native-idempotence
make gha-native-verify
```

The `default` scenario runs inside a molecule-managed Debian container with
nested Podman. Its Dockerfile installs `crun` for the opt-in `mtp_ping`
one-shot but pins Podman's default runtime to `runc` in `containers.conf`; the
role's normal systemd-managed containers use `--cgroups=split`, while
`mtp_ping` explicitly uses `--runtime=crun --cgroups=disabled`. The `gha`
scenario applies the role directly to the GitHub Actions runner VM with
`ansible_connection: local`.

## Driver conditionals

`mp_driver` is the single source of truth for driver-specific behavior.

- `shared/base.yml` sets `mp_driver: podman` for `molecule-telemt`
- `gha/inventory/hosts.yml` overrides localhost to `mp_driver: native`
- Do not put `mp_driver` in `shared/vars/common.yml`; `vars_files` precedence
  would mask inventory overrides and break the native scenario.

The shared prepare task writes `telemt.test` to `/etc/hosts` only when
`mp_driver != 'podman'`. Podman scenarios use `etc_hosts` in `molecule.yml`
because `/etc/hosts` in the molecule container is a bind mount that
`lineinfile` cannot atomically replace.

## What verify checks

1. systemd units are active: pod, Pebble, decoy, telemt
2. telemt listener accepts connections
3. `/v1/health` and `/v1/health/ready` return healthy JSON
4. the role emitted the expected per-user x per-endpoint Fake-TLS links
5. rendered `telemt.toml` contains TLS, mask, users, and domain settings
6. `/metrics` exposes expected counters
7. a vanilla HTTPS GET through telemt's splice reaches the Caddy decoy
8. the served certificate eventually has a Pebble issuer and expected SAN
9. a raw garbage TCP probe increments `telemt_connections_total`
10. optional `mtp_ping` performs a real Fake-TLS handshake to Telegram and
    increments Alice's authenticated user counter

`mtp_ping` is opt-in because it needs internet egress to Telegram DCs on 443:

```bash
MP_RUN_MTP_PING=1 make default-podman-verify
```

The default is off in CI.

## Key variables

Required:

- `telemt_domain` - proxy and Fake-TLS SNI domain
- `telemt_users` - dict of `user: 32-hex-secret`

Important:

- `telemt_listen_port` - public proxy listener, default `443`
- `telemt_modes_tls` - default `true`; Fake-TLS mode
- `telemt_tls_mask` - default `true`; invalid traffic splices to decoy
- `telemt_link_endpoints` - optional map of labels to advertised server IPs
- `telemt_molecule_mode` - deploy Pebble and point Caddy ACME at it
- `telemt_publish_api`, `telemt_publish_metrics` - publish host loopback ports
- `telemt_apparmor_profile` - AppArmor profile name passed to every container,
  default `unconfined` (see "Important role behavior")

Internal variables use the `_telemt_*` prefix.

## Debug commands for podman scenario

```bash
podman exec molecule-telemt systemctl status podman-telemt.service --no-pager
podman exec molecule-telemt journalctl -xeu podman-telemt.service --no-pager -n 200
podman exec molecule-telemt journalctl -xeu podman-telemt-decoy.service --no-pager -n 200
podman exec molecule-telemt podman ps --all
podman exec molecule-telemt podman logs --tail 100 telemt
podman exec molecule-telemt podman logs --tail 100 telemt-decoy
podman exec molecule-telemt podman logs --tail 100 telemt-pebble
podman exec molecule-telemt ss -ltnp
```

For the `gha` native scenario, run the same commands directly on the runner VM
without the outer `podman exec molecule-telemt`.

## Validated technical decisions

| Decision | Why |
|----------|-----|
| Pebble ACME in molecule | Tests the production ACME/TLS-ALPN-01-through-splice path |
| `acme_ca` uses `https://localhost:14000/dir` | Matches Pebble's bundled endpoint certificate hostname |
| Pebble minica is copied from container rootfs | It is not available through Pebble's management roots API |
| `podman_container_exec` probes API/metrics from decoy | telemt loopback checks reject host-published DNAT source addresses |
| Raw `printf | nc` remains a shell task | No installed native module sends arbitrary bytes over a TCP socket |
| `mtp_ping` runs in a one-shot container | Keeps Erlang/build tools off the host and joins the pod network namespace |
| `nftables` is installed with Podman | netavark needs `nft` for pod NAT/firewall rules on Debian/Ubuntu variants |
| `telemt_apparmor_profile` defaults to `unconfined` | Ubuntu 24.04 + podman 4.9.3 default profile (`containers-default-0.57.4-apparmor1`) denies `socket(AF_INET, SOCK_STREAM)` for confined containers; cap-drop, no-new-privileges, read-only rootfs, and netns isolation remain |
