# TLS and ACME contract

The role operates two independent TLS owners behind one HAProxy TCP frontend.
They MUST remain independent.

Naive issuance uses [acme.sh](https://github.com/acmesh-official/acme.sh);
Molecule uses [Pebble](https://github.com/letsencrypt/pebble) as a real local
ACME test CA. AnyTLS uses sing-box's
[built-in ACME](https://sing-box.sagernet.org/configuration/shared/tls/#acme-fields).

## Naive domain

HAProxy owns the Naive domain's TLS key and certificate.

1. On first apply, the role creates an ECC key and a temporary self-signed
   certificate so HAProxy can start.
2. `naive-acme-renew.service` runs acme.sh in the pod with TLS-ALPN-01 on
   internal port `10443`.
3. HAProxy routes the Naive domain's `acme-tls/1` connection to that responder.
4. Successful issuance writes separate `fullchain.pem` and `key.pem` files and
   reloads HAProxy with `USR2`.
5. `naive-acme-renew.timer` runs daily with a randomized delay and persists
   across downtime.

HAProxy MUST use `ssl-f-use crt /certs/fullchain.pem key /certs/key.pem`; a
combined PEM is not part of the contract.

Implementation: [config.yml](../../tasks/config.yml),
[acme.yml](../../tasks/acme.yml),
[acme-renew.service.j2](../../templates/acme-renew.service.j2),
[acme-renew.timer.j2](../../templates/acme-renew.timer.j2),
[haproxy.cfg.j2](../../templates/haproxy.cfg.j2).

Verification: [verify.yml](../../molecule/shared/verify.yml) asserts a
Pebble-issued certificate, active timer, forced serial rotation, and that
HAProxy serves the rotated serial.

## AnyTLS domain

sing-box owns the AnyTLS TLS handshake and certificate. HAProxy MUST NOT
terminate this TLS stream.

The derived `_naive_proxy_anytls_acme` value is true when:

```text
naive_proxy_anytls_acme_enabled
and (not naive_proxy_molecule_mode or naive_proxy_anytls_acme_in_molecule)
```

Implementation: [vars/main.yml](../../vars/main.yml).

### ACME mode

sing-box uses its built-in ACME with:

- `disable_http_challenge: true`;
- the AnyTLS domain as the only requested domain;
- persistent `/acme-data` storage;
- `alternative_tls_port: 8445`.

The alternative port MUST equal the internal AnyTLS listener. During initial
issuance CertMagic needs to bind that port before the inbound listener exists;
on renewal the active listener answers the challenge inline. Using public port
443 would collide with HAProxy inside the shared pod.

When a custom ACME directory needs a private CA, the configured CA file is
mounted read-only and exported as `SSL_CERT_FILE`.

Implementation: [anytls.json.j2](../../templates/anytls.json.j2),
[anytls.service.j2](../../templates/anytls.service.j2).

### Static mode

When derived ACME is false, sing-box reads
`<config_dir>/anytls-certs/{fullchain,key}.pem`. If no certificate exists, the
role creates a long-lived self-signed certificate. This makes ordinary
Molecule scenarios independent of public ACME and also permits an operator to
pre-provision a static certificate.

Implementation: [config.yml](../../tasks/config.yml).

## SNI and ALPN ordering

HAProxy's TCP frontend MUST evaluate:

1. AnyTLS SNI → `be_anytls`, regardless of ALPN;
2. remaining `acme-tls/1` → `be_acme`;
3. everything else → the local Naive HTTPS frontend.

This ordering is what allows acme.sh and sing-box ACME to share one public
port. Verification: real AnyTLS Pebble issuance and served-certificate checks
in [singbox-anytls-verify.yml](../../molecule/shared/singbox-anytls-verify.yml).

## uTLS/ALPN coupling

When `naive_proxy_anytls_utls_fingerprint` is non-empty, generated clients send
a browser ClientHello offering `h2,http/1.1`. The AnyTLS server MUST then
advertise the same ALPN values. Without that derived server setting, the ACME
TLS wrapper may advertise only `acme-tls/1`, producing
`no_application_protocol` for normal AnyTLS sessions.

The role derives the server ALPN internally; operators configure only the uTLS
fingerprint. ACME continues to work because sing-box prepends the normal ALPN
values alongside the challenge protocol.

Implementation: [vars/main.yml](../../vars/main.yml),
[anytls.json.j2](../../templates/anytls.json.j2),
[singbox-client.json.j2](../../templates/singbox-client.json.j2).

Verification: [singbox-anytls-verify.yml](../../molecule/shared/singbox-anytls-verify.yml)
checks browser-ALPN and no-ALPN handshakes; the benchmark captures the real
ClientHello and checks Firefox markers in
[singbox-anytls-benchmark.yml](../../molecule/shared/tasks/singbox-anytls-benchmark.yml).
