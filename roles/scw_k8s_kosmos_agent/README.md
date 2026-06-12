# scw_k8s_kosmos_agent

Ansible role that joins a host (bare-metal or VM, any cloud) to a
[Scaleway Kosmos](https://www.scaleway.com/en/kosmos/) multi-cloud Kubernetes
pool using the official [scaleway/k8s-agent](https://github.com/scaleway/k8s-agent)
binary.

The role:

1. installs a **versioned, cached** agent binary under the role base directory
   (download is skipped entirely when the binary for the requested version is
   already present);
2. optionally performs a **node reset** (disabled by default) so the agent
   re-registers the node from scratch under a new name;
3. cleans up a **legacy apt-based** Scaleway k8s installation (packages +
   repository files, with a reboot) when one is detected;
4. runs the agent with the pool credentials to register the node.

## Requirements

- Debian-based OS (apt)
- systemd
- Root access
- Outbound HTTPS to `github.com` (binary download) and to the Scaleway API
- An existing Kosmos pool (region, pool ID) and a Scaleway secret key

## Quick start

```yaml
- hosts: k8s-nodes
  become: true
  roles:
    - role: kogeler.mini_pig.scw_k8s_kosmos_agent
      vars:
        scw_k8s_kosmos_agent_pool_region: "fr-par"
        scw_k8s_kosmos_agent_pool_id: "be99dfda-edf3-4737-9184-1d3048a775aa"
        scw_k8s_kosmos_agent_secret_key: "{{ vault_scw_secret_key }}"
```

## How it works

### Binary caching

The agent binary is stored under a versioned path:

```
{{ scw_k8s_kosmos_agent_base_dir }}/kosmos_agent_{{ scw_k8s_kosmos_agent_version }}
# e.g. /opt/scw_k8s_kosmos_agent/kosmos_agent_v0.1.5
```

The first task stats this path. If the file exists, the whole download flow is
skipped and the cached binary is used as-is. If it does not exist, the role:

1. downloads the release asset into a temporary directory (with retries — see
   the `*_download_*` variables);
2. verifies the binary (`-version` runs, `-h` advertises `-kosmos` support);
3. creates the base directory and copies the binary to the versioned path.

The download-validate-then-install order keeps the invariant *"the versioned
file exists ⇒ it is valid"*: a corrupted download or an interrupted play never
leaves a broken binary at the final path, so later runs cannot silently reuse
it. Bumping `scw_k8s_kosmos_agent_version` changes the path and triggers a
fresh download; binaries of previous versions are left in the base directory
and are not cleaned up automatically.

### Node reset (opt-in)

When `scw_k8s_kosmos_agent_reset_node_enabled: true`, before the agent run the
role:

1. stops `containerd.service` and `kubelet.service` (each is skipped when the
   unit does not exist on the host);
2. removes the agent state files `/etc/scw-k8s-userdata` and
   `/etc/scw-k8s-versions.json`.

Without its state files the agent registers the node from scratch **under a
new name**, even if the node is already joined and working. The toggle is
`false` by default; enable it explicitly (e.g. with `-e`) for the single run
that should re-register the node, then turn it back off.

### Legacy apt cleanup

If any of the legacy Scaleway apt packages
(`scw_k8s_kosmos_agent_legacy_apt_packages`) are installed, the role unholds
and purges them, removes the legacy apt repository files, **reboots the host**
(`scw_k8s_kosmos_agent_legacy_apt_reboot_timeout`), and refreshes service
facts before proceeding. Set
`scw_k8s_kosmos_agent_legacy_apt_cleanup_enabled: false` to skip this even
when legacy packages are present.

### Randomized SNAT pause (iptables role integration)

The agent's outbound calls need a deterministic source IP. On hosts where the
`iptables` role installed the randomized-SNAT module (`mpig_randomized_snat`
nft table), the role pauses it for the agent run: stops the drift-check timer,
drops the table, verifies it is gone, runs the agent, and then — in an
`always:` section, so a failed agent run still restores the chain — restarts
the timer, re-applies the chain and verifies it is back. When the table is not
loaded, these tasks short-circuit and the agent runs straight through. See
`roles/iptables` (README "Randomized SNAT pool") for the pause-API contract.

### Agent run

The agent is executed as `<binary> -kosmos` with `POOL_REGION`, `POOL_ID` and
`SCW_SECRET_KEY` taken from the role variables. The task runs on every play
while `scw_k8s_kosmos_agent_run_agent` is `true` and reports `changed` on
success; agent output (stdout + stderr) is printed even when the run fails.

## Role variables

### Binary

| Variable | Default | Description |
|---|---|---|
| `scw_k8s_kosmos_agent_version` | `"v0.1.5"` | Agent version (GitHub release tag). Changing it changes the versioned binary path and triggers a download |
| `scw_k8s_kosmos_agent_base_dir` | `/opt/scw_k8s_kosmos_agent` | Role base directory; versioned binaries are stored here as `kosmos_agent_<version>` |
| `scw_k8s_kosmos_agent_binary_url` | GitHub release URL derived from the version | Download URL of the agent binary |
| `scw_k8s_kosmos_agent_binary_download_timeout` | `60` | Download timeout, seconds |
| `scw_k8s_kosmos_agent_binary_download_retries` | `3` | Number of retries when the download fails |
| `scw_k8s_kosmos_agent_binary_download_delay` | `60` | Delay between retries, seconds |

### Registration

| Variable | Default | Description |
|---|---|---|
| `scw_k8s_kosmos_agent_pool_region` | `""` | Kosmos pool region (e.g. `fr-par`), **required** |
| `scw_k8s_kosmos_agent_pool_id` | `""` | Kosmos pool ID, **required** |
| `scw_k8s_kosmos_agent_secret_key` | `""` | Scaleway secret key, **required** — keep it in a vault |
| `scw_k8s_kosmos_agent_run_agent` | `true` | Whether to actually execute the agent (the SNAT pause/resume wrapper is skipped too when `false`) |

### Node reset

| Variable | Default | Description |
|---|---|---|
| `scw_k8s_kosmos_agent_reset_node_enabled` | `false` | Stop `containerd.service`/`kubelet.service` and remove `/etc/scw-k8s-userdata` + `/etc/scw-k8s-versions.json` before the agent run, forcing re-registration under a new node name |

### Legacy apt cleanup

| Variable | Default | Description |
|---|---|---|
| `scw_k8s_kosmos_agent_legacy_apt_cleanup_enabled` | `true` | Purge legacy Scaleway apt packages (with a reboot) when any of them are installed |
| `scw_k8s_kosmos_agent_legacy_apt_packages` | scaleway-cni-plugins, -containerd, -crictl, -kubectl, -kubelet, -runc | Legacy packages to detect and purge |
| `scw_k8s_kosmos_agent_legacy_apt_files` | scaleway-container apt list + keyring | Legacy apt repository files to remove |
| `scw_k8s_kosmos_agent_legacy_apt_reboot_timeout` | `1200` | Reboot timeout after the package cleanup, seconds |

### Containerd path override

| Variable | Default | Description |
|---|---|---|
| `scw_k8s_kosmos_agent_remove_containerd_path_override` | `true` | **Currently not referenced by any task** in this version of the role |
| `scw_k8s_kosmos_agent_containerd_path_override_file` | `/etc/systemd/system/containerd.service.d/10-path.conf` | **Currently not referenced by any task** in this version of the role |

### Internal (vars/main.yml)

Not meant to be overridden: `_scw_k8s_kosmos_agent_binary_path` (versioned
binary path), `_scw_k8s_kosmos_agent_reset_units`
(`containerd.service`, `kubelet.service`) and
`_scw_k8s_kosmos_agent_reset_files` (`/etc/scw-k8s-userdata`,
`/etc/scw-k8s-versions.json`).

## Tags

Each step is also available under a dashed alias
(`scw-k8s-kosmos-agent-...`).

| Tag | Scope |
|---|---|
| `scw_k8s_kosmos_agent` | Whole role |
| `scw_k8s_kosmos_agent_binary` | Binary install/cache only |
| `scw_k8s_kosmos_agent_reset_node` | Node reset step only (still gated by `scw_k8s_kosmos_agent_reset_node_enabled`) |
| `scw_k8s_kosmos_agent_add_node` | Node registration — includes the reset step, so a reset-enabled run with this tag still cleans the state before the agent starts |

Example — re-register a node under a new name:

```bash
ansible-playbook main.yml \
  --tags scw-k8s-kosmos-agent-add-node \
  -e scw_k8s_kosmos_agent_reset_node_enabled=true
```

## License

Apache-2.0
