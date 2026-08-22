# Testing

## Toolchain

All Molecule lifecycle and role-local toolchain commands run through
[`molecule/Makefile`](../../molecule/Makefile). It owns
`molecule/.venv`, starts Molecule from the role directory, and lets
ansible-compat discover the collection natively through the repository
`galaxy.yml`.

```bash
cd roles/naive_proxy/molecule
make bootstrap       # create the local venv
make env-info
make help
```

Use `make venv-refresh` to upgrade an existing environment or
`make venv-recreate` for a clean rebuild from the latest compatible PyPI
packages in [`requirements-dev.txt`](../../molecule/requirements-dev.txt).

Do not set `ANSIBLE_COLLECTIONS_PATH` or `GIT_DIR`; the Makefile rejects both
because either can shadow the checkout with stale collection content.

## Non-negotiable execution rules

1. Never invoke bare `molecule`; use a Make target.
2. Redirect complete Molecule output to a file. Never pipe the live command
   through `tail`, `grep`, or another truncating process.
3. During development, run `converge`, `verify`, and `idempotence` separately
   and keep the instance alive for diagnosis.
4. Use `destroy` only for a suspect instance or a required clean image rebuild.
5. Use `*-test` only for final clean lifecycle validation; it destroys the
   instance.
6. Never run `gha-native` on a workstation. It applies the role directly to
   the host and is reserved for the disposable GitHub Actions runner.
7. Never execute downloaded Naive/sing-box/iperf binaries on the host. Test
   clients run inside the Molecule instance and nested containers.

The official Naive client is containerised deliberately: past Ubuntu runner
tests exposed host-specific Chromium networking failures that were unrelated
to the deployed path. Containerisation also keeps trust stores, processes, and
cleanup inside the scenario boundary.

Example:

```bash
make default-podman-converge > /tmp/naive-default-converge.log 2>&1
make default-podman-verify > /tmp/naive-default-verify.log 2>&1
make default-podman-idempotence > /tmp/naive-default-idempotence.log 2>&1

rg -n 'PLAY RECAP|SCENARIO RECAP|failed=[1-9]|FATAL|ERROR' \
  /tmp/naive-default-*.log
```

## Scenario commands

```bash
# Full Naive contract on Debian trixie
make default-podman-converge > /tmp/default-converge.log 2>&1
make default-podman-verify > /tmp/default-verify.log 2>&1
make default-podman-idempotence > /tmp/default-idempotence.log 2>&1

# Supported older Debian base
make bookworm-podman-converge > /tmp/bookworm-converge.log 2>&1
make bookworm-podman-verify > /tmp/bookworm-verify.log 2>&1
make bookworm-podman-idempotence > /tmp/bookworm-idempotence.log 2>&1

# Released-SFA sing-box Naive/TUN stress
make singbox-stress-podman-converge > /tmp/singbox-converge.log 2>&1
make singbox-stress-podman-verify > /tmp/singbox-verify.log 2>&1
make singbox-stress-podman-idempotence > /tmp/singbox-idempotence.log 2>&1

# AnyTLS ACME/uTLS/TUN stress
make anytls-stress-podman-converge > /tmp/anytls-converge.log 2>&1
make anytls-stress-podman-verify > /tmp/anytls-verify.log 2>&1
make anytls-stress-podman-idempotence > /tmp/anytls-idempotence.log 2>&1
```

`MP_NETWORK` selects the outer rootless Podman network and defaults to
`slirp4netns`. Scenario files use the `network` key; `network_mode` is ignored
by the Molecule Podman driver. Supported choices are `host`, `slirp4netns`,
`bridge`, and `pasta` (Podman 5+); record a non-default choice with the test
result because it changes the outer connectivity boundary.

## Scenario configuration model

[`shared/base.yml`](../../molecule/shared/base.yml) is loaded explicitly by
the Makefile and each scenario's `molecule.yml` is merged on top. Molecule
deep-merges mappings but replaces lists. Keep shared mappings in the base;
keep `platforms` and any scenario-specific replacement list in the scenario.
Use the current `ansible.*` schema—mixing legacy `provisioner.playbooks/env`
with it can cause migration-time replacement instead of the intended merge.

`mp_driver` is the branch selector for shared prepare logic. The base inventory
sets `podman` in host vars, while `gha/inventory/hosts.yml` sets `native` for
localhost. Do not move it into `vars_files`: that higher-precedence value would
mask the scenario override. Podman scenarios use platform `etc_hosts` because
their `/etc/hosts` is a bind mount that `lineinfile` cannot atomically replace;
the native GHA scenario uses `lineinfile` on the real runner file.

When `naive_proxy_molecule_mode: true`, the role:

- deploys Pebble and publishes its ACME/management ports `14000`/`15000`;
- points acme.sh at Pebble with `--insecure` and `--force`;
- enables stage-wise HAProxy request/response debug logging;
- uses a static self-signed AnyTLS certificate unless the scenario explicitly
  enables `naive_proxy_anytls_acme_in_molecule`, in which case sing-box obtains
  a real Pebble certificate using the mounted test CA.

Every scenario included in the GitHub Actions matrix has an `ENABLE_CI` marker.
The workflow currently calls Molecule directly inside its disposable checkout;
the Makefile is the mandatory local/agent interface and mirrors the Podman
matrix through `make ci-podman`.

## Impact matrix

Use the smallest set that can observe the change.

| Change | Minimum relevant scenarios |
|---|---|
| documentation/comments only | link/static checks; `make lint` when YAML/Jinja comments changed |
| preflight, common tasks, HAProxy, Caddy, Naive backend, handlers | `default`; add `bookworm` for package/runtime portability |
| Debian package list or nested-container runtime | `default` + `bookworm` |
| generated client JSON | `default`; add `anytls-stress` for uTLS-specific shape |
| Naive certificate/acme.sh/timer | `default` (and `bookworm` when package-sensitive) |
| AnyTLS server, SNI, ACME, ALPN, uTLS | `anytls-stress`; add `default` if default/static mode changed |
| released-SFA sing-box build, Go, tags, cronet | `singbox-stress` + `anytls-stress` |
| Naive H2/TUN transport | `singbox-stress` |
| benchmark helpers shared by all transports | `default` + both stress scenarios |
| Makefile, venv, native collection discovery | `make venv-recreate`, `make lint`, one representative converge |

Run all four Podman scenarios only when shared role behaviour or the harness
affects all of them:

```bash
make ci-podman > /tmp/naive-ci-podman.log 2>&1
```

## Benchmark and stress topology

The official Naive benchmark is the control: a containerised official client
listens on SOCKS5 `127.0.0.1:1080` and carries a 30-second, 16-stream iperf3
run. Shared knobs live in
[`vars/benchmark.yml`](../../molecule/shared/vars/benchmark.yml); change them
only when the test objective requires it and record the override.

Both sing-box stress scenarios use a real TUN device in the client container's
own network namespace. The iperf sidecar joins that namespace, traffic crosses
ssl_router on port 443 before HAProxy, and a `/32` route pins the destination
through TUN. Verification compares interface-byte movement with iperf bytes so
a connected bridge route cannot produce a false pass. The Naive stress log
scan targets the reported H2/proxy errors; the AnyTLS scan targets resets,
authentication, certificate, and ALPN failures. Their throughput floor is kept
at 1 Mbps because transport correctness, not peak speed, is the assertion.

## Focused playbooks on a live instance

The old documentation required finding a private
`~/.ansible/tmp/molecule.<id>...` inventory. The Makefile now resolves that
state through Molecule and refuses an instance that is not converged:

```bash
make default-podman-benchmark > /tmp/naive-benchmark.log 2>&1
make bookworm-podman-benchmark > /tmp/naive-bookworm-benchmark.log 2>&1
make singbox-stress-podman-benchmark > /tmp/naive-singbox-benchmark.log 2>&1
make anytls-stress-podman-benchmark > /tmp/naive-anytls-benchmark.log 2>&1

make default-podman-runtime-refresh > /tmp/naive-runtime-refresh.log 2>&1
make bookworm-podman-runtime-refresh > /tmp/naive-bookworm-refresh.log 2>&1
```

These targets rerun only the throughput or image-refresh playbook and keep the
instance alive. They are diagnostic/maintenance tools, not substitutes for the
scenario's normal `verify` and `idempotence` evidence.

## Nested runtime and clean image rebuild

Podman scenario images install `crun` explicitly. Debian trixie's `runc` cannot
remount `/dev` read-only for this nested rootless-user-namespace layout. The
role's backend build therefore keeps Buildah's working OCI isolation and uses
`--network=host`; do not force `--isolation=chroot`, which reaches the same
failing `/dev` remount path.

The `default` and two stress scenarios use different Dockerfile content but
share Molecule's local trixie image tag. The stress scenarios share
[`shared/Dockerfile.j2`](../../molecule/shared/Dockerfile.j2). After changing
sing-box/Go/build tags/cronet, destroy every live scenario that can hold that
tag, inspect it, and remove only the exact image before converging the stress
scenarios:

```bash
make default-podman-destroy > /tmp/default-destroy.log 2>&1
make singbox-stress-podman-destroy > /tmp/singbox-destroy.log 2>&1
make anytls-stress-podman-destroy > /tmp/anytls-destroy.log 2>&1
podman image inspect localhost/molecule_local/docker.io/library/debian:trixie
podman image rm localhost/molecule_local/docker.io/library/debian:trixie
```

Resolve the exact target first and never use broad image pruning as part of the
role workflow.

## What success means

- Every recap has `failed=0`.
- Idempotence has `changed=0`.
- Stress banners show the configured sing-box and Go versions.
- TUN byte assertions pass; throughput without TUN movement is a failed test.
- Journal marker scans are empty.
- `anytls-stress` proves Pebble issuer, browser/no-ALPN negotiation, and uTLS
  ClientHello markers.

Coverage details and known gaps are in
[`../contracts/verification.md`](../contracts/verification.md).
