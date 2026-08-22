# Changing the role

## Before editing

1. Read the relevant contract under [`../contracts/`](../contracts/README.md).
2. Locate the implementation and existing assertion linked by that contract.
3. Check the working tree and preserve unrelated changes.
4. Use [`testing.md`](testing.md) to bootstrap the role-local toolchain.

## Keep the change vertical

A behavioural change SHOULD contain all affected layers:

```text
defaults/vars → preflight → task/template/unit/handler → Molecule assertion
              → contract → operator README (only if operator-facing)
```

Examples:

- A new public variable needs a documented default, validation where
  meaningful, implementation, a contract entry, and a scenario that exercises
  both its default and non-default shape.
- A routing change needs the HAProxy/sing-box template change plus a real
  request/handshake assertion; rendering-only assertions are insufficient for
  transport behaviour.
- A unit or handler change needs idempotence and a live restart/converge check,
  not only lint.
- A generated-client change needs structural assertions in
  [`verify-clients.yml`](../../molecule/shared/tasks/verify-clients.yml).

Do not place long design histories in `README.md` or `AGENTS.md`. Put stable
behaviour in a contract and repeatable operational knowledge in a runbook.

## Implementation invariants to preserve

- Keep AnyTLS SNI routing before the generic ACME ALPN route.
- Keep `http-reuse never` and `option http-server-close` on `be_naive`.
- Keep Naive authentication in HAProxy; the backend stays unauthenticated.
- Keep AnyTLS TLS ownership in sing-box and Naive TLS ownership in HAProxy.
- Keep handler cgroup-release polling before container restarts.
- Keep controller-generated client files mode `0600` and directory mode
  `0700`.
- Do not restore QR generation or a `qrcode[pil]` role/test dependency. The
  collection's standalone QR plugins are outside this role's contract and
  MUST NOT be deleted as part of role maintenance.
- Never run downloaded test binaries directly on the development host.

The detailed rationale and evidence are in
[`../contracts/runtime.md`](../contracts/runtime.md),
[`../contracts/tls-and-acme.md`](../contracts/tls-and-acme.md), and
[`../contracts/client-configs.md`](../contracts/client-configs.md).

## Validation

Always run:

```bash
cd roles/naive_proxy/molecule
make lint
```

Run the relevant scenario's `converge`, `verify`, and `idempotence` separately
during development. Use the impact matrix in [Testing](testing.md). Only use a
full `*-test` lifecycle for final clean validation because it destroys the
instance.

For documentation changes, additionally verify relative links and search for
obsolete paths or facts. Useful searches include:

```bash
rg -n 'roles/naive_proxy/debug|CI_DEBUG|\.config/molecule/config\.yml' \
  roles/naive_proxy/{defaults,tasks,templates,molecule,README.md,AGENTS.md}
rg -n 'mixed.*SOCKS|TODO.*tun|1\.14\.0-beta|1\.26\.6' \
  roles/naive_proxy/{defaults,tasks,templates,molecule,README.md,AGENTS.md}
git diff --check
```

## Completion checklist

- Contract matches implementation.
- Tests prove the changed behaviour, not a bypass path.
- Idempotence reports `changed=0` where role code changed.
- No unrelated scenario was rerun without a dependency reason.
- No Molecule instance or diagnostic capture was left running unintentionally.
- Operator and agent entrypoints link to the new detail instead of duplicating
  it.
