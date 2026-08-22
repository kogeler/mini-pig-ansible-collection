# Interface contract

## Supported execution model

The role MUST run on a Debian-family target with systemd and root privileges.
It installs Podman/Buildah and supporting packages itself, builds the Naive
backend image on the target, and manages the resulting pod and containers with
systemd.

Implementation: [install.yml](../../tasks/install.yml),
[main.yml](../../tasks/main.yml), [Containerfile.j2](../../templates/Containerfile.j2).

When `naive_proxy_enabled: false`, every role phase MUST be skipped. This is a
no-op switch, not an uninstall operation: an existing deployment keeps its
current services and files. When it is true, the phase order is preflight →
install → optional image refresh → backend build → configuration → services →
ACME → client configuration → healthchecks.

Implementation: [main.yml](../../tasks/main.yml).

## Required inputs

| Variable | Contract |
|---|---|
| `naive_proxy_domain` | Non-empty FQDN for the Naive endpoint. |
| `naive_proxy_anytls_domain` | Required when AnyTLS is enabled; MUST be non-empty and different from `naive_proxy_domain`. Both names are expected to resolve to the same public addresses. |
| `naive_proxy_external_ip_auto` | Non-empty `<display name>: <connect address>` map used only by the automatic client configuration. Values MUST be non-empty strings. |
| `naive_proxy_external_ip_manual` | Same shape as `_auto`, used only by the manual client configuration. The two maps MAY differ. |
| `naive_proxy_users` | Non-empty `username: password` map. HAProxy authenticates Naive requests; passwords are also AnyTLS secrets. The Naive backend itself remains unauthenticated. |
| `naive_proxy_listen_port` | Integer in `1..65535`, distinct from every active internal role port. |
| `naive_proxy_external_port` | Integer in `1..65535`; advertised to clients and MAY differ from the local listen port. |

Implementation and validation: [defaults/main.yml](../../defaults/main.yml),
[preflight.yml](../../tasks/preflight.yml), [vars/main.yml](../../vars/main.yml).

The role validates shapes and port conflicts but does not perform public DNS
lookups; DNS equality/reachability is an operator responsibility.

## Public variables

The complete default set lives in [defaults/main.yml](../../defaults/main.yml).
The groups below define their supported meaning; variables beginning with
`_naive_proxy_` in [vars/main.yml](../../vars/main.yml) are internal and MUST
NOT be treated as operator API.

### General and paths

| Variable | Default | Contract |
|---|---:|---|
| `naive_proxy_enabled` | `true` | Master role switch. |
| `naive_proxy_config_dir` | `/opt/naive-proxy` | Target-side state, configs, certificates, and build context. |
| `naive_proxy_client_config_dir` | `{{ playbook_dir }}/naive-proxy-json-configs` | Controller-side directory for generated JSON files. |
| `naive_proxy_pod_network` | `""` | Optional existing Podman network; the role never creates or deletes it. |
| `naive_proxy_listen_port` | `443` | Host port published by the Podman pod. |
| `naive_proxy_external_port` | listen port | Port written to client configurations. |
| `naive_proxy_molecule_mode` | `false` | Test-only runtime mode; MUST NOT be enabled in production. |

### Naive backend and container options

| Variable | Default | Contract |
|---|---:|---|
| `naive_proxy_naive_version` | release pin | NaiveProxy release used to build the local backend image. |
| `naive_proxy_padding` | `true` | Adds `--padding` to the backend process. |
| `naive_proxy_backend_base_image` / `naive_proxy_backend_base_image_tag` | Ubuntu `22.04` | Base of the locally built backend image. |
| `naive_proxy_backend_extra_env` | `{}` | Additional Podman container environment. |
| `naive_proxy_backend_extra_volumes` | `[]` | Additional Podman volume arguments. |
| `naive_proxy_backend_extra_args` | `[]` | Podman flags inserted before the image name. |
| `naive_proxy_backend_naive_args` | `[]` | Naive process flags appended after the image name. |
| `naive_proxy_read_only_rootfs` | `true` | Enables read-only root filesystems for long-running containers. |
| `naive_proxy_selinux_relabel` | `false` | Adds `:Z` to managed bind mounts. |

Implementation: [image.yml](../../tasks/image.yml),
[backend.service.j2](../../templates/backend.service.j2).

### Runtime images

The explicit image variables are:

| Component | Repository variable | Tag variable |
|---|---|---|
| HAProxy | `naive_proxy_haproxy_image` | `naive_proxy_haproxy_image_tag` |
| Caddy decoy | `naive_proxy_decoy_image` | `naive_proxy_decoy_image_tag` |
| acme.sh | `naive_proxy_acme_image` | `naive_proxy_acme_image_tag` |
| sing-box AnyTLS | `naive_proxy_singbox_image` | `naive_proxy_singbox_image_tag` |

Maintained tags MUST stay pinned; updating them follows
[the version runbook](../maintenance/update-versions.md).

`naive_proxy_update_runtime_images` is opt-in. It refreshes only HAProxy and
Caddy and queues a restart only when the resolved image ID changed. It MUST NOT
pull the locally built backend, acme.sh, Pebble, or sing-box.

Implementation: [utils.yml](../../tasks/utils.yml). Verification:
[shared/utils.yml](../../molecule/shared/utils.yml).

### AnyTLS

| Variable | Default | Contract |
|---|---:|---|
| `naive_proxy_anytls_enabled` | `true` | Deploys the AnyTLS service and adds AnyTLS client options. |
| `naive_proxy_anytls_log_level` | `info` | sing-box server log level. |
| `naive_proxy_anytls_acme_enabled` | `true` | Built-in sing-box ACME when true; static certificate mode when false. |
| `naive_proxy_anytls_acme_provider` | `letsencrypt` | Provider/directory passed to sing-box ACME. |
| `naive_proxy_anytls_acme_email` | Naive ACME email | AnyTLS ACME account email. |
| `naive_proxy_anytls_utls_fingerprint` | `""` | Optional client-only uTLS fingerprint; accepted values are validated against the internal allow-list. |
| `naive_proxy_anytls_acme_in_molecule` | `false` | Test-only override that enables real Pebble ACME in Molecule. |
| `naive_proxy_anytls_acme_directory_ca` | `""` | Test/custom-directory CA mounted for sing-box ACME trust. |

Certificate semantics are defined in [TLS and ACME](tls-and-acme.md).

Changing `naive_proxy_anytls_enabled` from true to false removes the route and
future client options, but the role does not delete a previously installed
AnyTLS unit, container state, or certificate data. Decommission those artifacts
explicitly when disabling an existing deployment.

### Decoy, ACME, tuning, and diagnostics

- `naive_proxy_decoy_index_html` selects a controller file for local decoy
  mode; `naive_proxy_decoy_upstream_url` selects reverse-proxy mode and wins
  when both are set.
- `naive_proxy_acme_email` and `naive_proxy_acme_server` configure the Naive
  domain's acme.sh client.
- `naive_proxy_haproxy_timeout_*`, `_global_maxconn`, `_cpu_policy`,
  `_ssl_cache_size`, `_h2_frontend_rxbuf`, `_h2_initial_window_size`,
  `_h2_max_frame_size`, and `_notsent_lowat` map directly to the rendered
  HAProxy configuration. Their exact defaults, units, version requirements,
  and omission semantics are in the [HAProxy contract](haproxy.md).
- `naive_proxy_haproxy_diagnostics_enabled`, `_port`, and `_ring_size` expose a
  loopback-only admin socket and H2 trace ring. Enabling them recreates the pod
  because the published-port set changes.
- `naive_proxy_singbox_urltest_url` and
  `naive_proxy_singbox_urltest_interval` affect only automatic client
  configurations.

Implementation: [Caddyfile.j2](../../templates/Caddyfile.j2),
[haproxy.cfg.j2](../../templates/haproxy.cfg.j2),
[pod.service.j2](../../templates/pod.service.j2),
[singbox-client.json.j2](../../templates/singbox-client.json.j2).

## Outputs

The role owns the following target-side surface:

- `naive_proxy_config_dir` and its managed subdirectories;
- systemd units named in [Runtime](runtime.md#systemd-contract);
- local backend image `localhost/naive-backend:<naive version>`;
- the Podman pod and containers named in [Runtime](runtime.md#component-contract).

It also writes exactly two mode-specific JSON files per user on the controller.
Their contract is in [Client configurations](client-configs.md). The role does
not generate QR codes or require QR/Pillow dependencies.

## Tags

Both hyphenated and underscore aliases are accepted by implementation; the
documented public tags are:

| Tag | Phase |
|---|---|
| `naive-proxy` | all role tasks |
| `naive-proxy-preflight` | validation |
| `naive-proxy-install` | packages/directories |
| `naive-proxy-utils` | opt-in image refresh |
| `naive-proxy-image` | backend image build |
| `naive-proxy-config` | configs, certificates, units |
| `naive-proxy-services` | service activation |
| `naive-proxy-acme` | Naive certificate issuance/timer |
| `naive-proxy-clients` | controller-side client JSON |
| `naive-proxy-healthchecks` | post-deploy checks |

Implementation: [main.yml](../../tasks/main.yml).
