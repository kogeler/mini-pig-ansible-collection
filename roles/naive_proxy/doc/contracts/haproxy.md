# HAProxy contract

HAProxy is the public ingress, Naive TLS owner, HTTP proxy authenticator, and
decoy router. Traffic ownership and backend reuse invariants are defined in
[Runtime](runtime.md); this contract owns supported tuning and diagnostics.

Implementation: [defaults/main.yml](../../defaults/main.yml) and
[haproxy.cfg.j2](../../templates/haproxy.cfg.j2).
Directive semantics are defined by the
[HAProxy 3.4 configuration manual](https://docs.haproxy.org/3.4/configuration.html).

## Version floor

The maintained image tag MUST be an explicit patch release and MUST include
the fix for [haproxy/haproxy#3354](https://github.com/haproxy/haproxy/issues/3354),
the HTTP/2 padded-DATA drain bug observed by this role. Do not lower the pin
below HAProxy `3.3.10`; `3.3.9` and older 3.3 releases, as well as the currently
unfixed 3.2/3.0/2.8 maintenance lines, retain the fault.

The upstream fix is
[`faf3e9a`](https://github.com/haproxy/haproxy/commit/faf3e9ac3a5df7258b0abbc06b0e0378617a18e5)
and its 3.3 backport is `043db34`. A later explicit stable patch is the normal
upgrade path. Verify the image banner and rerun all Podman scenarios after a
pin change; see [Updating versions](../maintenance/update-versions.md).

For HAProxy 2.x through 3.2 tags, the template emits defensive `no-quic` to
avoid startup checks in builds compiled with QUIC. HAProxy 3.3 removed that
directive, so it MUST NOT be rendered for 3.3 or newer tags. The role does not
configure a QUIC listener.

Verification: image/config assertions in
[verify.yml](../../molecule/shared/verify.yml), transport load in
[singbox-benchmark.yml](../../molecule/shared/tasks/singbox-benchmark.yml),
and the production investigation workflow in
[Debugging](../maintenance/debugging.md).

## Timeout defaults

| Variable | Default | Rendered directive |
|---|---:|---|
| `naive_proxy_haproxy_timeout_connect` | `5s` | `timeout connect` |
| `naive_proxy_haproxy_timeout_client` | `60s` | `timeout client` |
| `naive_proxy_haproxy_timeout_server` | `60s` | `timeout server` |
| `naive_proxy_haproxy_timeout_tunnel` | `3600s` | `timeout tunnel` |
| `naive_proxy_haproxy_timeout_client_fin` | `30s` | `timeout client-fin` |
| `naive_proxy_haproxy_timeout_server_fin` | `30s` | `timeout server-fin` |

Tunnel timeout governs established CONNECT sessions. FIN timeouts bound
half-closed connections independently of the main client/server timers.

## Global and H2 tuning

| Variable | Default | Semantics |
|---|---:|---|
| `naive_proxy_haproxy_global_maxconn` | `0` | Positive values render `maxconn`; zero omits it. |
| `naive_proxy_haproxy_cpu_policy` | `performance` | Non-empty values render HAProxy 3.2+ `cpu-policy`; empty uses HAProxy's own policy. |
| `naive_proxy_haproxy_ssl_cache_size` | `40000` | Positive values render `tune.ssl.cachesize`; zero omits it. HAProxy documents roughly 200 bytes per block. |
| `naive_proxy_haproxy_h2_frontend_rxbuf` | `6m` | Per-stream frontend receive buffer; empty omits the directive. HAProxy's own default is `1600k`. |
| `naive_proxy_haproxy_h2_initial_window_size` | `1048576` | Positive values render the per-stream initial window; zero leaves the RFC/default behaviour. Requires HAProxy 3.0+. |
| `naive_proxy_haproxy_h2_max_frame_size` | `0` | Positive values override the 16 KiB default; zero omits it. Requires HAProxy 3.0+. |
| `naive_proxy_haproxy_notsent_lowat` | `0` | Positive values render client and server Linux socket low-water marks; zero omits both. |

HAProxy size suffixes `k`, `m`, and `g` are binary units. For a first rxbuf
estimate use `bandwidth_mbps * rtt_ms * 125`, then account for the per-stream
memory cost and verify on the real workload.

The initial window is now a throughput setting only. The former 1 MiB maximum
frame workaround was removed after #3354 was fixed: oversized frames can add
head-of-line blocking, so raise `naive_proxy_haproxy_h2_max_frame_size` only
with benchmark evidence. `naive_proxy_haproxy_notsent_lowat` is Linux-specific
and intentionally disabled by default for the same reason.

Molecule asserts that the configured CPU/cache/FIN/rxbuf directives render.
It does not benchmark every alternate numeric value; performance and memory
sizing remain operator validation on representative hardware.

## Diagnostics

Diagnostics are opt-in:

| Variable | Default | Contract |
|---|---:|---|
| `naive_proxy_haproxy_diagnostics_enabled` | `false` | Controls both the admin socket and `h2trace` ring. |
| `naive_proxy_haproxy_diagnostics_port` | `19999` | Published only on host `127.0.0.1`; internally HAProxy binds the pod-wide address. |
| `naive_proxy_haproxy_diagnostics_ring_size` | `134217728` | Trace ring bytes; 128 MiB is sized for a bounded complete-verbosity investigation. |

Toggling diagnostics changes a pod-published port, so the role recreates the
pod through its handler cascade rather than reloading only HAProxy. The socket
is admin-level; it MUST remain loopback-only. Complete H2 trace verbosity can
contain plaintext credentials after TLS termination and MUST be handled as a
secret. A 32 MiB ring can be sufficient for bounded `advanced` traces on a
memory-constrained host; verify the report's dropped-event count before
trusting a reduced ring.

Implementation: [pod.service.j2](../../templates/pod.service.j2) and
[haproxy.cfg.j2](../../templates/haproxy.cfg.j2). Verification:
[verify-diagnostics.yml](../../molecule/shared/tasks/verify-diagnostics.yml).
Operational procedure: [Production debug scripts](../scripts/README.md).
