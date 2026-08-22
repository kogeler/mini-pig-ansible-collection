# Runtime contract

## Traffic topology

```text
public port
    |
    v
HAProxy TCP frontend (SNI + ALPN inspection)
    |-- AnyTLS SNI ----------> sing-box :8445 (raw TCP; sing-box owns TLS)
    |-- acme-tls/1 ----------> acme.sh :10443 (Naive domain challenge)
    `-- default -------------> HAProxy TLS frontend :8444
                                  |-- valid proxy auth --> Naive :8080
                                  `-- otherwise --------> Caddy :8081
```

The AnyTLS SNI rule MUST precede the generic `acme-tls/1` rule. Consequently,
the AnyTLS domain's ACME challenge reaches sing-box, while the Naive domain's
challenge reaches acme.sh.

Implementation: [haproxy.cfg.j2](../../templates/haproxy.cfg.j2).
Verification: [singbox-anytls-verify.yml](../../molecule/shared/singbox-anytls-verify.yml),
[verify.yml](../../molecule/shared/verify.yml).

Supported HAProxy version and tuning behaviour are specified separately in
[HAProxy](haproxy.md).

## Routing invariants

### Naive path

- HAProxy terminates TLS and advertises HTTP/2 plus HTTP/1.1.
- Routing is based on `http_auth(naive_users)`, not on HTTP method.
- Authenticated requests go to the Naive backend; unauthenticated requests go
  to Caddy.
- HAProxy removes `Proxy-Authorization` only after selecting `be_naive`.
- The internal HAProxy → Naive hop is plain HTTP on loopback.
- The Naive backend has no user database and performs no authentication.

The backend accepts one proxy transaction per TCP connection. `be_naive` MUST
retain both `option http-server-close` and `http-reuse never`; removing either
can reuse a completed socket and turn a later CONNECT into
`ERR_TUNNEL_CONNECTION_FAILED`.

Implementation: [haproxy.cfg.j2](../../templates/haproxy.cfg.j2),
[backend.service.j2](../../templates/backend.service.j2).
Verification: direct HTTPS proxy and containerised official-Naive SOCKS5 paths
in [verify.yml](../../molecule/shared/verify.yml) and
[benchmark.yml](../../molecule/shared/tasks/benchmark.yml).

### AnyTLS path

- HAProxy performs raw TCP passthrough based on the distinct AnyTLS SNI.
- sing-box terminates TLS, authenticates users, and routes accepted traffic
  directly.
- AnyTLS passwords reuse `naive_proxy_users` values; usernames are present on
  the server but client outbounds authenticate with the password/secret.
- There is no AnyTLS fallback/decoy path. Invalid or unauthenticated protocol
  traffic is closed by sing-box.

Implementation: [anytls.json.j2](../../templates/anytls.json.j2),
[anytls.service.j2](../../templates/anytls.service.j2).
Verification: [singbox-anytls-verify.yml](../../molecule/shared/singbox-anytls-verify.yml)
and [singbox-anytls-benchmark.yml](../../molecule/shared/tasks/singbox-anytls-benchmark.yml).

### Decoy modes

Exactly one Caddy behaviour is rendered:

- empty `naive_proxy_decoy_upstream_url` → local `/srv/index.html` file server;
- non-empty upstream → reverse proxy with upstream `Host` rewriting and
  forwarding headers removed.

Response bodies and `Location` headers are not rewritten. Operators MUST use
an upstream that does not reveal an unwanted origin through absolute links or
redirects.

Implementation: [Caddyfile.j2](../../templates/Caddyfile.j2),
[config.yml](../../tasks/config.yml). Verification: the local and remote modes
in [verify.yml](../../molecule/shared/verify.yml).

## Component contract

All components share `naive-pod`'s network namespace.

| Container | Lifecycle | Address/port | Required purpose |
|---|---|---|---|
| `naive-haproxy` | long-running | public port, loopback `8444` | ingress, TLS for Naive, auth/routing |
| `naive-backend` | long-running | loopback `8080` | unauthenticated standalone Naive backend |
| `naive-anytls` | optional long-running | loopback `8445` | AnyTLS server and its TLS owner |
| `naive-decoy` | long-running | loopback `8081` | local or reverse-proxied decoy |
| `naive-acme` | transient | loopback `10443` while running | Naive TLS-ALPN-01 responder |
| `naive-pebble` | Molecule-only | `14000`, management `15000` | local ACME CA |

Internal names and ports are fixed in [vars/main.yml](../../vars/main.yml).
Maintained image pins live in [defaults/main.yml](../../defaults/main.yml).

## Security boundary

Production long-running container units (HAProxy, Naive, Caddy, and optional
AnyTLS) MUST:

- use `no-new-privileges` and `apparmor=unconfined`;
- drop all Linux capabilities, adding `NET_BIND_SERVICE` only to HAProxy,
  Caddy, and the Naive backend;
- use read-only root filesystems when `naive_proxy_read_only_rootfs` is true;
- mount only the configuration/state required by that component;
- write logs through Podman's passthrough driver into the systemd journal.

`apparmor=unconfined` is a deliberate compatibility trade-off for Podman on
Ubuntu hosts whose `containers-default` profile blocks required socket
operations. It does not remove the separate capability and no-new-privileges
restrictions.

Implementation: the service templates under [templates](../../templates/).
Molecule's nested-container privileges are test harness privileges and MUST NOT
be copied into production service units.

## Systemd contract

```text
podman-naive-pod.service
├── podman-naive-decoy.service
├── podman-naive-haproxy.service
├── podman-naive-backend.service
├── podman-naive-anytls.service       (when enabled)
├── podman-naive-pebble.service       (Molecule only)
└── naive-acme-renew.service
      ^
      └── naive-acme-renew.timer
```

Templates: [pod.service.j2](../../templates/pod.service.j2),
[decoy.service.j2](../../templates/decoy.service.j2),
[haproxy.service.j2](../../templates/haproxy.service.j2),
[backend.service.j2](../../templates/backend.service.j2),
[anytls.service.j2](../../templates/anytls.service.j2),
[acme-renew.service.j2](../../templates/acme-renew.service.j2).

The handler cascade MUST stop affected units, recursively poll every
`cgroup.procs` below their `--cgroups=split` systemd cgroup, and start them only
after release. Fixed sleeps or direct `state: restarted` are not equivalent and
reintroduce the `219/CGROUP` race.

Implementation: [handlers/main.yml](../../handlers/main.yml). Idempotence and
restart behaviour are exercised by every Molecule scenario; the shared
benchmark equivalent is [restart-container-unit.yml](../../molecule/shared/tasks/restart-container-unit.yml).

## Runtime limitations

- Public TLS-ALPN-01 validation requires CA reachability on public port 443, or
  forwarding from 443 to `naive_proxy_listen_port`.
- AnyTLS has no decoy/fallback.
- The backend is built on the target; this role provides no prebuilt backend
  image.
- `naive_proxy_enabled: false` is not an uninstall, and disabling AnyTLS does
  not prune its previously installed unit or persistent data.
- Generated profiles intentionally reject global IPv6 and QUIC/UDP 443; see
  [Client configurations](client-configs.md).
- Reverse-proxy decoy bodies and redirects are not rewritten.
