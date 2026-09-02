# telemt

Ansible role for running [Telemt](https://github.com/telemt/telemt), a Telegram
MTProxy, in root-managed Podman containers and systemd units.

The role supports two different client-facing topologies. Choose one before
configuring the remaining variables:

| Topology | Telegram link | Public protocol | Role-managed ingress | Typical use |
|---|---|---|---|---|
| `direct` (default) | `tg://proxy` | MTProxy, optionally Fake TLS | Telemt | General Telegram clients and the established Fake TLS setup |
| `web` | `tg://webproxy` | HTTPS on external TCP/443 | HAProxy, then Telemt WEB | A compatible Telegram Desktop WEB-proxy client |

The topologies are not two spellings for the same setup. They use different
client link formats, ingress protocols, secret modes, and decoy paths.

## What the role produces

For either topology the role:

- creates a Podman pod and systemd-managed containers;
- renders the Telemt and decoy configuration;
- starts and health-checks the deployment;
- prints ready-to-use Telegram proxy links at the end of the play.

WEB mode additionally deploys HAProxy, obtains its TLS certificate with
`acme.sh`, and enables a renewal timer.

The role does not configure DNS, a cloud load balancer, edge NAT, or a host
firewall. Those must route the documented public endpoint to the local socket
published by Podman.

Default service and state identities are deliberately isolated:

| Resource | Direct | WEB |
|---|---|---|
| Instance | `telemt` | `telemt-web` |
| State directory | `/opt/telemt` | `/opt/telemt_web` |
| Pod | `podman-telemt-pod.service` | `podman-telemt-web-pod.service` |
| Telemt | `podman-telemt.service` | `podman-telemt-web.service` |
| Decoy | `podman-telemt-decoy.service` | `podman-telemt-web-decoy.service` |
| TLS ingress | Telemt itself | `podman-telemt-web-haproxy.service` |
| Certificate renewal | Caddy-managed | `telemt-web-acme-renew.timer` |

## Requirements

Both topologies require:

- a Debian-based host with root access and systemd;
- Podman-compatible host networking;
- a non-empty `telemt_users` mapping with 16-byte secrets represented as
  32 hexadecimal characters;
- outbound access from Telemt to Telegram.

WEB mode additionally requires:

- a lowercase FQDN dedicated to this WEB endpoint;
- one stable, declared IPv4 for Telemt's WEB relay tuple;
- external TCP/443 reaching the role-managed HAProxy listener;
- one operator-owned decoy source;
- a Telegram Desktop build that supports `tg://webproxy`.

Generate a user secret with:

```bash
openssl rand -hex 16
```

## Direct mode

### Minimal deployment

```yaml
- hosts: proxy
  roles:
    - role: kogeler.mini_pig.telemt
      vars:
        telemt_domain: "mask.example.org"
        telemt_users:
          main: "0123456789abcdef0123456789abcdef"
```

The defaults enable only Fake TLS. The role prints a link similar to:

```text
tg://proxy?server=mask.example.org&port=443&secret=ee0123456789abcdef0123456789abcdef...
```

### Direct secret modes

Direct mode can expose any combination of these modes:

| Variables | Link secret | Meaning |
|---|---|---|
| `telemt_modes_classic: true` | raw 32-hex secret | Classic MTProxy handshake |
| `telemt_modes_secure: true` | `dd` + secret | Secure MTProxy handshake |
| `telemt_modes_tls: true` | `ee` + secret + encoded domain | Fake TLS handshake and masking |

At least one mode must be enabled. When multiple modes are enabled, the role
prints a link for every enabled mode and user.

In direct mode `telemt_listen_port` is both the local Telemt listener and the
port written into `tg://proxy` links. Use `telemt_link_endpoints` when the
advertised server address differs from `telemt_domain`:

```yaml
telemt_domain: "mask.example.org"
telemt_listen_port: 9443
telemt_link_endpoints:
  primary: "203.0.113.10"
  backup: "203.0.113.11"
```

## WEB mode

### Minimal deployment

This example assumes that the server owns public TCP/443 directly:

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
```

With the defaults, the role uses the `https` carrier and the `dd` secret
mode. It prints:

```text
tg://webproxy?server=proxy.example.org&secret=dd0123456789abcdef0123456789abcdef
```

WEB links never contain a port. Telegram WEB proxy clients always connect to
the external endpoint on TCP/443.

### Traffic path

```text
Telegram Desktop -> proxy.example.org:443
    -> optional L4 forwarding / DNAT
    -> telemt_listen_bind:telemt_listen_port
    -> HAProxy TCP/ALPN router
       |-- acme-tls/1 -> acme.sh challenge responder
       `-- regular TLS -> HAProxy HTTPS terminator
            |-- canonical Host -> Telemt WEB on pod loopback
            `-- other Host -> Caddy decoy

Telemt invalid capability or user -> Caddy decoy
Telemt valid WEB stream -> Telegram middle proxy
```

HAProxy routes the entire canonical vhost to Telemt. Telemt performs the
credential decision and strips carrier credentials before sending invalid
requests to the decoy. Do not route only known WEB paths in an external proxy.

### Public endpoint, local ingress, and declared relay address

WEB mode has three independent address values:

| Setting | Meaning |
|---|---|
| `proxy.example.org:443` | Real endpoint resolved and contacted by Telegram Desktop; also used by ACME |
| `telemt_listen_bind:telemt_listen_port` | Local host socket on which Podman publishes HAProxy ingress |
| `telemt_web_public_ip:443` | Stable value written to Telemt's WEB `public_addr` and used as its declared relay destination tuple |

The client does not send the selected DNS A address in TLS or HTTP. TLS SNI
and HTTP `Host` contain only the hostname. An SNI-routing TCP proxy also does
not preserve the original destination when it opens a new backend connection.
Consequently, neither HAProxy nor Telemt can derive or verify the public IP
originally selected by the client.

`telemt_web_public_ip` is therefore not a bind address, a DNS assertion, or a
link-generation endpoint. Telemt does not resolve `telemt_domain` and does not
compare `public_addr` with the incoming socket. Telemt 3.5.2 only requires a
concrete socket address on port 443; this role's interface currently accepts
one IPv4 other than `0.0.0.0` and renders `<IP>:443`.

For every logical MTProxy stream, Telemt combines:

- the source address from `X-Forwarded-For` with a synthetic source port;
- the declared `telemt_web_public_ip:443` destination.

With the role's default Middle-End routing, that destination is serialized as
`our_addr` in `RPC_PROXY_REQ`. It is an input supplied by Telemt, not a value
sent or checked by the client. Direct relay does not use it to authenticate
the WEB client.

The variable remains singular because Telemt stores one `SocketAddr` per WEB
vhost. Every session for that hostname receives the same declared value. A
list would not model multiple DNS A records: Telemt receives no per-connection
original destination with which it could select an element. Multiple A records
may still feed the same ingress; Telemt simply declares the configured value
for all of them.

For the first deployment, using one stable A record or load-balancer address
as the declared value preserves the upstream project's intended semantics:

```yaml
telemt_listen_bind: ""
telemt_listen_port: 443
telemt_web_public_ip: "203.0.113.10"
```

It is not technically validated against DNS. To test whether Telegram
Middle-End is sensitive to the declared value, redeploy with a stable address
that deliberately differs from the real endpoint, for example:

```yaml
telemt_web_public_ip: "192.0.2.123"
```

Changing `public_addr` changes the WEB profile identity. Rerunning this role
also restarts Telemt, so run each comparison with a newly issued WEB session.

The local ingress remains independent. For this network path:

```text
198.51.100.20:443 -> TCP passthrough -> 10.20.0.5:8443
```

configure the local socket separately:

```yaml
telemt_domain: "proxy.example.org"
telemt_web_public_ip: "198.51.100.20"  # Initial declared value for comparison
telemt_listen_bind: "10.20.0.5"
telemt_listen_port: 8443
```

The forward must preserve the TLS ClientHello and ALPN because the same route
carries normal WEB traffic and ACME TLS-ALPN-01 validation. It does not have
to preserve the source socket. An upstream TLS-terminating reverse proxy is a
different topology and is not managed by this role.

### Client address behind an SNI TCP router

An SNI router does not need to terminate TLS or create an HTTP header. The
role's own HAProxy terminates TLS later and creates `X-Forwarded-For`. If the
router proxies TCP by opening a new connection, HAProxy can only put the
router's address into that header; the original client address is no longer
present in the data it receives.

This does not prevent WEB session establishment. It changes source-aware
semantics:

- all clients behind that router share Telemt's per-IP WEB limits;
- source deny/rate policies and source-address metrics see the router;
- Middle-End receives the router address plus Telemt's unique synthetic port
  as `client_addr`.

The pinned Telemt defaults allow 16 live sessions and 64 unused bootstraps per
forwarded IP. For a trusted shared SNI ingress, make those per-IP ceilings
equal to the corresponding global ceilings if the defaults are too small:

```yaml
telemt_web_limits:
  max_sessions_global: 128
  max_sessions_per_ip: 128
  max_bootstraps_global: 512
  max_bootstraps_per_ip: 512
```

These settings accept the loss of source identity; they do not reconstruct
it. Without source-preserving forwarding or explicit L4 source metadata, no
downstream role setting can recover the original client IP. This is separate
from `telemt_web_public_ip`, which supplies the declared destination tuple and
remains required by Telemt's WEB vhost schema.

### Carrier choice: https or https-lanes

`telemt_web_carrier` changes the carrier used inside the same HTTPS endpoint.
It does not change the generated link.

| Value | Behaviour | Choose it when |
|---|---|---|
| `https` (default) | One serialized HTTPS uplink/downlink sequence per session | You want the simplest and least concurrency-heavy setup |
| `https-lanes` | Independent sequencing, polling, retry state, and queues for each logical MTProxy stream | Users create several simultaneous streams and application-level head-of-line blocking is measurable |

`https-lanes` requires public HTTP/2 and enough HAProxy/upstream connection
capacity for concurrent lane polls. It removes serialization between logical
streams at the WEB protocol layer, but it is not QUIC or HTTP/3; streams on the
same HTTP/2 TCP connection can still be affected by packet loss.

Start with `https`. Switch to `https-lanes` only when its concurrency model
solves an observed workload problem. New sessions use the selected carrier;
the user-facing `tg://webproxy` link remains the same.

See the
[upstream Telemt 3.5.2 WEB contract](https://github.com/telemt/telemt/blob/3.5.2/docs/WEB/WEB_PROXY.en.md)
for the wire-level details.

### Secret choice: plain or dd

Both modes use the same raw 32-hex value from `telemt_users`. The profile
controls how that value is represented in the link and which inner MTProxy
handshake Telemt accepts:

| Value | Generated link secret | Effect |
|---|---|---|
| `plain` | `<32-hex-secret>` | Classic inner MTProxy handshake |
| `dd` (default) | `dd<32-hex-secret>` | Secure inner MTProxy handshake |

The outer network traffic is HTTPS in both cases. `dd` does not select the
`https-lanes` carrier, and `plain` does not disable TLS. Fake TLS `ee`
secrets belong to direct mode and are invalid in WEB profiles.

When `telemt_web_profiles` is empty, every entry in `telemt_users` receives
`telemt_web_default_secret_mode`:

```yaml
telemt_web_default_secret_mode: dd
telemt_users:
  alice: "0123456789abcdef0123456789abcdef"
  bob: "fedcba9876543210fedcba9876543210"
```

Use explicit profiles to select users, mix secret modes, or apply per-user
session and stream limits:

```yaml
telemt_users:
  alice: "0123456789abcdef0123456789abcdef"
  bob: "fedcba9876543210fedcba9876543210"

telemt_web_profiles:
  - user: alice
    secret_mode: plain
    max_sessions: 4
    max_streams: 32
    max_streams_per_session: 8
  - user: bob
    secret_mode: dd
```

Only users listed in a non-empty `telemt_web_profiles` list receive a WEB
profile. Every profile user must also exist in `telemt_users`.

### Decoy choice

WEB mode requires exactly one operator-owned decoy:

| Variable | Result |
|---|---|
| `telemt_decoy_site_dir` | Copies a complete static site directory from the controller |
| `telemt_decoy_index_html` | Copies one operator-provided index file |
| `telemt_decoy_upstream_url` | Reverse-proxies to one fixed HTTP(S) origin without a path, query, or credentials |

The built-in generic stub is accepted in direct mode but rejected in WEB mode
because shared decoy content makes deployments easier to fingerprint.

When using `telemt_decoy_upstream_url`, choose a static-style origin. Response
bodies, absolute URLs, and `Location` headers are not rewritten and may expose
the upstream hostname.

### Certificates

The role obtains the WEB certificate with `acme.sh` through TLS-ALPN-01.
HAProxy routes only the ACME ALPN challenge to the temporary responder and
terminates normal TLS itself. Renewal is managed by:

```text
telemt-web-acme-renew.timer
```

`telemt_acme_email` is the ACME account email and
`telemt_acme_server` selects the CA, defaulting to `letsencrypt`.

## Multiple instances on one host

One default direct instance and one default WEB instance can coexist. Their
names and state directories are already different, but their host sockets must
also be distinct.

For every additional instance, keep these values unique:

| Resource | Variables |
|---|---|
| Pod, containers, units, and handlers | `telemt_instance_name` |
| Configuration, certificates, and state | `telemt_config_dir` |
| Public local ingress socket | `telemt_listen_bind` plus `telemt_listen_port` |
| Published API socket | `telemt_api_bind` plus `telemt_api_port` |
| Published metrics socket | `telemt_metrics_bind` plus `telemt_metrics_port` |
| DNS and certificate identity | `telemt_domain` |

The role does not discover unrelated instances or probe their sockets.

## Configuration reference

### Core variables

| Variable | Default | Purpose |
|---|---|---|
| `telemt_deployment_mode` | `direct` | Select `direct` or `web` |
| `telemt_domain` | `""` | Required proxy domain; WEB requires a canonical lowercase FQDN |
| `telemt_users` | `{}` | Required mapping of user name to 32-hex secret |
| `telemt_listen_bind` | `""` | Local IPv4 for the Podman ingress publish |
| `telemt_listen_port` | `443` | Local ingress port; also advertised by direct links |
| `telemt_instance_name` | mode-derived | Runtime and systemd namespace |
| `telemt_config_dir` | mode-derived | Persistent configuration and state |
| `telemt_pod_network` | `""` | Existing Podman network to attach |
| `telemt_image_tag` | `3.5.2` | Telemt image version; WEB requires 3.5.2 or newer |

### WEB variables

| Variable | Default | Purpose |
|---|---|---|
| `telemt_web_public_ip` | `""` | Required stable IPv4 declared as Telemt `public_addr`; not checked against DNS |
| `telemt_web_carrier` | `https` | `https` or `https-lanes` |
| `telemt_web_default_secret_mode` | `dd` | `plain` or `dd` for derived profiles |
| `telemt_web_profiles` | `[]` | Optional explicit per-user profiles and limits |
| `telemt_web_limits` | `{}` | Supported positive `[web.limits]` overrides |
| `telemt_web_timeouts` | `{}` | Supported positive `[web.timeouts]` overrides |
| `telemt_web_haproxy_timeout_margin_secs` | `10` | Margin above Telemt long polling for HAProxy |
| `telemt_acme_email` | `""` | WEB certificate account email |
| `telemt_acme_server` | `letsencrypt` | acme.sh CA selector |

### API and metrics

The control API is disabled and unpublished by default. Publishing it provides
full proxy control, so keep it on loopback unless a firewall and a strict
whitelist protect it.

| Variable | Default |
|---|---|
| `telemt_api_enabled` | `false` |
| `telemt_publish_api` | `false` |
| `telemt_api_bind` | `127.0.0.1` |
| `telemt_api_port` | `9091` |
| `telemt_api_whitelist` | `[]` |
| `telemt_publish_metrics` | `false` |
| `telemt_metrics_bind` | `127.0.0.1` |
| `telemt_metrics_port` | `9090` |

Advanced HAProxy, container-hardening, timeout, and image variables are
documented inline in
[`defaults/main.yml`](defaults/main.yml), which is the authoritative list of
all defaults.

## Operations

Inspect the default direct instance:

```bash
systemctl status podman-telemt-pod.service
systemctl status podman-telemt.service
systemctl status podman-telemt-decoy.service
journalctl -u podman-telemt.service -f
```

Inspect the default WEB instance:

```bash
systemctl status podman-telemt-web-pod.service
systemctl status podman-telemt-web.service
systemctl status podman-telemt-web-haproxy.service
systemctl status podman-telemt-web-decoy.service
systemctl status telemt-web-acme-renew.timer
journalctl -u podman-telemt-web-haproxy.service -f
```

Apply configuration changes by rerunning the role. Instance-scoped handlers
restart only the affected topology. An unchanged repeated run is idempotent.

## WEB troubleshooting

| Symptom | Check |
|---|---|
| Preflight rejects `telemt_domain` | Use a lowercase FQDN with at least one dot, not an IP or single label |
| Preflight rejects `telemt_web_public_ip` | Use one syntactically valid IPv4 other than `0.0.0.0`; private and loopback values are accepted |
| Certificate issuance fails | Verify DNS and that external TCP/443 is forwarded unchanged to `telemt_listen_bind:telemt_listen_port` |
| Link is rejected by Telegram | Use a compatible Telegram Desktop build, external port 443, and a `plain` or `dd` WEB link |
| Valid-looking requests reach the decoy | Check the exact FQDN, profile user, secret mode, and external forwarding |
| Clients behind an SNI TCP router hit limits together | Raise `max_sessions_per_ip` and `max_bootstraps_per_ip` to the corresponding global ceilings; the original source IP is unavailable in this topology |
| Long polls terminate at a fixed interval | Keep HAProxy/L4 timeouts above `telemt_web_timeouts.long_poll_secs` |
| `https-lanes` behaves serially | Verify public HTTP/2 negotiation and sufficient concurrent connection capacity |

Carrier capabilities, bootstrap values, and session tokens are bearer
credentials. Keep HAProxy access logging disabled and do not add an upstream
proxy that records request paths, queries, or authorization headers.

## Local development

Run from `roles/telemt/molecule`:

```bash
make bootstrap
make lint
make default-podman-converge
make default-podman-idempotence
make default-podman-verify
```

The `default` scenario deploys direct and WEB instances together, verifies
that rerunning either one does not restart the other, and exercises WEB
certificate issuance, decoy routing, and both `https` and `https-lanes` over
HTTP/2. Each carrier runs `plain` and `dd` profiles through a real Telegram
`req_pq`/`resPQ` round trip.

The `gha` scenario uses the same converge and verify playbooks on a native
GitHub Actions runner:

```bash
make gha-native-converge
make gha-native-idempotence
make gha-native-verify
```

## License

Apache-2.0
