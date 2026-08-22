# naive_proxy

An Ansible role that deploys NaiveProxy and an optional sing-box AnyTLS server
behind one HAProxy listener. Containers run in one Podman pod and are managed
as systemd services. HAProxy also serves an unauthenticated Caddy decoy and
routes the two independent TLS-ALPN-01 certificate flows.

The design follows the official
[NaiveProxy HAProxy setup](https://github.com/klzgrad/naiveproxy/wiki/HAProxy-Setup)
and deploys [NaiveProxy](https://github.com/klzgrad/naiveproxy) with an optional
[sing-box AnyTLS](https://sing-box.sagernet.org/configuration/inbound/anytls/)
endpoint.

This page is the operator/developer entry point. Normative behaviour is in the
[role contracts](doc/contracts/README.md); repeatable development procedures
are in the [maintenance runbooks](doc/maintenance/README.md).

## Architecture

```text
public port
    |
    v
HAProxy TCP frontend (SNI + ALPN inspection)
    |-- AnyTLS SNI ----------> sing-box :8445 (TCP passthrough; owns TLS)
    |-- acme-tls/1 ----------> acme.sh :10443 (Naive certificate challenge)
    `-- default -------------> HAProxy TLS frontend :8444
                                  |-- valid proxy auth --> Naive :8080
                                  `-- otherwise --------> Caddy :8081
```

The Naive and AnyTLS domains share the same public address and port but MUST be
different names. HAProxy terminates Naive TLS; sing-box terminates AnyTLS TLS.
The Naive backend is deliberately unauthenticated because HAProxy owns HTTP
proxy authentication.

See the [runtime](doc/contracts/runtime.md) and
[TLS/ACME](doc/contracts/tls-and-acme.md) contracts for routing, security, and
certificate invariants.

## Requirements

- Debian-family target with systemd and root privilege;
- public reachability on port 443, or forwarding from 443 to the configured
  listen port, for production TLS-ALPN-01 issuance;
- one FQDN for Naive and, when AnyTLS is enabled, a second FQDN resolving to
  the same public address set;
- outbound access to the configured image registries and release sources.

The role installs its Podman/Buildah and supporting host packages. It builds
the standalone Naive backend image on the managed host.

## Quick start

```yaml
- name: Deploy proxy edge
  hosts: proxy
  become: true
  roles:
    - role: kogeler.mini_pig.naive_proxy
      vars:
        naive_proxy_domain: "cdn.example.org"
        naive_proxy_anytls_domain: "edge.example.org"
        naive_proxy_external_ip_auto:
          helsinki: "203.0.113.10"
          stockholm: "203.0.113.20"
        naive_proxy_external_ip_manual:
          helsinki: "203.0.113.10"
        naive_proxy_users:
          alice: "replace-with-a-secret"
        naive_proxy_acme_email: "admin@example.org"
```

The two server maps are client-profile inputs, not local bind addresses. The
automatic profile uses its map for latency-based selection; the manual profile
uses its own map for an explicit selector. Set the maps equal when both modes
should expose the same servers.

To deploy only Naive, disable AnyTLS and omit its domain:

```yaml
naive_proxy_anytls_enabled: false
```

## Operator interface

The required inputs are:

- `naive_proxy_domain`;
- `naive_proxy_anytls_domain` when AnyTLS is enabled;
- non-empty `naive_proxy_external_ip_auto` and
  `naive_proxy_external_ip_manual` maps;
- non-empty `naive_proxy_users` map.

Frequently used optional variables include:

| Variable | Purpose |
|---|---|
| `naive_proxy_listen_port` / `naive_proxy_external_port` | local published port and port advertised to clients |
| `naive_proxy_pod_network` | attach the pod to an existing Podman network |
| `naive_proxy_decoy_index_html` | controller-side file for the local decoy page |
| `naive_proxy_decoy_upstream_url` | use a remote reverse-proxied decoy instead |
| `naive_proxy_anytls_utls_fingerprint` | add browser-like uTLS to generated AnyTLS outbounds |
| `naive_proxy_update_runtime_images` | refresh the supported runtime-image subset on an existing host |
| `naive_proxy_haproxy_diagnostics_enabled` | expose the loopback-only HAProxy admin socket and trace ring |

The supported meaning of every public variable, validation boundary, tag, and
owned output is in the [interface contract](doc/contracts/interface.md); the
executable literal defaults remain in `defaults/main.yml`. Image/version pins
are intentionally explicit; update them through the
[version runbook](doc/maintenance/update-versions.md).

## Generated client configurations

The role writes two sing-box JSON files per current user on the Ansible
controller:

```text
singbox-<inventory-host>-<user>-auto.json
singbox-<inventory-host>-<user>-manual.json
```

They are full TUN profiles with Naive options and, when enabled, matching
AnyTLS options for every server-map entry. The role does not generate QR codes
and has no QR/Pillow runtime dependency. See the
[client configuration contract](doc/contracts/client-configs.md) for routing,
DNS, IPv6, QUIC, and selection semantics.

## Services and state

Target state defaults to `/opt/naive-proxy`. The principal units are:

```text
podman-naive-pod.service
podman-naive-haproxy.service
podman-naive-backend.service
podman-naive-decoy.service
podman-naive-anytls.service        # when enabled
naive-acme-renew.service
naive-acme-renew.timer
```

Normal inspection uses systemd and Podman:

```bash
systemctl status podman-naive-haproxy.service
journalctl -u podman-naive-backend.service --no-pager -n 100
podman logs naive-anytls
```

The role's built-in healthchecks prove unit readiness and the decoy route.
Authenticated tunnels, certificates, generated profiles, renewals, and stress
paths are covered by Molecule as described in the
[verification contract](doc/contracts/verification.md).

## Development

All local automation lives in the role's Molecule Makefile:

```bash
cd roles/naive_proxy/molecule
make bootstrap
make lint
make versions-check
make default-podman-converge > /tmp/naive-converge.log 2>&1
make default-podman-verify > /tmp/naive-verify.log 2>&1
```

`bootstrap` creates the role-local `.venv`. Use `make venv-refresh` to upgrade
it in place or `make venv-recreate` for a clean latest-PyPI environment. Do
not invoke bare `molecule`; scenario setup and collection discovery belong to
the wrapper. See [Testing](doc/maintenance/testing.md) before running a
scenario and [Changing the role](doc/maintenance/change-workflow.md) before a
cross-cutting edit.

## Documentation map

- [Operator guides](doc/guides/README.md) — deployment variants, tuning
  examples, service operations, refresh, and decommissioning;
- [Contracts](doc/contracts/README.md) — supported behaviour and evidence;
- [Maintenance](doc/maintenance/README.md) — change, version, test, and debug
  runbooks;
- [Production debug scripts](doc/scripts/README.md) — bounded H2 capture and
  analysis tools;
- [Agent entry point](AGENTS.md) — repository-local rules and invariant map.

## Limitations

- AnyTLS has no unauthenticated fallback or decoy.
- A remote decoy's response bodies and `Location` headers are not rewritten.
- Disable flags do not uninstall an existing deployment or prune old state;
  follow the [operator guide](doc/guides/deployment-and-operations.md#idempotency-and-decommissioning)
  when decommissioning.
- Public DNS equality and public CA reachability are operator responsibilities.
- Loopback Molecule tests cannot reproduce every real-network H2 pressure
  pattern; use the production debug runbook when needed.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
