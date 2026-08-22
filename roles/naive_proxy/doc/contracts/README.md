# naive_proxy contracts

This directory is the normative description of the role's externally visible
behaviour. The role implementation remains the executable source of truth; a
behavioural change is incomplete until the matching contract and its evidence
are updated in the same change.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are used in their usual
normative sense.

## Contract map

| Contract | Owns |
|---|---|
| [Interface](interface.md) | supported host, required inputs, public variables, tags, filesystem outputs |
| [Runtime](runtime.md) | containers, ports, traffic routing, service lifecycle, security boundaries |
| [TLS and ACME](tls-and-acme.md) | certificate ownership, issuance, renewal, SNI/ALPN routing |
| [HAProxy](haproxy.md) | supported version floor, timeouts, H2/global tuning, diagnostics |
| [Client configurations](client-configs.md) | generated sing-box files and routing semantics |
| [Verification](verification.md) | built-in healthchecks, Molecule coverage, evidence and known gaps |

## Evidence convention

Each contract links to both:

- **implementation** — tasks, defaults, variables, handlers, or templates that
  enforce the behaviour;
- **verification** — Molecule assertions or stress tasks that exercise it.

An implementation link without a verification link means the behaviour is
currently checked by review/lint only. Such gaps are called out explicitly in
[Verification](verification.md); documentation must not imply stronger coverage
than the tests provide.

## Changing a contract

1. Update the contract before or together with the implementation.
2. Add or adjust the nearest assertion in `molecule/shared/`.
3. Run the smallest scenario set that covers the changed behaviour; use the
   matrix in [Verification](verification.md).
4. Run idempotence for changes to tasks, templates, handlers, images, or units.
5. Record new maintenance knowledge in `../maintenance/`, not in `AGENTS.md` or
   the operator README.

The repeatable workflow is documented in
[Changing the role](../maintenance/change-workflow.md).
