# iptables - Agent Context

This file is for future AI agents working on `roles/iptables`. It captures
the role contract, the test workflow, the sharp edges I hit while writing
the `add-nftables` branch, and — most importantly — the documentation you
MUST consult before changing firewall rules or test plumbing here. Skim
the "Mandatory documentation" section before you do anything else.

## Mandatory documentation (read before editing firewall rules)

You will be tempted to write or "fix" something based on intuition, a
half-remembered nft idiom, or a stack-overflow answer. Don't. Firewall
behaviour is unforgiving — a wrong match in iptables-nft compatibility
mode, a `meter` shorthand that re-applies wrong, or an interface name in
the wrong chain ends up in production traffic. Before any change to
templates, migration logic, or assertions, open the relevant page and
quote it back to yourself.

Authoritative references (links are stable):

- nftables wiki — top-level: <https://wiki.nftables.org/wiki-nftables/index.php/Main_Page>
- nftables wiki — Sets (canonical dynamic-set syntax): <https://wiki.nftables.org/wiki-nftables/index.php/Sets>
- nftables wiki — Meters (DEPRECATED shorthand, fails on re-apply): <https://wiki.nftables.org/wiki-nftables/index.php/Meters>
- nftables wiki — Rate limiting: <https://wiki.nftables.org/wiki-nftables/index.php/Rate_limiting_matchings>
- nftables wiki — Atomic rule replacement: <https://wiki.nftables.org/wiki-nftables/index.php/Atomic_rule_replacement>
- nftables wiki — Configuring chains (hook priorities incl. `srcnat`/`dstnat`): <https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains>
- nftables wiki — Loading rules from a file: <https://wiki.nftables.org/wiki-nftables/index.php/Loading_rules_from_a_file>
- nftables wiki — Main differences with iptables: <https://wiki.nftables.org/wiki-nftables/index.php/Main_differences_with_iptables>
- nft(8) on Debian 12: <https://manpages.debian.org/bookworm/nftables/nft.8.en.html>
- iptables-extensions(8) (hashlimit semantics — single named bucket, NOT per-interface): <https://manpages.debian.org/bookworm/iptables/iptables-extensions.8.en.html>
- iptables(8) (-w wait-for-xtables-lock, atomic restore semantics): <https://manpages.debian.org/bookworm/iptables/iptables.8.en.html>
- systemd.unit(5) (`PartOf=`, `ReloadPropagatedFrom=`): <https://www.freedesktop.org/software/systemd/man/systemd.unit.html>
- Ansible Molecule — Dependency: <https://ansible.readthedocs.io/projects/molecule/configuration/#dependency>
- ansible-compat — Runtime + auto-discovery from `galaxy.yml`: <https://ansible-compat.readthedocs.io/>
- ansible-galaxy collection install (transitive `dependencies:` from `galaxy.yml`): <https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html>

Strict rule: when you're about to edit `nftables.conf.j2`, the iptables
body in `mini-pig-firewall-apply.j2`, any `tasks/migrate_*` file, or any
assertion that probes the live kernel, open the corresponding doc page
first and cite it in your reasoning (commit message, PR body, or AGENTS
follow-up). "Based on the existing pattern" is not enough — that pattern
might be the bug.

## Pitfalls I stepped on (don't repeat these)

Each item is something I got wrong at least once. The fix and the
documentation behind it are recorded so the next agent doesn't replay
the same loop.

### P1. nft `meter NAME { ... }` shorthand cannot be re-applied

I first translated the iptables hashlimit ICMP rate limit into the
inline `meter NAME { ip saddr limit rate X/minute }` shorthand. It
parsed fine. It loaded fine. Then the second `nft -f /etc/nftables.conf`
(either via the role's `validate:` hook or Debian's stock
`ExecReload=`) blew up with:

```
Error: Could not process rule: Device or resource busy
```

Cause: the `meter NAME { ... }` form transpiles to a dynamic set whose
declaration uses `create` semantics (`NLM_F_EXCL`). The kernel refuses
to re-create a set/meter of the same name in another transaction.

Canonical fix — declare the dynamic set explicitly at table scope, then
reference it via `update @<set>` in rules:

```nft
table ip mpig_filter {
    set icmp_echo_v4 { type ipv4_addr; flags dynamic; timeout 5m; }

    chain input {
        ...
        iifname "eth0" icmp type echo-request \
            update @icmp_echo_v4 { ip saddr limit rate 10/minute } accept
    }
}
```

The explicit `set ... flags dynamic` declaration is idempotent — the
kernel accepts it as a no-op when the set already exists with matching
type+flags.

Docs:

- <https://wiki.nftables.org/wiki-nftables/index.php/Meters> — "Meters
  are syntactic sugar for sets with dynamic flag" and the deprecation note.
- <https://wiki.nftables.org/wiki-nftables/index.php/Sets> — `flags dynamic`,
  `timeout`, `update @set`.
- <https://wiki.nftables.org/wiki-nftables/index.php/Rate_limiting_matchings> —
  per-key rate limit via dynamic set + `limit rate`.

Do NOT "fix" the EBUSY symptom by dropping `validate:` or by replacing
`ExecReload=nft -f` with `ExecReload=systemctl restart`. The template
must be re-applyable.

### P2. iptables `--hashlimit-name icmp` is a single global bucket — preserve that in nft

When I rewrote ICMP rate-limit into explicit dynamic sets I first
suffixed each set with the interface name (`icmp_echo_v4_{{ inf }}`).
That looked tidy but it silently broke parity with the iptables side.

`iptables-extensions(8)` for `-m hashlimit`:

> `--hashlimit-name foo` — The name for the /proc/net/ipt_hashlimit/foo
> entry. There is one shared rate-limit bucket per name, regardless of
> which rule references it.

i.e. `-m hashlimit --hashlimit-name icmp --hashlimit-mode srcip` in the
iptables backend counts per source IP across ALL interfaces. My
per-interface suffix split that into N counters and would have let an
attacker bypass the rate limit by spreading floods across interfaces.

Current correct form: ONE set per direction shared across all external
interfaces, mirroring iptables semantics:

```nft
set icmp_echo_v4  { type ipv4_addr; flags dynamic; timeout 5m; }
set icmp_reply_v4 { type ipv4_addr; flags dynamic; timeout 5m; }
set icmpv6_echo   { type ipv6_addr; flags dynamic; timeout 5m; }
set icmpv6_reply  { type ipv6_addr; flags dynamic; timeout 5m; }
```

Rule: when porting any iptables match to nft, READ the iptables-side
match documentation first, not just the nft syntax. The scope of the
key (per-saddr, per-saddr-dport, per-interface, …) is part of the
behaviour and must round-trip.

### P3. Don't silence `validate:` or replace dynamic tests with static snapshots

When (P1) was misdiagnosed I tried two "quick" mitigations:

1. Drop `validate: '/usr/sbin/nft -c -f %s'` from the
   `template: src: nftables.conf.j2` task in `tasks/nftables.yml`.
2. Replace the Stage 4a "reload nftables.service then snapshot the live
   kernel" test in `molecule/nftables/converge.yml` with a static
   read of `/etc/nftables.d/*.conf`.

Both were wrong and both got reverted. Reasoning, with docs:

- `nft -c -f file` is NOT a parser-only pass. nft 1.0.6 submits the
  batch to the kernel for a dry-run commit, then rolls back. See
  <https://manpages.debian.org/bookworm/nftables/nft.8.en.html> ("OPTIONS,
  `-c`: Check commands validity without actually applying the changes").
  When it fails on re-apply, the template IS broken — silencing the
  check just defers the failure to boot or to the next drift-check.
- Debian's stock `nftables.service` has
  `ExecReload=/usr/sbin/nft -f /etc/nftables.conf` — bare `nft -f`,
  no shell wrapper. Our apply dispatcher and the SNAT service both
  do the same — bare `nft -f` on their respective files. The
  protection against EBUSY on re-apply lives **in-file** via the
  `add table; delete table; table {…}` idiom (see P4), so every
  reload path hits identical atomicity. The Stage 4a "four reloads"
  sub-block exercises this on the same kernel state four times in a
  row — if the in-file pattern regresses (someone drops the prefix or
  reverts the dispatcher to a shell-wrapped delete + load) the test
  goes red. Replacing the live reloads with a file diff doesn't catch
  this.

Rule: when a test fails after a template change, the failure is the
contract telling you the change is incomplete — not the test asking to
be rewritten.

### P4. nft 1.0.6 (Debian 12 baseline) rejects `destroy table`

`destroy table` only landed in newer nft. On the supported baseline you
get:

```
Error: syntax error, unexpected destroy
```

So atomic delete-then-create lives **in-file**, using the canonical
nft idiom that works on 1.0.6 — `add table … ; delete table … ; table … { … }`
wrapped in one `nft -f` batch. Each `nft -f` is one kernel transaction,
no shell-level pre-delete, no race window:

```nft
#!/usr/sbin/nft -f

add table ip mpig_filter
delete table ip mpig_filter
add table ip6 mpig_filter
delete table ip6 mpig_filter
add table ip mpig_nat
delete table ip mpig_nat

table ip mpig_filter { … }
table ip6 mpig_filter { … }
table ip mpig_nat { … }
```

The same pattern lives in `mpig-randomized-snat.conf.j2` for the
randomized-SNAT table. `add table` is idempotent — creates an empty
table if missing, no-op if present — so the `delete table` that follows
never errors on first apply or after a kernel wipe. Both
`mini-pig-firewall-apply` (nftables backend) and
`mpig-randomized-snat.service` shrink to a flat
`/usr/sbin/nft -f <conf>` — no `/bin/sh -c`, no `2>/dev/null || true`.

Debian's stock `ExecReload=/usr/sbin/nft -f /etc/nftables.conf` benefits
from the same atomicity for free, which is why the Stage 4a "four reloads"
sub-block stays green without any shell-level protection in the dispatcher
(verified by molecule's `nftables` scenario).

NEVER reintroduce `destroy table` in `nftables.conf.j2`. NEVER use
`flush ruleset` either — it would wipe foreign tables (kube-proxy,
Docker, libvirt) the role doesn't own. NEVER drop the
`add table … ; delete table …` prefix in front of any managed table —
the second `nft -f` over the same kernel state would fail on dynamic
sets or any other write-once shape (this is exactly the EBUSY class
that pitfall P1 fixed; without the in-file delete it would resurface).
NEVER wrap `nft -f` in a shell `delete … || true; nft -f` "for safety" —
that's the design we explicitly moved away from.

Docs:

- <https://wiki.nftables.org/wiki-nftables/index.php/Atomic_rule_replacement>
  — the in-file `add … ; delete … ; … { … }` pattern is the canonical
  1.0.6-compatible form of atomic destroy-then-load.
- <https://wiki.nftables.org/wiki-nftables/index.php/Loading_rules_from_a_file>
  — `nft -f` semantics: a single batch = a single kernel transaction.

### P5. Molecule + ansible-compat auto-discover the collection from `galaxy.yml`

I once "fixed" a CI-vs-local discrepancy by pinning
`ANSIBLE_COLLECTIONS_PATH` to the checkout, symlinking the role into
`~/.ansible/collections/`, and adding a `GIT_DIR=/dev/null` shim to the
Makefile. All of this was unnecessary — and all of it got reverted.

How it actually works: ansible-compat's `Runtime.prepare_environment(install_local=True)`
looks for `<cwd>/galaxy.yml` or `<cwd>/../../galaxy.yml`, and if found
runs `ansible-galaxy collection install <repo>` into a managed cache.
That covers the role itself AND the transitive `dependencies:` block in
`galaxy.yml`. Nothing in the user's `~/.ansible/` or the env is
involved.

Concretely:

- The Makefile's only job is `cd $(ROLE_DIR) && molecule -c molecule/shared/base.yml $* -s <scenario>`.
- The `molecule -c .../base.yml` flag (resolved relative to the cwd, which
  is why we cd first) points molecule at the shared base config and
  scenario.
- The `dependency: name: galaxy` block in `base.yml` installs anything
  declared in a sibling `requirements.yml`/`collections.yml` (we have
  none). The `galaxy.yml` `dependencies:` block is installed transitively
  by ansible-compat's `install_collection_from_disk()`.

Do NOT set `ANSIBLE_COLLECTIONS_PATH`. Do NOT symlink into
`.ansible/collections/`. Do NOT rsync role sources for testing. Do NOT
add `GIT_DIR=/dev/null`. If discovery is failing, the diagnosis is "we
aren't cd'd into the collection root or a role under it" — fix the
invocation, not the environment.

Docs:

- <https://ansible-compat.readthedocs.io/> — `Runtime.prepare_environment`.
- <https://ansible.readthedocs.io/projects/molecule/configuration/#dependency>
  — Molecule's `dependency: name: galaxy` step.
- <https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html>
  — `ansible-galaxy collection install <path>` transitively installs the
  `dependencies:` block from a collection's own `galaxy.yml`.

### P6. Migration order: load nft tables BEFORE wiping iptables-nft

`migrate_to_nftables.yml` does an atomic `iptables-restore` /
`ip6tables-restore` wipe of `ip filter`, `ip nat`, `ip6 filter`. If this
runs BEFORE the nft `mpig_*` tables are live, the host is briefly
firewall-naked. The current order in `tasks/nftables.yml` is:

1. Render `/etc/nftables.conf`.
2. Write the `nftables` backend marker.
3. Start `mini-pig-firewall.service` → dispatcher loads the nft tables.
4. THEN include `migrate_to_nftables.yml`.

Don't reorder. The migration include guards on the OLD marker captured
before step 2.

Also note `migrate_to_scoped_iptables.yml` is intentionally different:
the wipe AND the MPIG-* re-install happen in the same
`iptables-restore` transaction, so the host stays covered there too.
See iptables(8) on `--noflush` / atomic restore semantics:
<https://manpages.debian.org/bookworm/iptables/iptables-restore.8.en.html>.

### P7. `mangle` is never touched

The role has never written to the `mangle` table. The migration wipes
exclude it on purpose — that is operator territory (QoS, mark-based
policy routing). Don't add `mangle` to any iptables/nft path here
without a separate design discussion.

### P8. Foreign-chain wipe is a one-time migration cost, not steady state

The atomic iptables-restore wipe used by both migration paths clears
`KUBE-*`, `DOCKER-*`, libvirt chains in the affected iptables-nft
tables — that's a one-time cost during migration, documented in the
README. Steady-state applies (scoped iptables backend) MUST NOT touch
foreign chains/rules; the scoped backend uses `ensure_chain` +
`ensure_anchor` for that reason. If you find yourself adding a
`-F`/`-X` to the apply script outside the `MPIG-*` namespace, stop and
re-read the contract.

### P9. `ip6tables` doesn't have a `nat` table on the iptables-nft baseline we ship

This is why the migration wipe only resets `ip filter`, `ip nat`, and
`ip6 filter` — no `ip6 nat`. Same reason `tasks/main.yml` doesn't run
`ip6tables -t nat`. nft's `ip6 mpig_filter` table exists, but the v6
NAT story is intentionally absent. Don't add it as a "tiny consistency
fix" — it widens scope.

### P10. systemd merges back-to-back `systemctl reload` requests

The Stage 4a "four reloads" sub-block in
`molecule/nftables/converge.yml` fires four `systemd: state: reloaded`
calls against `nftables.service` in a tight loop (the other Stage 4a
sub-block is the drift-recovery test for the decoupled SNAT unit —
unaffected by this discussion). The verify-side assertion ONLY
requires `>= 2` "Reloaded …nftables" entries in the journal — NOT 4.

The reason: systemd's job model merges queued reload jobs of the same
type when they accumulate on a `Type=oneshot RemainAfterExit=yes` unit
(Debian's `nftables.service` is one). If `systemctl reload` is invoked
while a previous ExecReload is still running, the new request is
queued as a single "next" reload; subsequent identical requests merge
into that same queued job. Four loop iterations can legitimately
collapse to 2-3 journal events.

We do NOT fight this — it is documented systemd behaviour, not a bug.
The contract under test is "live reload actually fired multiple times,
without failures", not "the exact loop count appears in the journal".

If you see this assertion fire, the failure is in the role — likely
pitfall P1 (meter shorthand → EBUSY on re-apply) or the test itself
was stubbed (pitfall P3). Don't:

- lower the count threshold further;
- relax the "Reload failed" / "Could not process rule" negative checks
  (these are how meter-EBUSY regressions get caught — failed reloads
  are NEVER merged by systemd);
- delete the `journalctl --sync` call that flushes pending entries
  before the capture (race between systemctl's reply and journald's
  commit otherwise drops the trailing event).

Docs:

- systemd job model — <https://systemd.io/JOB_TYPES> (job merging
  semantics for reload).
- systemd.service(5) — <https://www.freedesktop.org/software/systemd/man/systemd.service.html>
  (Type=oneshot, RemainAfterExit, ExecReload behaviour).
- journalctl(1) `--sync` — <https://manpages.debian.org/bookworm/systemd/journalctl.1.en.html>
  (forces journald to flush pending writes to backing storage).

### P11. The SNAT unit is intentionally decoupled from `nftables.service`

The randomized-SNAT unit used to carry `PartOf=nftables.service` +
`ReloadPropagatedFrom=nftables.service`. That coupling caused two
problems and one of them silently rotted in production:

1. **Asymmetric design.** `mini-pig-firewall.service` is invoked by
   `mini-pig-firewall.timer` via `systemctl start` — and `PartOf`
   propagates only stop/restart, not start. So no propagation
   mechanism worked from the timer side. The fix was a shell-level
   `reload_randomized_snat()` function in the apply dispatcher that
   did `systemctl reload || start` on every dispatcher tick. Living
   workaround for a coupling that didn't help.
2. **Backend-irrelevant linkage.** The scoped iptables backend has no
   relationship to `nftables.service`, yet the SNAT unit lifecycle
   was bound to it.

The current design: `mpig-randomized-snat.service` is `Type=oneshot`
without `RemainAfterExit`, has its own `mpig-randomized-snat.timer`
on `iptables_drift_check_interval`, and is **fully independent of
`nftables.service`**. Two service+timer pairs, both shaped the same
way (`mini-pig-firewall.{service,timer}` and
`mpig-randomized-snat.{service,timer}`), each owning its own kernel
tables.

Trade-off accepted: an external `systemctl restart nftables.service`
(e.g. `apt-postinst` on the `nftables` package) loses the SNAT chain
until the next timer fire — up to ~10 minutes (default) of no
randomization. `mpig_filter` / `mpig_nat` survive that path because
they're in `/etc/nftables.conf` and the parent re-applies them itself.

DO NOT re-introduce `PartOf=nftables.service` or
`ReloadPropagatedFrom=nftables.service` on `mpig-randomized-snat.service`
for "consistency" or "faster recovery from apt-postinst restarts". You
will resurrect the `reload_randomized_snat()` shell hack and the
asymmetry that justified it. If recovery latency matters for a
particular host, the right knob is `iptables_drift_check_interval` —
lower it to taste.

Docs:

- systemd.unit(5) — <https://www.freedesktop.org/software/systemd/man/systemd.unit.html>
  (`PartOf=` propagates stop+restart only, not start; `ReloadPropagatedFrom=`
  propagates reload; neither covers the start-via-timer case used here).

### P12. ICMP echo-request rate-limit must sit ABOVE `ct state related,established accept`

ICMP conntrack tuples are stable per `ping` process (same `id`), so
packets 2..N of a continuous inbound ping match `ESTABLISHED` and skip
the rate-limit. Required order in both backends:

1. `iif lo accept`
2. echo-request rate-limit (hashlimit / `update @icmp_echo_v4 { ... limit rate }`) — accept in-burst
3. echo-request **explicit drop** — over-burst, otherwise `hashlimit`/`update` non-match falls through to (4)
4. `ct state related,established accept`

Echo-reply stays BELOW (4): our own outbound `ping <peer>` returns as
echo-reply matching `ESTABLISHED`, and we don't want to throttle that.

Don't "clean up" to the conventional state-first idiom — it silently
disables ICMP rate-limiting for steady inbound pings. The `default`
scenario probes this in `molecule/shared/{converge,verify}.yml`: fire
N=20 parallel `ping -c 1` from extns_a, record each ping's exit
status; verify asserts `1 ≤ OK < N` — i.e. the peer actually saw
packet loss. All-OK means the rate-limit was bypassed (state shortcut
took over); all-FAIL means even the in-burst portion was dropped.
Only covers the iptables backend — the nft template carries the same
ordering by construction.

Docs:

- iptables-extensions(8) `hashlimit` semantics (match-only, no implicit drop):
  <https://manpages.debian.org/bookworm/iptables/iptables-extensions.8.en.html>
- nft `update @set { ... limit rate }` falls through on non-match:
  <https://wiki.nftables.org/wiki-nftables/index.php/Rate_limiting_matchings>

## Rules for AI agents running Molecule

1. Use the Makefile wrapper at `molecule/Makefile`, not bare `molecule`.
   The wrapper does ONE thing: `cd $(ROLE_DIR) && molecule -c molecule/shared/base.yml <action> -s <scenario>`.
   The cd-into-role-dir is what lets ansible-compat auto-discover the
   collection from `galaxy.yml`. Bare `molecule` invoked from
   `molecule/<scenario>` will not auto-discover.
2. Activate the local Python venv before make/molecule/ansible commands:
   `source /media/data/app/python/venv3/bin/activate`.
3. Prefer separate `converge` and `verify` while debugging. Use `test`
   for a final clean run only — it destroys the instance at the end and
   discards artefacts that verify reads from `/tmp/molecule-*` files.
4. Never run `verify` alone after a `destroy`. The verify play slurps
   converge-produced artefacts under `/tmp` inside the container; if
   the container was destroyed (or recreated without re-running
   converge), verify fails on the slurp before reaching any assertion.
5. Do not pipe Molecule output through `tail`. Redirect full logs to
   `/tmp`, then inspect with `rg`, `grep`, `sed`, or `less`. Stream the
   tail with `Monitor` when running long converge jobs in the
   background.
6. Keep the instance alive between debugging iterations. Destroy
   explicitly only when host state is suspect or you need a first-apply
   migration path (the `scoped_migration` scenario needs a clean
   container; partial state will skip the migration branch).
7. These scenarios need privileged container features (`SYS_ADMIN`,
   `NET_ADMIN`, `NET_RAW`) and can fail under restricted sandboxes
   before Ansible starts. If local sandboxing blocks Podman state or
   nested netfilter operations, rerun the same Make target with the
   required approval — don't change the test.
8. Don't run the `gha` scenario locally (if one is added). It targets
   `localhost` and would mutate the dev host.

Run from `roles/iptables/molecule`:

```bash
source /media/data/app/python/venv3/bin/activate
make help
make default-podman-converge
make default-podman-verify
make scoped-migration-podman-converge
make scoped-migration-podman-verify
make nftables-podman-converge
make nftables-podman-verify
```

Full final runs:

```bash
make default-podman-test
make scoped-migration-podman-test
make nftables-podman-test
```

The Molecule instance name is `molecule-iptables` for all podman
scenarios. Useful diagnostics while an instance is alive:

```bash
podman exec molecule-iptables systemctl status mini-pig-firewall.service --no-pager
podman exec molecule-iptables systemctl status mini-pig-firewall.timer --no-pager
podman exec molecule-iptables systemctl status nftables.service --no-pager
podman exec molecule-iptables systemctl status mpig-randomized-snat.service --no-pager
podman exec molecule-iptables systemctl status mpig-randomized-snat.timer --no-pager
podman exec molecule-iptables systemctl list-timers --no-pager
podman exec molecule-iptables nft list ruleset
podman exec molecule-iptables nft list table ip mpig_filter
podman exec molecule-iptables nft list table ip mpig_nat
podman exec molecule-iptables nft list table ip mpig_randomized_snat
podman exec molecule-iptables iptables -t nat -S
podman exec molecule-iptables iptables -S
podman exec molecule-iptables journalctl -u mini-pig-firewall.service -n 100 --no-pager
podman exec molecule-iptables journalctl -u nftables.service -n 100 --no-pager
podman exec molecule-iptables cat /var/lib/mini-pig-firewall-backend
```

## What this role does

`roles/iptables` manages host firewall/NAT policy for mini-pig
deployments. It supports two mutually exclusive backends:

- scoped iptables backend, selected by default with
  `iptables_use_nftables: false`
- nftables backend, opt-in with `iptables_use_nftables: true`

The role also has a backend-independent randomized SNAT module that
loads a native nft chain in `table ip mpig_randomized_snat` at
`priority srcnat - 10`. The kernel evaluates that chain ahead of every
iptables-nft chain at `srcnat`, so kube-proxy / Kilo CNI /
iptables-persistent never see the randomized-pool traffic. See nft hook
priority docs:
<https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains>.

Both backends share one unified service `mini-pig-firewall.service`. It
is a thin oneshot unit whose `ExecStart` is `mini-pig-firewall-apply`,
a dispatcher that reads `/var/lib/mini-pig-firewall-backend` and
re-applies the matching state. The role invokes the service at apply
time; systemd fires it on boot (`WantedBy=multi-user.target`) and on
`mini-pig-firewall.timer`.

The randomized SNAT module owns a **separate** systemd lifecycle:
`mpig-randomized-snat.service` (same `Type=oneshot` no-`RemainAfterExit`
shape as the unified service) plus `mpig-randomized-snat.timer` on the
same `iptables_drift_check_interval` cadence. The two service+timer
pairs are fully decoupled — they own independent nft tables and never
reach into each other.

## Role flow

`tasks/main.yml` is the dispatcher and shared preflight:

1. Probe Docker, legacy rule files (`/etc/iptables/rules.v[46]`),
   `netfilter-persistent`, any leftover `mini-pig-iptables.service` from
   older versions, and the firewall-backend marker.
2. Validate `iptables_snat_rules`: `to_source` is required; `protocol`
   is required when `src_port` or `dst_port` is set.
3. Install `iptables` + `nftables` packages unconditionally — both
   backends and the randomized SNAT module need both binaries.
4. Enable IPv4 forwarding when requested.
5. Enforce the reverse-migration guard BEFORE backend dispatch.
6. Render the unified firewall service + dispatcher script
   (`tasks/firewall_service.yml`) and enable the service.
7. Include exactly one backend: `tasks/iptables.yml` or
   `tasks/nftables.yml`. Each backend writes the marker, then starts
   `mini-pig-firewall.service` to apply.
8. Always include `tasks/randomized_snat.yml` (SNAT module + its own
   `mpig-randomized-snat.timer` + `mini-pig-firewall.timer` + cleanup
   of legacy artefacts).

The backend marker lives at `/var/lib/mini-pig-firewall-backend`:

- scoped iptables backend writes `iptables-scoped`
- nftables backend writes `nftables`

## Core backend contract

Default behaviour must remain conservative: existing users stay on
scoped iptables unless they explicitly set `iptables_use_nftables: true`.

### Scoped iptables backend

- Owns only `MPIG-*` chains and comment-anchored jump rules
  (`-m comment --comment "mini-pig firewall: <chain>"`).
- Optional Docker integration owns `MPIG-DOCKER-USER`, anchored from
  `DOCKER-USER`.
- Steady-state applies must not flush foreign chains or foreign rules.
- First migration from the old `iptables-persistent` layout is
  intentionally destructive for the managed filter/nat tables in a
  single atomic `iptables-restore` transaction. This is an accepted
  one-time cost to bootstrap scoped ownership.
- The apply logic is the iptables body inside
  `templates/mini-pig-firewall-apply.j2`; the script is invoked through
  `mini-pig-firewall.service` and dispatches by reading the backend
  marker.

### nftables backend

- Owns only these nft tables: `table ip mpig_filter`,
  `table ip6 mpig_filter`, `table ip mpig_nat`. Plus
  `table ip mpig_randomized_snat` from the SNAT module (separate file,
  separate unit).
- The template `nftables.conf.j2` MUST NOT use `flush ruleset` and
  MUST NOT use `destroy table` (rejected by nft 1.0.6 / Debian 12).
- Atomic delete-then-create lives **in-file** via the
  `add table … ; delete table … ; table … { … }` idiom (see pitfall P4).
  Apply is a flat `nft -f /etc/nftables.conf` — no shell-level
  pre-delete in the dispatcher.
- Per-source-IP rate limiting in the template uses explicit
  `set NAME { type ...; flags dynamic; timeout 5m; }` declarations +
  `update @set { key limit rate X/Y }` rules — see pitfall P1.
- Migration to nftables loads the replacement nft ruleset BEFORE the
  iptables-nft wipe in `migrate_to_nftables.yml` so the kernel firewall
  stays covered across the transition — see pitfall P6.
- Reverse migration from nftables back to iptables is intentionally
  blocked by the marker guard in `tasks/main.yml`.

## SNAT model

There are two SNAT mechanisms. Keep them separate.

### Static SNAT (`iptables_snat_rules`)

- Rendered by the selected backend.
- In scoped iptables mode it lives under `MPIG-POSTROUTING`, ahead of
  the broad `MASQUERADE` block (narrow match terminates first).
- In nftables mode it lives in `table ip mpig_nat / chain postrouting`,
  ahead of the broad `masquerade` block.
- Optional `src_port` or `dst_port` requires `protocol`. Validation in
  `tasks/main.yml` rejects malformed entries before any template renders.

### Randomized SNAT (`iptables_randomized_ext_ips`)

- Lives in its own native nft table `table ip mpig_randomized_snat` at
  `priority srcnat - 10`. The kernel evaluates this chain ahead of
  every iptables-nft chain at `srcnat`. See
  <https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains>.
- Loaded by `mpig-randomized-snat.service` from
  `/etc/nftables.d/mpig-randomized-snat.conf`. The unit is
  **decoupled from `nftables.service`** — no `PartOf=`, no
  `ReloadPropagatedFrom=`. It mirrors `mini-pig-firewall.service`:
  `Type=oneshot` without `RemainAfterExit`, so each `systemctl start`
  re-fires `ExecStart`, which is a flat `nft -f
  /etc/nftables.d/mpig-randomized-snat.conf` (no shell wrapper —
  atomic delete-then-create lives in-file via the
  `add table; delete table; table {…}` idiom, see pitfall P4).
- Steady-state re-application is driven by `mpig-randomized-snat.timer`
  on the same `iptables_drift_check_interval` cadence as
  `mini-pig-firewall.timer`. The two units own independent kernel
  tables and never reach into each other.
- Trade-off the decoupling buys: an external `systemctl restart
  nftables.service` (e.g. `apt-postinst` on the `nftables` package) is
  no longer propagated via `PartOf=`. The chain is restored on the
  next timer fire — up to ~10 minutes (default) after the parent
  restart. `mpig_filter` / `mpig_nat` are unaffected because they
  live in `/etc/nftables.conf` and nftables.service re-applies them
  itself.
- The off path for an empty pool stops the timer, disables the
  service, drops the kernel table, then removes the conf + unit
  files.
- To temporarily disable randomization from another role: stop the
  timer first (so the timer can't re-arm), `nft delete table ip
  mpig_randomized_snat`, do work, then start the timer + service back
  in the reverse order. Don't add a sentinel file or a flag to the
  unit — the timer/start pair is the public API.

## Migration and safety constraints

- Two migration paths share a single design: an atomic
  `iptables-restore` / `ip6tables-restore` wipe of `ip filter`,
  `ip nat`, `ip6 filter` to empty containers. The wipe runs once per
  migration:
  - `migrate_to_scoped_iptables.yml` — wipes AND immediately
    re-installs `MPIG-*` chains + anchors in the same transaction; host
    stays covered by the role's scoped chain layout.
  - `migrate_to_nftables.yml` — wipes only; native nft `mpig_*` tables
    were already loaded by the caller before the include fires, so the
    kernel firewall is in nft form when iptables-nft becomes empty.
- Both wipes intentionally drop **foreign** chains in the affected
  iptables-nft tables (`KUBE-*`, `DOCKER-*`, libvirt, podman, …).
  Operators must restart foreign managers (or reboot) for immediate
  reconcile; most self-heal on their next periodic pass. This trade-off
  is documented in README "Migration" and asserted in the molecule
  scenarios.
- `mangle` is never touched — the role has never written there.
- Do not move the reverse-migration guard below backend dispatch.
- Do not reintroduce the old Docker restart handler. The scoped backend
  is designed to avoid flushing Docker-owned chains on steady-state
  applies.
- Do not reintroduce `/etc/iptables/rules.v4` or `/etc/iptables/rules.v6`
  as managed rule files for the current backend.
- Do not reintroduce `destroy table` in `nftables.conf.j2` — it breaks
  the Debian 12 nft baseline (see pitfall P4).
- Be careful with `changed_when: false` on cleanup tasks — it can hide
  real kernel-rule changes. Only use that pattern when the surrounding
  transition still reports the managed state change clearly.
- If you alter template whitespace in `nftables.conf.j2`, validate with
  `nft -c -f` (NOT parser-only — see pitfall P3) AND a live Molecule
  run.

## Molecule scenarios

### `default`

- Debian 12 (bookworm, nft 1.0.6), podman-in-podman. The bookworm nft
  baseline rejects `destroy table` — exercising that version is the
  point.
- Exercises the scoped iptables backend.
- Applies several `iptables_inf_ext` shapes for backward compatibility.
- Verifies static SNAT variants, native nft randomized SNAT chain at
  `priority srcnat - 10`, drift-check timer, live kernel rules,
  Docker-related chains, and traffic probes.
- Includes a packet-level randomized-SNAT distribution probe: N=20
  TCP connects from extns_a → peer_ip_b:80 land in an accept-loop
  listener inside extns_b that records every peer IP. Verify asserts
  BOTH pool IPs appeared at least once across the N probes and
  peer_ip_a never leaked through. The intent is binary: catch a
  collapsed `numgen random` map (one pool IP missing across 20 rolls
  has ~2e-6 probability) or a chain that failed to preempt at
  `priority srcnat - 10`. No chi-squared or distribution-ratio
  checks — they're flaky on small N and not the regression we're
  guarding against.
- Molecule idempotence is intentionally disabled because converge walks
  through multiple role variable transitions.

### `scoped_migration`

- Starts from the old `iptables-persistent` layout with `rules.v4`,
  `rules.v6`, and legacy randomized SNAT units.
- Verifies first-apply cleanup into the scoped MPIG layout.
- Creates foreign `KUBE-*` state after the first apply and verifies a
  second scoped apply preserves it.

### `nftables`

- Walks through iptables backend → nftables backend migration →
  idempotent nft reapply → changed-rule reapply → forbidden reverse
  migration.
- Verifies nft ruleset ownership (the three `mpig_*` tables only),
  legacy cleanup, randomized SNAT coexistence under
  `priority srcnat - 10`, and the reverse-migration guard.
- Stage 4a exercises two independent contracts: (1) decoupled drift
  recovery — manually delete the SNAT table, fire
  `mpig-randomized-snat.service` (proxy for the timer), assert the
  chain is restored; (2) `/etc/nftables.conf` re-applyability — four
  back-to-back `systemctl reload nftables.service` calls must show
  `>= 2` successful reloads and zero "Reload failed" / "Could not
  process rule" entries in the journal (catches pitfall P1 meter-EBUSY
  regressions). The SNAT chain surviving across those four reloads is
  the side-effect proof that the unit is genuinely independent of
  `nftables.service` (no `PartOf=`).
- It starts from iptables mode and its Dockerfile installs `iptables`,
  so it does not prove behaviour on a direct fresh nft-only host
  without the `iptables` binary.

## Review checklist for future changes

Before approving changes to this role, confirm:

- `iptables_use_nftables` still defaults to `false`.
- Scoped iptables steady-state applies preserve foreign chains/rules.
- nftables backend does not use `flush ruleset` and does not render
  `destroy table` (rejected by nft 1.0.6 / Debian 12).
- Both `/etc/nftables.conf` and `/etc/nftables.d/mpig-randomized-snat.conf`
  still carry the in-file `add table … ; delete table …` prefix for
  every managed table (pitfall P4). The `nftables`-scenario verify has
  positive assertions on those lines — if they disappear, that's the
  red flag.
- The dispatcher (`mini-pig-firewall-apply`, nftables case) and
  `mpig-randomized-snat.service` both apply via flat
  `/usr/sbin/nft -f <conf>` — no `/bin/sh -c '...delete table... ; nft -f'`
  shell wrapping. Re-introducing the shell wrapper means the in-file
  pattern got broken; fix the conf, not the unit.
- Any per-key rate limiting in `nftables.conf.j2` uses explicit
  `set ... flags dynamic` + `update @set { ... limit rate ... }` — NOT
  the `meter NAME { ... }` shorthand (pitfall P1).
- The scope of an nft rate-limit set matches the iptables-side scope
  (e.g. ICMP hashlimit is a single global bucket, NOT per-interface —
  pitfall P2).
- ICMP echo-request rate-limit sits ABOVE `ct state related,established
  accept` (both in `MPIG-INPUT` and `chain input` of `mpig_filter` /
  `ip6 mpig_filter`), followed by an explicit `drop` for over-burst.
  Echo-reply rate-limit stays BELOW the state accept. Reverting to the
  state-first idiom silently disables the limit for steady-state inbound
  pings (pitfall P12).
- Migration to nft loads replacement runtime rules before teardown
  (pitfall P6).
- Reverse migration from nftables to iptables still fails early.
- Randomized SNAT lives in `table ip mpig_randomized_snat` at
  `priority srcnat - 10` (its own nft file under `/etc/nftables.d/`,
  loaded by `mpig-randomized-snat.service`).
- The SNAT unit is decoupled from `nftables.service`: no `PartOf=`, no
  `ReloadPropagatedFrom=`, `Type=oneshot` without `RemainAfterExit`,
  driven by `mpig-randomized-snat.timer` on the same
  `iptables_drift_check_interval` cadence as `mini-pig-firewall.timer`.
  Do not re-introduce the coupling — it creates the asymmetry that
  forced the `reload_randomized_snat()` shell hack the old dispatcher
  needed.
- Empty randomized SNAT pool tears down the service, timer, kernel
  table, conf, and unit files cleanly. Stat-then-act on the kernel
  drop (don't rely on parsing nft's error strings).
- `iptables_snat_rules` validation still rejects port-specific rules
  without `protocol`.
- `validate: '/usr/sbin/nft -c -f %s'` is still present on the
  `/etc/nftables.conf` render task.
- Stage 4a in the `nftables` scenario still drives BOTH a live drift
  simulation (`nft delete table ip mpig_randomized_snat` + service
  start + table re-snapshot) AND a live `systemctl reload
  nftables.service` loop (not static snapshots).
- The Makefile is still the canonical
  `cd $(ROLE_DIR) && molecule -c molecule/shared/base.yml ...` — no
  `ANSIBLE_COLLECTIONS_PATH`, no symlinks into `.ansible/collections/`,
  no `GIT_DIR=/dev/null` (pitfall P5).
- All relevant Molecule scenarios pass through the Makefile wrapper.
