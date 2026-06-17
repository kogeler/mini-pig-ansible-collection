# init - Agent Context

Context for AI agents working on `roles/init`, for **any** kind of task
(adding a sub-area, changing package/sysctl/DNS/SSH behaviour, debugging an
apply, refactoring, etc.). It describes what the role is, how it's laid out,
the per-area behaviour and its variables, the conventions to follow, and the
handful of non-obvious gotchas — including the handler/reboot ordering, which
is easy to break but is only one part of the role.

## What this role does

`roles/init` is the baseline host-bootstrap role for mini-pig deployments
(Debian, primarily bookworm/trixie). It takes a freshly-provisioned host to a
known baseline. The sub-areas are mostly independent:

- **grub** — preset grub-pc install device (BIOS hosts)
- **apt** — APT sources, package install/remove, optional full upgrade,
  reboot-required detection
- **dns** — DNS-over-TLS via systemd-resolved
- **users** — passwordless sudo, managed users + keys, root password/keys
- **time** — timezone + systemd-timesyncd NTP
- **kernel** — kernel cmdline (`GRUB_CMDLINE_LINUX_DEFAULT`) + sysfs tuning
- **sysctl** — persistent sysctl drop-in
- **hosts** — hostname + (optionally immutable) `/etc/hosts`
- **ssh** — sshd hardening, disable cloud key-fetch
- **mount** — optional btrfs data disk
- **s3** — optional awscli + `~/.aws` config
- **logs** — journald limits

## Layout & conventions

- `defaults/main.yml` — all user-facing `init_*` variables (every
  `init_flow_control_<area>` defaults `true`).
- `vars/main.yml` — internal constants: `_init_packages` (install set),
  `_init_packages_remove`, config paths, `_init_sysctl_parameters`,
  `_init_grub_bios_boot_part_guid`.
- `tasks/main.yml` — dispatcher: one `include_tasks` per area, each gated by
  `when: init_flow_control_<area> | bool` and tagged `['init', 'init-<area>']`
  (so you can run a single area with `--tags init-<area>`).
- `tasks/<area>.yml` — the per-area work.
- `handlers/main.yml`, `templates/…`.

Conventions to keep when editing:

- Task names are prefixed `init | <area> | <description>`.
- User-facing vars are `init_*`; internal facts/registers are `_init_*`.
- Prefer native/collection modules over `shell`/`command` (collection-wide
  rule). The few surviving `command`/`shell` uses (`update-grub`,
  `btrfs filesystem resize`, `lsblk`) are where no native module fits.
- Keep template/comment "why"s terse (one line); long rationale goes here.
- Tasks should stay idempotent; areas that intentionally use
  `changed_when: false` (e.g. `/etc/hosts` chattr toggles, `btrfs resize`,
  `update-grub`) do so to avoid false "changed" noise — preserve that intent.

## Apply order (`tasks/main.yml`)

| # | area | gate / notes |
|---|------|--------------|
| 0 | bare `user:` task | resolves `ansible_user`'s home into `_init_user_info` (consumed by `s3.yml`). |
| 1 | `grub.yml` | also tagged `init-apt` (it touches dpkg/debconf state). |
| 2 | `apt.yml` | |
| 3 | `apt-cleanup.yml` | only when `init_apt_kernel_cleanup`; tagged `init-apt` + `init-apt-cleanup`. |
| 4 | `dns.yml` | ends with `meta: flush_handlers`. |
| 5 | `users.yml` | |
| 6 | `time.yml` | ends with `meta: flush_handlers`. |
| 7 | `kernel.yml` | |
| 8 | `sysctl.yml` | |
| 9 | `hosts.yml` | |
| 10 | `ssh.yml` | |
| 11 | `mount.yml` | only when `init_block_dev != ''`. |
| 12 | `s3.yml` | only when both AWS keys are set. |
| 13 | `logs.yml` | |
| 14 | reboot decision task | notifies `reboot host` (see Handlers & reboot). |

The two `meta: flush_handlers` (dns step 4, time step 6) start
systemd-resolved / systemd-timesyncd within the run. They also constrain when
the reboot may be notified — see below.

## Sub-areas

- **grub.yml** — *grub-pc install device only* (which disk the bootloader
  installs to). Gathers `package_facts`, detects BIOS vs UEFI via
  `/sys/firmware/efi`; on BIOS hosts with `grub-pc`, enumerates disks from
  `lsblk` and selects MBR (`dos`) disks or GPT disks carrying a `bios_grub`
  partition (`_init_grub_bios_boot_part_guid`), then presets
  `grub-pc/install_devices` via `debconf`. Manual override:
  `init_grub_install_devices`. UEFI hosts and hosts without `grub-pc` are
  skipped. **Unrelated to the kernel cmdline** (that's `kernel.yml`).
- **apt.yml** — disables unattended-upgrade in `20auto-upgrades`; removes
  `_init_packages_remove` + `init_packages_remove_extra` (purge+autoremove);
  optional xanmod-kernel and google-cloud-sdk repos+keys; replaces Debian
  `sources.list`; installs `_init_packages` + `init_packages_extra`
  (`install_recommends: false`); `upgrade: full` only when `init_apt_upgrade`.
  When `init_apt_reboot_if_required`, computes `_init_apt_will_reboot`
  (true if `/var/run/reboot-required` exists OR running kernel != newest
  installed). The actual reboot is deferred to a handler (see below).
  Old-kernel purging lives in its own area (`apt-cleanup.yml`, below).
- **apt-cleanup.yml** — runs right after `apt.yml`, gated on
  `init_apt_kernel_cleanup` (default true). From `package_facts` it groups
  every versioned `linux-{image,headers,modules,kbuild,compiler}-*` package by
  minor (X.Y) line, keeps the `init_apt_kernel_versions_keep` (default 2)
  newest patch versions per line plus the running kernel, and purges the rest.
  Match is by the version embedded in the package name, so
  stock/backports/metapackage kernels are all covered; version-less
  metapackages like `linux-image-amd64` never match and are never touched.
  Before purging, an `assert` guard fails the run if the computed remove set
  ever targets the running kernel (`ansible_facts['kernel']`) — defence in
  depth, since the running kernel is already force-kept.
- **dns.yml** — installs a dhclient hook to stop DHCP from rewriting DNS;
  ensures `systemd-resolved`; renders the DNS-over-TLS drop-in from
  `init_resolved_dns` (notify `restart systemd-resolved`); points
  `/etc/resolv.conf` at the stub resolver; `flush_handlers`; starts the
  service.
- **users.yml** — `%sudo NOPASSWD` via `lineinfile` with `visudo` validate;
  creates `init_users + init_users_extra` (groups appended, optional
  `ssh_keys` via exclusive `authorized_key`); sets root password
  (`init_root_password`, default `!` = login locked); removes root's
  authorized_keys and the cloud-init sudoers drop-in.
- **time.yml** — sets `init_time_zone` (hwclock UTC); writes NTP /
  FallbackNTP into `timesyncd.conf` (notify `restart systemd-timesyncd`);
  `flush_handlers`; starts the service.
- **kernel.yml** — sets `GRUB_CMDLINE_LINUX_DEFAULT` from `init_cmdline_linux`
  (in `/etc/default/grub` if present, else drop-in
  `/etc/default/grub.d/90-init-role-cmdline.cfg`); notifies `update grub`;
  records `_init_grub_cmdline_changed`. Then renders the sysfs config
  (`sysfs.conf.j2` → `/etc/sysfs.d/init_role.conf`, notify
  `restart sysfsutils`).
- **sysctl.yml** — removes the obsolete `90-init-role.conf` drop-in; renders
  the persistent file at `init_sysctl_conf_filename` (default
  `99-zz-init-role.conf`); applies each parameter via `ansible.posix.sysctl`
  from `_init_sysctl_parameters` merged with user `init_sysctl_parameters`.
- **hosts.yml** — sets hostname (`init_hostname`, defaults to
  `inventory_hostname`); renders `/etc/hosts` from `hosts.j2`. When
  `init_block_hosts_file` (default true) it toggles the immutable bit
  (`chattr -i` before write, `+i` after) so cloud-init scripts can't drift the
  file; those chattr tasks are `changed_when: false`.
- **ssh.yml** — disables Scaleway `scw-fetch-ssh-keys` if present; forces
  `PermitRootLogin no`, `PasswordAuthentication no`, `X11Forwarding no` in
  `sshd_config` (notify `restart ssh`).
- **mount.yml** — only with `init_block_dev` set. Creates a btrfs filesystem
  (`force: no`), re-gathers facts, mounts by UUID at `init_mount_point`
  (default `/data`) with `compress-force=zstd` and an `ssd` opt when
  `init_block_dev_ssd`, then `btrfs filesystem resize max`.
- **s3.yml** — only with both `init_aws_access_key_id` and
  `init_aws_secret_access_key`. Installs `awscli` + `awscli-plugin-endpoint`
  and writes `~/.aws/{credentials,config}` (mode 0600) under
  `_init_user_info.home`.
- **logs.yml** — renders `journald.conf` from the `init_journald_*` sizes
  (notify `restart systemd-journald`).

## Handlers & reboot

`handlers/main.yml`, in order: `restart ssh`, `update grub`
(`shell: update-grub`, regenerates `grub.cfg`), `restart systemd-journald`,
`restart systemd-resolved`, `restart sysfsutils`, `restart systemd-timesyncd`,
`reboot host` (`reboot`, `reboot_timeout: 1200`).

Two facts about handlers drive the reboot design:

1. **Handlers run in definition order, not notification order.** So
   `reboot host` is defined **last** to guarantee it runs after `update grub`
   (and everything else). Keep it last; if you add a handler, put it before
   `reboot host`.
2. **A notified handler fires at the next `meta: flush_handlers` or at
   end-of-play.** Because `dns.yml` (step 4) and `time.yml` (step 6) flush,
   the reboot must only be *notified after step 6*, or it would fire mid-play
   before `kernel.yml` (step 7) rewrote the cmdline / `grub.cfg`.

So the reboot is notified from a **single** decision task at the end of
`tasks/main.yml` that aggregates both triggers — APT
(`_init_apt_will_reboot`, from `apt.yml`) and cmdline change
(`_init_grub_cmdline_changed`, from `kernel.yml`):

```yaml
- name: init | notify reboot host handler
  ansible.builtin.set_fact:
    _init_reboot_required: >-
      {{ (_init_apt_will_reboot | default(false) | bool)
         or (_init_grub_cmdline_changed | default(false) | bool) }}
  changed_when: _init_reboot_required | bool
  notify: reboot host
  tags: ['init', 'init-apt', 'init-kernel']
```

`changed_when` gates the notify (handlers only fire on `changed`); each input
is `| default(false)` so partial runs are safe. `update grub` stays a separate
handler with its own native notify from `kernel.yml`. Do **not** re-add
`notify: reboot host` to `apt.yml`/`kernel.yml` (that resurrects the
two-mechanism inconsistency and the premature-flush reboot).

## Variables worth knowing

Defaults that change behaviour materially (`defaults/main.yml`):

- `init_apt_upgrade` (false) — run `apt upgrade --full`.
- `init_apt_reboot_if_required` (false) — enables reboot-required detection
  (and thus the reboot path).
- `init_apt_kernel_cleanup` (true) — purge stale kernels at the end of
  `apt.yml`; `init_apt_kernel_versions_keep` (2) — how many newest patch
  versions to keep per minor (X.Y) line (running kernel always kept).
- `init_cmdline_linux` ("") — kernel cmdline; empty means leave it untouched.
- `init_block_dev` ("") / `init_mount_point` (`/data`) / `init_block_dev_ssd`
  — data-disk mount; empty `init_block_dev` skips `mount.yml`.
- `init_aws_access_key_id` / `init_aws_secret_access_key` — both required to
  run `s3.yml`.
- `init_apt_xanmod_kernel_enable`, `init_apt_google_cloud_sdk_enable` —
  optional repos.
- `init_root_password` (`!`), `init_users` / `init_users_extra`.
- `init_block_hosts_file` (true) — immutable `/etc/hosts`.
- `init_sysctl_parameters`, `init_sysctl_conf_filename`.
- `init_hostname`, `init_time_zone`, `init_resolved_dns`, `init_journald_*`.

## Testing & linting

There is **no molecule scenario** for `init`, and it is **not** in the CI lint
matrix (CI in `.github/workflows/molecule.yml` only lints roles whose molecule
scenario carries an `ENABLE_CI` marker). Consequences:

- The role carries a backlog of **pre-existing** lint violations (truthy
  `yes`/`no`, the bare/non-FQCN step-0 `user:` task, implicit-octal
  `0644`/`0700`/`0600`, `ignore-errors`, `command-instead-of-shell` on
  `update grub`, …). When you edit, judge **your diff** against lint — don't
  adopt the whole backlog.
- No converge/verify harness exists. Validate behaviour by reasoning or by a
  throwaway playbook exercising the relevant Ansible mechanics (handler
  ordering, `set_fact` + `changed_when` + `notify`, `register` of a skipped
  task, etc.).

Lint command (activate the project's Ansible venv first — its path is in your
local agent config, not in this repo):

```bash
# after activating the venv:
cd roles/init && timeout 90 ansible-lint --offline
```

- Use the project's Ansible venv (never the host's system / `~/.local/bin`
  install). Don't install/upgrade anything in it; ask the operator.
- `--offline` is mandatory in the agent sandbox: with no network egress, plain
  `ansible-lint` hangs *indefinitely* on Galaxy collection-prep (hung, not
  slow). A correct run is ~25s.
- `timeout 90` bounds it; hitting the timeout means something's wrong
  (lock/network) — debug it, don't wait.
- Stale-lock recovery: a `SIGKILL`ed run leaves `<repo-root>/.ansible/.lock`;
  symptom is exit code 4 ("Timeout waiting for another instance … to release
  the lock") or a fresh run that hangs. `rm -f <repo-root>/.ansible/.lock` and
  rerun; don't run concurrent `ansible-lint` instances (they contend on it).

## Gotchas

- **grub-pc install device vs kernel cmdline** are different concerns in
  different files (`grub.yml` vs `kernel.yml`); don't conflate them.
- **`/etc/hosts` is immutable** when `init_block_hosts_file` — any external
  write to it must go through the chattr toggle, which the role handles.
- **`s3.yml` depends on the step-0 `user:` task** for `_init_user_info.home`;
  don't reorder it before that task.
- **Reboot/handler ordering** — see the Handlers & reboot section before
  touching reboot, `update grub`, `flush_handlers`, or handler order.
- **Never commit host data** (absolute local paths, usernames, internal
  hostnames) into this or any repo file — use relative paths/placeholders.
