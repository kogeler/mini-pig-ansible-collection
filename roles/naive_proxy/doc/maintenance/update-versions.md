# Updating versions

This role maintains runtime and test pins as one compatible set. “Latest” is
not sufficient: the released-SFA sing-box source, Go toolchain, build tags,
cronet bindings, and `libcronet.so` MUST be ABI-consistent.

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
| Naive backend | [`defaults/main.yml`](../../defaults/main.yml) | [NaiveProxy releases](https://github.com/klzgrad/naiveproxy/releases) |
| Naive test client | [`shared/vars/common.yml`](../../molecule/shared/vars/common.yml) | same Naive release; MUST equal backend |
| sing-box AnyTLS server | [`defaults/main.yml`](../../defaults/main.yml) | latest stable [sing-box release](https://github.com/SagerNet/sing-box/releases) |
| released-SFA stress core + Go | [`shared/base.yml`](../../molecule/shared/base.yml) | SFA [`main/version.properties`](https://github.com/SagerNet/sing-box-for-android/blob/main/version.properties); matching SFA APK MUST exist in the sing-box release |
| stress build tags | [`shared/base.yml`](../../molecule/shared/base.yml) | matching sing-box `release/DEFAULT_BUILD_TAGS`, plus local `with_purego` only |
| cronet bindings | matching sing-box `go.mod` and `.github/CRONET_GO_VERSION` | matching sing-box tag |
| `libcronet.so` | [`shared/base.yml`](../../molecule/shared/base.yml) | [cronet-go releases](https://github.com/sagernet/cronet-go/releases), Chromium revision matching the bindings |
| HAProxy/Caddy/acme.sh | [`defaults/main.yml`](../../defaults/main.yml) | project releases/container tags checked by the audit |
| Pebble | [`vars/main.yml`](../../vars/main.yml) | Pebble releases |
| BusyBox/iperf3 helpers | [`shared/vars/benchmark.yml`](../../molecule/shared/vars/benchmark.yml) | Docker Hub tags/digest checked by the audit |

Do not use SFA `dev/version.properties` or “latest prerelease” for the stress
client. The harness represents released Android users and therefore follows
SFA `main` plus the matching published APK.

## Procedure

1. Run `make versions` and save the output.
2. Open the source links above and confirm release status, architecture assets,
   and compatibility notes.
3. Update all duplicate pins that form one contract. In particular:
   - Naive backend and test client together;
   - sing-box server independently at the latest stable server release;
   - released-SFA sing-box, Go, default tags, cronet commit, and cronet release
     as one tuple.
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
| released-SFA sing-box / Go / tags / cronet | clean rebuild; `singbox-stress` + `anytls-stress`, including idempotence |
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
- `GO_VERSION` is used without the `go` prefix;
- the latest stable sing-box release contains `SFA-<name>-universal.apk`;
- local tags equal that tag's `DEFAULT_BUILD_TAGS` plus `with_purego`;
- sing-box `go.mod` cronet short SHA agrees with `.github/CRONET_GO_VERSION`;
- the selected cronet-go release provides the ABI-matching Linux library;
- the built binary banner reports the exact version, Go toolchain, tags, and
  `CGO: disabled`;
- `with_purego` still loads the ABI-matched `libcronet.so`, and the released-SFA
  toolchain emits a glibc-interpreted executable for this tuple; retain a
  glibc-based client image unless inspection of the new artifact proves that
  requirement changed;
- both stress tests prove actual TUN movement and clean journals.
