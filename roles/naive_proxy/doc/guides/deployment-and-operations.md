# Deployment and operations

This guide collects operator examples that do not belong in the role's short
entry-point README. Check the [interface contract](../contracts/interface.md)
before adopting an example: it is authoritative for defaults and validation.

## Port forwarding in front of HAProxy

When a firewall, load balancer, or router forwards public `443` to another
local port, distinguish the port the pod publishes from the port clients use:

```yaml
naive_proxy_listen_port: 8443
naive_proxy_external_port: 443
```

Both Naive and AnyTLS client options will advertise `443`. TLS-ALPN-01 still
has to arrive from the public CA on public port 443 and reach the configured
listen port unchanged.

## Decoy choices

The default unauthenticated Naive route serves the built-in local page. To use
a controller-side HTML file instead:

```yaml
naive_proxy_decoy_index_html: "{{ playbook_dir }}/files/decoy.html"
```

To reverse-proxy an HTTPS site:

```yaml
naive_proxy_decoy_upstream_url: "https://example.com"
```

The upstream setting wins if both variables are set. Caddy rewrites the
upstream `Host` and removes forwarding headers, but does not rewrite response
bodies or `Location` headers. Absolute links and redirects can therefore
reveal the origin; prefer an upstream you control or a local page.

## HAProxy throughput tuning

The role ships throughput-oriented defaults for a dedicated edge. Start with
the defaults and change one variable at a time under a representative load.
For example, a 500 Mbps path at 50 ms RTT has a bandwidth-delay product of
about 3.125 MB:

```yaml
naive_proxy_haproxy_cpu_policy: performance
naive_proxy_haproxy_h2_frontend_rxbuf: "4m"
```

The rough sizing formula is:

```text
BDP_bytes ~= bandwidth_mbps * rtt_ms * 125
```

`tune.h2.fe.rxbuf` is per H2 stream, so multiply by expected concurrent
streams when estimating memory. The exact defaults, omission semantics, and
the HAProxy version floor are in the
[HAProxy contract](../contracts/haproxy.md).

## Runtime image refresh

Pinned images are normally pulled only when absent. To re-resolve the current
HAProxy and Caddy tags on an existing host for one apply:

```yaml
naive_proxy_update_runtime_images: true
```

The role compares image IDs and queues only the affected installed units for
restart. It does not refresh the locally built Naive backend, acme.sh, Pebble,
or sing-box. Reset the flag to false after the maintenance run unless every
apply should contact the registries.

## Service operations

Common inspection commands on the target are:

```bash
systemctl status \
  podman-naive-pod.service \
  podman-naive-haproxy.service \
  podman-naive-backend.service \
  podman-naive-decoy.service \
  podman-naive-anytls.service \
  naive-acme-renew.timer

journalctl -u podman-naive-haproxy.service --no-pager -n 100
podman logs naive-backend
podman logs naive-anytls
```

Trigger the Naive certificate job manually or reload HAProxy's configuration
and certificate files without recreating its cgroup:

```bash
systemctl start naive-acme-renew.service
systemctl reload podman-naive-haproxy.service
```

The renewal service reloads HAProxy automatically after successful issuance.
AnyTLS certificate renewal is internal to sing-box.

## Generated client files

The role prints every generated path and writes two secret JSON files per
current user. Import one directly into a compatible sing-box client, or run it
with a suitable platform-specific sing-box build:

```bash
sing-box check -c singbox-proxy-alice-auto.json
sing-box run -c singbox-proxy-alice-auto.json
```

The automatic profile periodically probes its configured URL; the manual
profile exposes an explicit selector. The full TUN and leak-prevention policy
is in the [client configuration contract](../contracts/client-configs.md).
The role deliberately does not create QR artifacts.

## Idempotency and decommissioning

Normal repeated applies are idempotent: files are rewritten only when their
content changes, the backend image rebuilds only when its Containerfile
changes, and handlers restart only affected units after safely releasing
nested cgroups.

The disable switches are not removal operations:

- `naive_proxy_enabled: false` skips the role and leaves deployed state;
- disabling AnyTLS removes it from newly rendered routing/client output but
  does not delete an old unit, container data, or certificate storage;
- removed users or renamed inventory hosts can leave old controller-side JSON
  files.

Explicitly remove stale secret files and retired target artifacts as a
separate, reviewed decommissioning operation.
