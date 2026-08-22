# naive_proxy — agent entry point

This file is the short operational context for agents changing this role. Do
not expand it into a second design document. Read the linked contract or
runbook for the task at hand.

## Start here

1. Read the relevant [role contract](doc/contracts/README.md).
2. Follow [Changing the role](doc/maintenance/change-workflow.md).
3. Select validation through [Testing](doc/maintenance/testing.md).
4. Preserve unrelated working-tree changes.

Use these task-specific runbooks:

| Work | Read |
|---|---|
| runtime, routing, units, handlers | [Runtime contract](doc/contracts/runtime.md) |
| variables, tags, outputs | [Interface contract](doc/contracts/interface.md) |
| certificates, SNI, ALPN, uTLS | [TLS and ACME contract](doc/contracts/tls-and-acme.md) |
| HAProxy version/tuning/diagnostics | [HAProxy contract](doc/contracts/haproxy.md) |
| generated sing-box JSON | [Client-config contract](doc/contracts/client-configs.md) |
| test coverage or scenario choice | [Verification contract](doc/contracts/verification.md) and [Testing](doc/maintenance/testing.md) |
| binary/image/toolchain updates | [Updating versions](doc/maintenance/update-versions.md) |
| a failed scenario or production H2 issue | [Debugging](doc/maintenance/debugging.md); use [CI debugging](doc/maintenance/ci-debugging.md) for workflow instrumentation |

## Mandatory local/agent Molecule rules

All local and agent-driven commands run through `molecule/Makefile` from
`roles/naive_proxy/molecule`. The repository's disposable CI workflow may
invoke its selected scenario directly.

1. Never invoke bare `molecule`.
2. Never set `ANSIBLE_COLLECTIONS_PATH` or `GIT_DIR`. ansible-compat discovers
   `../../galaxy.yml` from the checkout. Do not recreate a private collection
   tree, copy the collection, or add a self-symlink installer.
3. Use the role-local `.venv`: `make bootstrap`, `make venv-refresh`, or
   `make venv-recreate`. Manual activation is optional.
4. Redirect the complete Molecule stream to a log file. Never truncate the
   live process through `tail`, `grep`, or a similar pipe.
5. During development run `converge`, `verify`, and `idempotence` separately.
   Keep a failed instance alive until evidence is collected.
6. Use `destroy` only for suspect state or an explicitly required clean image
   rebuild. `*-test` is for final clean lifecycles and destroys the instance.
7. Never run `gha-native` on a workstation; it mutates the host and is only for
   the disposable GitHub Actions runner.
8. Never execute downloaded Naive, sing-box, cronet, or benchmark binaries on
   the development host. They run inside the Molecule instance/containers.

Typical development sequence:

```bash
cd roles/naive_proxy/molecule
make bootstrap
make lint
make default-podman-converge > /tmp/naive-converge.log 2>&1
make default-podman-verify > /tmp/naive-verify.log 2>&1
make default-podman-idempotence > /tmp/naive-idempotence.log 2>&1
rg -n 'PLAY RECAP|SCENARIO RECAP|failed=[1-9]|FATAL|ERROR' /tmp/naive-*.log
```

The full target list and scenario impact matrix are in
[Testing](doc/maintenance/testing.md); `make help` is the executable command
reference.

## Critical invariants

- AnyTLS SNI routing precedes the generic `acme-tls/1` branch. sing-box owns
  AnyTLS TLS/ACME; HAProxy and acme.sh own Naive TLS/ACME.
- Naive authentication lives in HAProxy. The backend remains unauthenticated,
  and HAProxy strips `Proxy-Authorization` only after route selection.
- `be_naive` retains both `option http-server-close` and `http-reuse never`.
  The standalone backend accepts one proxy transaction per TCP connection.
- The handler cascade waits for all child `cgroup.procs` to clear before
  restarting `--cgroups=split` Podman units. Do not replace it with fixed
  sleeps or direct `state: restarted`.
- Generated client profiles are controller-side secret files: directory mode
  `0700`, file mode `0600`. Each current user gets `auto` and `manual` JSON.
- Client stress success requires TUN byte movement, not throughput alone. A
  connected container route can otherwise bypass the proxy.
- The released-SFA stress binary follows SFA `main/version.properties` and its
  released APK tuple. It is independent of the latest stable server image pin.
- Do not restore QR generation or `qrcode[pil]` to this role or its tests. The
  collection's standalone QR plugin files are outside this role and MUST NOT
  be removed during role maintenance.

Rationale, implementation links, and test evidence live in the contracts, not
here.

## Source map

| Area | Primary files |
|---|---|
| defaults and public API | `defaults/main.yml`, `tasks/preflight.yml` |
| derived/internal state | `vars/main.yml` |
| phase ordering and tags | `tasks/main.yml` |
| runtime implementation | `tasks/*.yml`, `templates/*.j2`, `handlers/main.yml` |
| generated client profiles | `tasks/clients.yml`, `templates/singbox-client.json.j2` |
| shared Molecule inputs | `molecule/shared/base.yml`, `molecule/shared/vars/` |
| shared verification | `molecule/shared/verify.yml`, `molecule/shared/tasks/`, `molecule/shared/*-verify.yml` |
| local automation and pins audit | `molecule/Makefile`, `molecule/scripts/version_audit.py`, `molecule/scripts/run_scenario_playbook.py` |
| production diagnostic helpers | `doc/scripts/` |

Scenario directories should contain only scenario-specific topology or
overrides. Put reusable convergence, verification, and benchmark logic under
`molecule/shared/`.

## Change expectations

A behavioural change is vertical:

```text
default/validation -> implementation -> live assertion -> contract
```

Add operator-facing examples to `README.md` only when they help a normal role
consumer. Put stable semantics in `doc/contracts/` and repeatable procedures in
`doc/maintenance/`. Update comments next to version pins when their upstream
source or compatibility tuple changes.

For every implementation change:

- run `make lint`;
- run the smallest relevant scenario set from the impact matrix;
- run idempotence when tasks, templates, handlers, images, or units changed;
- inspect full logs and require `failed=0`; idempotence requires `changed=0`;
- run `git diff --check` and verify changed documentation links.

Documentation-only changes need link/static checks, plus `make lint` when YAML
or Jinja comments changed. Do not rerun completed scenarios that cannot observe
the new change.
