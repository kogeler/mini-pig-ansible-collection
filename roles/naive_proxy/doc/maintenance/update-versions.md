# Updating versions

This role maintains runtime and test pins as one compatible set. “Latest” is
not sufficient: the released-SFA sing-box source, Go toolchain, build tags,
linker flags, cronet bindings, and `libcronet.so` MUST be ABI-consistent.

[`defaults/main.yml`](../../defaults/main.yml) is the single source of truth
for every runtime version and image pin. Molecule reads it (see
`molecule_naive_proxy_role_defaults` in
[`shared/vars/common.yml`](../../molecule/shared/vars/common.yml)) and MUST
NOT repeat or assert those values. The only test-owned pins are the
released-SFA stress tuple and the benchmark helpers below.

## Automated audit

```bash
cd roles/naive_proxy/molecule
make bootstrap
make versions
make versions-check
```

[`version_audit.py`](../../molecule/scripts/version_audit.py) compares local
pins with authoritative release APIs and validates the released SFA tuple. A
new upstream release may require an intentional code/test migration; do not
blindly copy the audit output and assume compatibility.

## Source map

| Component | Local pin | Authoritative source |
|---|---|---|
| Naive backend (and the Molecule test client) | [`defaults/main.yml`](../../defaults/main.yml) | [NaiveProxy releases](https://github.com/klzgrad/naiveproxy/releases) |
| sing-box AnyTLS server | [`defaults/main.yml`](../../defaults/main.yml) | latest stable [sing-box release](https://github.com/SagerNet/sing-box/releases); MUST be ≥ v1.14.0 (ACME `certificate_provider`) |
| HAProxy | [`defaults/main.yml`](../../defaults/main.yml), minor line `X.Y-alpine` | newest stable line on [Docker Hub](https://hub.docker.com/_/haproxy/tags); never older than `3.4` (haproxy#3354, see [HAProxy](../contracts/haproxy.md#version-floor)) |
| Caddy | [`defaults/main.yml`](../../defaults/main.yml), minor line `X.Y-alpine` | newest stable line on [Docker Hub](https://hub.docker.com/_/caddy/tags) |
| acme.sh | [`defaults/main.yml`](../../defaults/main.yml), explicit patch | newest `X.Y.Z` on [Docker Hub](https://hub.docker.com/r/neilpang/acme.sh/tags); the image publishes no minor-line tag |
| Pebble | [`vars/main.yml`](../../vars/main.yml) | [Pebble releases](https://github.com/letsencrypt/pebble/releases) |
| released-SFA stress tuple | [`shared/base.yml`](../../molecule/shared/base.yml) | see [Released-SFA tuple](#released-sfa-tuple) |
| BusyBox/iperf3 helpers | [`shared/vars/benchmark.yml`](../../molecule/shared/vars/benchmark.yml) | Docker Hub tags/digest checked by the audit |

HAProxy and Caddy follow a minor release line on purpose: Docker Hub retags
the line on every patch release, so a host receives upstream fixes whenever
the image is pulled (first install, or
`naive_proxy_update_runtime_images: true`). Moving to a newer line is a
deliberate pin change that `make versions` reports.

## Released-SFA tuple

The stress client represents released Android users. SFA (sing-box for
Android) ships `libbox.aar`, which sing-box's own CI builds with gomobile from
the sing-box tag SFA names. The harness rebuilds that libbox recipe as a Linux
CLI. Find each tuple member like this:

| Pin in `shared/base.yml` | Where to find it |
|---|---|
| `singbox_build_version` | SFA [`main/version.properties`](https://github.com/SagerNet/sing-box-for-android/blob/main/version.properties) `VERSION_NAME`, prefixed with `v`. The [sing-box release](https://github.com/SagerNet/sing-box/releases) of that tag MUST contain `SFA-<VERSION_NAME>-universal.apk`. |
| `singbox_build_go_version` | the same file's `GO_VERSION` without `go`. It MUST equal `setup-go` `go-version` of job `build_android_library` in sing-box `.github/workflows/build.yml` at that tag. |
| `singbox_build_tags` | `sharedTags` in sing-box `cmd/internal/build_libbox/main.go` at that tag (the main `libbox.aar` variant; `libbox-legacy.aar` omits `with_naive_outbound`), plus our `with_purego`. |
| `singbox_build_ldflags` | sing-box `release/LDFLAGS` at that tag, which `cmd/internal/build_shared/flags.go` `LinkerFlags` also uses for libbox. The Dockerfile adds the version `-X` and `-s -w -buildid=`. |
| `singbox_cronet_commit` | sing-box `.github/CRONET_GO_VERSION` at that tag. Its short SHA MUST match the `github.com/sagernet/cronet-go` pin in `go.mod`. |

Do not take the tags from `release/DEFAULT_BUILD_TAGS`. Those are the CLI
release tags: `with_acme`, `with_dhcp`, `with_ccm`, `with_ocm`, and
`with_cloudflared` are CLI-only, and the `ts_omit_*` set is libbox-only.

`libcronet.so` comes from `lib/linux_<arch>/libcronet.so` of
[cronet-go](https://github.com/sagernet/cronet-go) at exactly
`singbox_cronet_commit`. sing-box's own purego Linux release extracts the same
file. Do not substitute a cronet-go GitHub release asset: releases are cut
from other commits, and a C-ABI mismatch segfaults. For example, the sing-box
v1.14.2 pin was built from cronet-go tag `v150.0.7871.63-3`, which has no
GitHub release.

Do not use SFA `dev/version.properties` or a prerelease for the stress
client.

## Procedure

1. Run `make versions` and save the output.
2. Open the source links above and confirm release status, architecture assets,
   and compatibility notes (sing-box `docs/changelog.md`, `docs/migration.md`
   and `docs/deprecated.md` for every minor bump).
3. Change each pin in its one location. In particular:
   - the Naive pin in `defaults/main.yml` also drives the Molecule test client;
   - sing-box server independently at the latest stable server release;
   - the released-SFA version, Go, tags, linker flags, and cronet commit as one
     tuple.
4. Update explanatory source comments in `defaults/main.yml` or
   `molecule/shared/base.yml`; keep the refresh URLs there.
5. Update `version_audit.py` only when upstream metadata semantics changed,
   not to suppress a legitimate stale result.
6. Run `make versions-check` and `make lint`.
7. Select tests from the impact table below and follow
   [Testing](testing.md).
8. Search for obsolete pins throughout the role and inspect `git diff --check`.

## Test impact

| Pin changed | Required validation |
|---|---|
| released-SFA sing-box / Go / tags / ldflags / cronet | clean rebuild; `singbox-stress` + `anytls-stress`, including idempotence |
| sing-box server | `default` for ordinary deployment/config plus `anytls-stress` for ACME and traffic |
| Naive backend/client | `default` + `bookworm` + `singbox-stress`; add `anytls-stress` when shared role convergence changed |
| HAProxy | all Podman scenarios; the H2 and AnyTLS routes both depend on it |
| Caddy/acme.sh/Pebble | `default` + `bookworm`; add `anytls-stress` for Pebble or shared ACME routing |
| iperf3/BusyBox benchmark helpers | `default` + both stress scenarios |
| Python development dependencies | `make venv-recreate`, `make env-info`, lint, audit, and one representative scenario |

Already completed scenarios that do not consume the changed pin SHOULD NOT be
repeated merely for ceremony.

## Released-SFA checklist

Before accepting the tuple, confirm all of the following:

- SFA `main/version.properties` `VERSION_NAME` maps to sing-box tag `v<name>`;
- the latest stable sing-box release contains `SFA-<name>-universal.apk`;
- `GO_VERSION` (without `go`) equals the `build_android_library` Go version;
- local tags equal build_libbox `sharedTags` plus `with_purego`;
- local linker flags equal `release/LDFLAGS`;
- sing-box `go.mod` cronet short SHA agrees with `.github/CRONET_GO_VERSION`,
  and that commit contains `lib/linux_amd64/libcronet.so`;
- the version banner printed by the stress verify shows the expected version,
  Go toolchain, tags, and `CGO: disabled` (reviewed in the log, not asserted);
- `with_purego` still loads the ABI-matched `libcronet.so`, and the released-SFA
  toolchain emits a glibc-interpreted executable for this tuple; retain a
  glibc-based client image unless inspection of the new artifact proves that
  requirement changed;
- both stress tests prove actual TUN movement and clean journals, and the
  generated client configs pass `sing-box check` with the stress build.

`make versions` automates every item that upstream metadata can prove.
