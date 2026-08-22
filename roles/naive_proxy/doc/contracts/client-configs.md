# Client configuration contract

## Files and ownership

For every `naive_proxy_users` entry, the role MUST write exactly two JSON files
on the Ansible controller:

```text
singbox-<inventory_hostname>-<user>-auto.json
singbox-<inventory_hostname>-<user>-manual.json
```

The directory is mode `0700`; files are mode `0600`. Generation is delegated
to localhost without privilege escalation. The role generates JSON only—there
are no QR artifacts or QR runtime dependencies.

“Two files” is per user in the current inventory. The role does not prune files
left by removed users or renamed inventory hosts; operators MUST remove those
stale secret files explicitly.

Implementation: [clients.yml](../../tasks/clients.yml).
Verification: [verify-clients.yml](../../molecule/shared/tasks/verify-clients.yml).

## Server map expansion

The automatic file consumes only `naive_proxy_external_ip_auto`; the manual
file consumes only `naive_proxy_external_ip_manual`. For each `<name>: <ip>`:

- `<name> - Naive` connects to `<ip>:naive_proxy_external_port`, authenticates
  with username/password, and uses `naive_proxy_domain` as TLS SNI;
- when AnyTLS is enabled, `<name> - AnyTLS` connects to the same IP/port,
  authenticates with the password secret, and uses
  `naive_proxy_anytls_domain` as TLS SNI.

Display tags MUST be derived from map keys, never IP addresses. A file contains
`2 × map size` protocol options when AnyTLS is enabled and `1 × map size` when
disabled.

Implementation: [singbox-client.json.j2](../../templates/singbox-client.json.j2).

## Selection modes

- `auto` has a top-level `proxy` outbound of type `urltest`; its URL and
  interval come from `naive_proxy_singbox_urltest_url` and
  `naive_proxy_singbox_urltest_interval`. Defaults are Google's small
  `https://www.gstatic.com/generate_204` probe and `3m`; operators MAY point
  the probe at a controlled endpoint.
- `manual` has a top-level `proxy` outbound of type `selector`; its default is
  one of the generated options.
- DNS detour and `route.final` MUST both target `proxy`, so changing selection
  mode changes the full tunnel consistently.

## TUN and leak-prevention policy

Both files contain a `tun` inbound at `172.19.0.1/30` with `auto_route`,
`strict_route`, mixed stack, and the Android captive-portal package excluded.
The generated policy:

- uses IPv4-only remote DNS to `1.1.1.1:443` over Cloudflare DoH (TLS name
  `cloudflare-dns.com`) through `proxy`;
- hijacks protocol/port-53 DNS;
- rejects global IPv6 (`2000::/3`) to prevent IPv6 leaks;
- rejects DoT on port 853;
- rejects UDP/443 so QUIC falls back to proxied TCP;
- routes remaining UDP directly because Naive/AnyTLS outbounds are TCP paths;
- routes all remaining traffic through `proxy`.

This policy is intentional API. Changing it requires a contract update and
new structural assertions for every affected rule.

Implementation: [singbox-client.json.j2](../../templates/singbox-client.json.j2).
Current verification: [verify-clients.yml](../../molecule/shared/tasks/verify-clients.yml)
asserts the shared `proxy` DNS detour and `route.final`, but does not yet assert
the complete TUN/IPv6/DoT/QUIC/remaining-UDP rule set. This limitation is
tracked in [Verification](verification.md#known-verification-gaps); do not cite
the current test as proof of every policy rule.

## uTLS option

When `naive_proxy_anytls_utls_fingerprint` is set, every AnyTLS outbound MUST
carry `tls.utls.enabled: true` and the selected fingerprint. Naive outbounds
MUST NOT receive that block. With an empty value no AnyTLS outbound carries
`tls.utls`.

Verification: structural assertions in
[verify-clients.yml](../../molecule/shared/tasks/verify-clients.yml) and the
on-wire Firefox assertion in
[singbox-anytls-benchmark.yml](../../molecule/shared/tasks/singbox-anytls-benchmark.yml).

## Compatibility

Generated files require a sing-box client build with the Naive outbound and
core AnyTLS support. The stress harness rebuilds the same released SFA core,
Go toolchain, build tags, and ABI-compatible cronet library recorded in
[base.yml](../../molecule/shared/base.yml). Version maintenance is specified in
[Updating versions](../maintenance/update-versions.md).
