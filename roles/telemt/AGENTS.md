# telemt - Agent Context

## Rules for AI agents running Molecule

1. Every operation on a Molecule scenario MUST go through a target in
   `molecule/Makefile`, invoked from `roles/telemt/molecule`. This includes
   setup, execution, diagnostics, recovery, cleanup, and instance removal.
   Agents MUST NOT run bare `molecule`, `ansible-playbook`, or `ansible`
   against Molecule inventory; run `podman exec/inspect/rm` against a Molecule
   instance; edit files under the instance's `/opt`; or invoke `systemctl`
   inside the instance.
2. If the Makefile does not expose a required operation, extend the automation
   first and then use the new target. Add a reusable lifecycle or diagnostic
   operation, parameterized across a scenario/driver where practical. Do not
   add a one-off target tied to one failed run, ephemeral inventory path,
   container ID, runtime file, or service mutation.
3. Never repair or normalize a partially executed scenario out of band. Use
   the Makefile `destroy`/`create` lifecycle, or rerun a suitable existing
   Makefile action. A result obtained after manual runtime mutation is invalid
   and must be repeated from a clean instance.
4. Use the role-local environment created by `make bootstrap`. Make targets
   select `molecule/.venv` automatically; do not activate or call its binaries
   directly and do not depend on an external venv.
5. Use `make default-podman-test` for a clean default-scenario validation; its
   Molecule sequence owns destroy, create, prepare, converge, idempotence,
   verify, and final destroy. Separate Makefile `converge`, `idempotence`, and
   `verify` actions are allowed while developing, but the final result must be
   reproduced by the clean `test` target.
6. `login` is available for human investigation, not as an agent bypass for
   unautomated commands. When agents need additional state or logs, add a
   reusable diagnostic task/action behind the Makefile wrapper.
7. Do not pipe Make/Molecule output through `tail`. Save the complete Make
   target output under `/tmp`, then inspect that log with `rg`, `grep`, `sed`,
   or `less` without interacting with the scenario directly.
8. Prefer native Ansible modules over `shell`/`command`. For this role,
   legitimate command/shell cases are one-shot image/config validation,
   raw-byte TCP probes (`printf | nc`), and rescue-only diagnostics such as
   `journalctl`.

## What this role does

`telemt` has two deployment topologies in one Podman pod managed by systemd:

- `direct` (default) preserves the Rust MTProto/Fake-TLS proxy. Telemt owns the
  public listener and splices invalid TLS-looking traffic to Caddy.
- `web` exposes HAProxy through a local ingress that external TCP/443 reaches
  directly or through L4 forwarding. HAProxy routes ACME ALPN, terminates
  normal TLS, and sends the canonical Host to Telemt's WEB listener. Telemt,
  not HAProxy, validates capabilities/users and falls back invalid requests to
  Caddy. Foreign Host values go straight to Caddy.

Default direct resources use instance name `telemt` and `/opt/telemt`.
Default WEB resources use `telemt-web` and `/opt/telemt_web`. Do not replace
these mode-derived defaults with static names: direct + WEB coexistence is a
tested contract. Same-mode multi-instance deployments must override both
`telemt_instance_name` and `telemt_config_dir`.

The Molecule scenario validates the real ACME/TLS-ALPN-01 path with Pebble:
Caddy requests a certificate from Pebble, Pebble validates the TLS-ALPN-01
challenge through telemt's TCP splice, and verify polls the served certificate
until the Pebble-issued cert replaces Caddy's bootstrap cert.

## Architecture

```text
Direct client : telemt_listen_port
    |
    v
+--- Pod: telemt-pod (direct default) --------------------------------+
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

WEB client :443
    |
    | optional L4 SNI forwarding / DNAT
    v
+--- Pod: telemt-web-pod (WEB default) -------------------------------+
| HAProxy outer TCP (:telemt_listen_port)                               |
|   |-- ALPN acme-tls/1 -> acme.sh responder (:10443)                   |
|   `-- default -> HAProxy inner TLS (:8444)                            |
|                  |-- canonical Host -> Telemt WEB (:18080)            |
|                  `-- foreign Host -> Caddy HTTP (:18081)              |
|                                         ^                             |
| Telemt invalid capability/user --------+                              |
+----------------------------------------------------------------------+
```

Internal ports:

| Name | Port | Purpose |
|------|------|---------|
| `telemt_listen_port` | default `443`, direct molecule `9443` | local ingress published by Podman; WEB may receive forwarded public TCP/443 |
| `_telemt_decoy_port` | `8443` | Caddy decoy HTTPS listener inside the pod |
| `_telemt_pebble_port` | `14000` | Pebble ACME directory, molecule only |
| `_telemt_pebble_mgmt_port` | `15000` | Pebble management endpoint, molecule only |
| `_telemt_pebble_host_port` | direct `14000`, WEB `14010` | host-side Pebble mapping in molecule only |
| `_telemt_pebble_mgmt_host_port` | direct `15000`, WEB `15010` | host-side Pebble management mapping in molecule only |
| `_telemt_web_listener_port` | `18080` | Telemt WEB listener on pod loopback |
| `_telemt_web_decoy_port` | `18081` | WEB-mode Caddy HTTP fallback |
| `_telemt_haproxy_tls_port` | `8444` | HAProxy inner TLS terminator |
| `_telemt_acme_alpn_port` | `10443` | acme.sh TLS-ALPN responder |
| `telemt_metrics_port` | default `9090` | metrics listener |
| `telemt_api_port` | default `9091` | API listener |

## Important role behavior

- The decoy always uses Caddy's ACME issuer path. Production uses the default
  public CA behavior; molecule mode overrides the global ACME CA to Pebble.
- WEB certificates are owned by acme.sh and mounted into HAProxy. Renewal is
  driven by `<instance>-acme-renew.timer`; the renewal hook reloads that
  instance's HAProxy.
- WEB links always use the external endpoint on TCP/443. `telemt_listen_port`
  is the local ingress and may differ when L4 passthrough preserves the TLS
  ClientHello and ACME ALPN traffic. `public_addr` is a separate declared
  relay tuple; Telemt does not compare it with DNS or the ingress destination.
- HAProxy always overwrites `X-Forwarded-For` with the source address of its
  incoming TCP connection. Direct ingress or source-preserving DNAT exposes
  the client address. A connection-proxying SNI router exposes only the router
  address, so every client behind it shares WEB per-IP limits and Telemt's
  source-policy identity. Do not claim that the role can reconstruct a source
  address absent from the incoming connection.
- `telemt_web_public_ip` is deliberately singular. Telemt stores one
  `SocketAddr` per WEB vhost and rejects duplicate vhosts for the same host.
  Every session for that vhost receives the same declared value because no
  original-destination signal reaches Telemt. A list would not provide a
  per-connection selection mechanism.
- Pod, container, systemd, ACME, and handler identities derive from
  `telemt_instance_name`; config, Caddy state, ACME state, and certificates
  derive from `telemt_config_dir`. WEB defaults add `web-`/`_web` isolation.
- The role intentionally does not discover other deployments or check their
  live sockets. Colocated inventories must keep `telemt_instance_name`,
  `telemt_config_dir`, canonical domains, public socket tuples, and any
  published API/metrics socket tuples unique.
- Keep the entire canonical Host route pointed at Telemt. Moving credential
  filtering into HAProxy breaks the upstream WEB fallback semantics and risks
  exposing secret-bearing paths in access logs.
- WEB mode deliberately omits `[general.modes]` from `telemt.toml`. Telemt
  rejects a config where every legacy mode is false; no legacy MTProxy
  listener is exposed in this topology, and WEB profiles still explicitly use
  only `plain`/`dd` handshakes.
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
    `<telemt_config_dir>/pebble-root.pem` only when it changed. This prevents stale CA
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
restart <instance> pod
  -> stops: HAProxy (WEB), telemt, decoy, Pebble (molecule only)
  -> restarts: pod
  -> notifies: Pebble, decoy, telemt, then HAProxy

restart <instance> pebble   molecule-only Pebble unit
restart <instance> decoy    Caddy decoy container
restart <instance>          telemt proxy container
restart <instance> haproxy  WEB TLS ingress container
```

Handler topics must remain instance-scoped. Static `restart telemt-*` listener
names can make one role inclusion consume another instance's notifications.

Each container restart is implemented as stop, polling until every
`cgroup.procs` in the unit's cgroup tree is empty or the tree is gone, then
start. The recursive check is required because `--cgroups=split` puts conmon
and the payload in child cgroups. Podman 4.9 on Ubuntu 24.04 can otherwise race
an immediate systemd restart and fail `ExecStartPre` with `219/CGROUP`.

In molecule mode the Pebble setup is deliberately before `meta: flush_handlers`
so the decoy container never starts with a missing `pebble-root.pem` bind
source.

## Molecule layout

```text
roles/telemt/molecule/
├── Makefile
├── requirements-dev.txt # inputs for role-local molecule/.venv
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
    ├── web-verify.yml
    ├── files/web_flow.py # WEB/MTProxy wire probe with Telegram req_pq/resPQ
    ├── tasks/
    │   ├── prepare.yml
    │   ├── converge-telemt.yml
    │   ├── converge-web.yml
    │   ├── verify-web-carrier.yml
    │   ├── verify-coexistence.yml
    │   └── wait-services.yml
    └── vars/common.yml
```

Both scenarios deploy direct and WEB on the same host. The common converge
play applies direct after WEB has loaded its handlers and compares systemd
activation timestamps in both directions. This makes namespace and handler
isolation part of idempotence instead of mutating the host during verify.

Scenarios are included in the repository CI matrix when their directory has an
`ENABLE_CI` marker. The workflow runs:

```bash
molecule -c molecule/shared/base.yml test -s <scenario>
```

## Make targets

Run from `roles/telemt/molecule`:

```bash
make bootstrap
make help
make lint
make ci-podman
make default-podman-converge
make default-podman-idempotence
make default-podman-verify
make gha-native-converge
make gha-native-idempotence
make gha-native-verify
```

Runtime image tag defaults have exactly one source of truth:
`defaults/main.yml`. To update them, change only that file, open a pull request,
review both Telemt Molecule scenario results, and merge only after they pass.
Do not duplicate concrete tag values in documentation or tests; CI is the
compatibility gate.

The `default` scenario runs inside a molecule-managed Debian container with
nested Podman. Its Dockerfile installs `crun` for the `mtp_ping`
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

The shared prepare task writes the direct and WEB test domains to `/etc/hosts`
when `mp_driver != 'podman'`. The Podman scenario uses `etc_hosts` in
`molecule.yml` because `/etc/hosts` in the molecule container is a bind mount
that `lineinfile` cannot atomically replace.

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
10. when enabled, `mtp_ping` performs a real Fake-TLS handshake to Telegram
    and increments Alice's authenticated user counter
11. direct and WEB units, config paths, listeners, and health endpoints remain
    simultaneously active

The WEB portion checks Pebble certificate issuance through HAProxy, canonical
and foreign Host camouflage, wrong/malformed capabilities, and complete
create/uplink/downlink/delete flows. It deploys `https-lanes` and `https`
sequentially in the same scenario and runs both plain and DD profiles over
negotiated HTTP/2 for each carrier. Every combination performs a valid inner
MTProxy handshake, sends `req_pq` through HAProxy, WEB, and Telemt, and requires
a matching `resPQ` from Telegram. These checks are mandatory in both `default`
and `gha`, so both environments must provide Telegram egress.

Repository CI enables the separate direct-mode `mtp_ping` check for both
drivers. It remains opt-in for ordinary local actions because it builds an
Erlang test image. Run the full default CI equivalent with:

```bash
make ci-podman
```

Both direct and WEB live Telegram probes are therefore mandatory in repository
CI, and both environments must provide Telegram egress.

## Key variables

Required:

- `telemt_domain` - proxy and Fake-TLS SNI domain
- `telemt_users` - dict of `user: 32-hex-secret`

Important:

- `telemt_listen_port` - local proxy ingress, default `443`; direct links also
  advertise it, while WEB links always use external TCP/443
- `telemt_listen_bind` - optional concrete host IPv4 for the local publish
- `telemt_instance_name` - runtime/systemd namespace; unique per instance
- `telemt_config_dir` - persistent state namespace; unique per instance
- `telemt_modes_tls` - default `true`; Fake-TLS mode
- `telemt_tls_mask` - default `true`; invalid traffic splices to decoy
- `telemt_link_endpoints` - optional map of labels to advertised server IPs
- `telemt_molecule_mode` - deploy Pebble and point Caddy ACME at it
- `telemt_publish_api`, `telemt_publish_metrics` - publish host loopback ports
- `telemt_apparmor_profile` - AppArmor profile name passed to every container,
  default `unconfined` (see "Important role behavior")
- `telemt_deployment_mode` - `direct` (default) or `web`
- `telemt_web_public_ip` - required stable declared IPv4 for the WEB relay tuple
- `telemt_web_profiles` - WEB user profiles and `plain`/`dd` secret modes
- one WEB decoy source: site directory, index file, or upstream URL

Internal variables use the `_telemt_*` prefix.

## Molecule diagnostics

```bash
cd roles/telemt/molecule
make default-podman-converge 2>&1 | tee /tmp/telemt-converge.log
make default-podman-idempotence 2>&1 | tee /tmp/telemt-idempotence.log
make default-podman-verify 2>&1 | tee /tmp/telemt-verify.log
make default-podman-destroy 2>&1 | tee /tmp/telemt-destroy.log
```

The scenario's verify tasks and rescue blocks must collect the service state,
journal excerpts, container state, and listener information needed to diagnose
failures. If existing output is insufficient, extend those diagnostics (or add
a reusable Makefile-backed diagnostic action) and rerun through Make. The same
rule applies to the `gha` native scenario: agents must not execute diagnostic
or repair commands directly on the runner.

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
