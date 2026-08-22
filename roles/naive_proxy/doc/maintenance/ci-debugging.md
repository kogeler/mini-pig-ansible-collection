# CI debugging

This runbook preserves the reusable parts of the former role-level
`CI_DEBUG.md` and updates them for the current topology. Add only the section
needed for a temporary investigation; do not permanently relax runner security
or run downloaded clients directly on the host.

The current workflow discovers every scenario whose directory contains
`ENABLE_CI`, installs collection dependencies, and runs the scenario's own
Molecule test sequence. Local reproduction still goes through the role
[Makefile](testing.md).

Always identify the execution boundary first:

- in `gha`, the runner is the managed target, so role units and `naive-pod`
  are visible directly (normally through `sudo`);
- in Podman scenarios, the managed target is the outer
  `molecule-naive-proxy` container. Run target-state commands with
  `podman exec molecule-naive-proxy ...` and copy artifacts back with
  `podman cp`; runner-level Podman output alone shows only the outer instance.

## Capture the baseline first

Add a step before the experiment so before/after evidence is available:

```yaml
- name: Record host security baseline
  run: |
    set +e
    uname -a
    cat /etc/os-release
    sysctl kernel.apparmor_restrict_unprivileged_userns
    sysctl kernel.apparmor_restrict_unprivileged_unconfined
    sysctl kernel.yama.ptrace_scope
    sudo aa-status
    podman info
```

## Disposable-runner security experiment

Use this only to determine whether AppArmor/user-namespace restrictions cause
a failure. It broadens the security boundary for the rest of the job and MUST
NOT become the normal workflow:

```yaml
- name: Relax host security restrictions for diagnosis
  run: |
    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
    sudo sysctl -w kernel.yama.ptrace_scope=0
    sudo systemctl stop apparmor || true
    sudo systemctl disable apparmor || true
    echo "apparmor_restrict_unprivileged_userns=$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo N/A)"
    echo "yama.ptrace_scope=$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo N/A)"
    sudo aa-status || true
```

If the failure disappears, narrow the responsible path next; do not treat a
disabled host policy as the fix. The nested Podman scenarios already use the
specific capabilities, `/dev/fuse`, optional `/dev/net/tun`, and test-only
AppArmor settings recorded in their `molecule.yml` files.

## Host-level packet capture (`gha`)

The standard Molecule public listener is `8443`. In the native `gha` scenario,
start before the action that must be observed and keep the filter narrow:

```yaml
- name: Start bounded host capture
  run: |
    sudo timeout 600 tcpdump -i any -s 96 -w /tmp/naive-host.pcap \
      'port 8443 or port 1080' &
    echo $! | sudo tee /tmp/tcpdump-host.pid
```

For `singbox-stress` and `anytls-stress`, also include the ssl_router public
port and inspect the scenario variables before hard-coding a filter. Those are
Podman scenarios, so capture inside `molecule-naive-proxy`; a runner-host pcap
of a slirp connection does not expose their inner role ports directly.

## Pod-network packet capture

Run on the managed target after converge, when `naive-pod` exists, and before
verify. For a Podman scenario, place this body in a temporary script, execute
it through `podman exec molecule-naive-proxy`, and later retrieve the pcap with
`podman cp`; the outer instance is already managed as root, so remove the
`sudo` prefixes inside that script:

```yaml
- name: Start bounded pod-netns capture
  run: |
    set -euo pipefail
    INFRA_ID=$(sudo podman pod inspect naive-pod \
      --format '{{.InfraContainerID}}')
    INFRA_PID=$(sudo podman inspect "$INFRA_ID" --format '{{.State.Pid}}')
    echo "infra container=${INFRA_ID} pid=${INFRA_PID}"
    sudo nsenter -t "$INFRA_PID" -n \
      timeout 600 tcpdump -i lo -s 128 -w /tmp/naive-pod.pcap \
      'port 8080 or port 8081 or port 8444 or port 8445 or port 10443' &
    echo $! | sudo tee /tmp/tcpdump-pod.pid
```

The internal ports are defined in
[`vars/main.yml`](../../vars/main.yml): Naive `8080`, Caddy `8081`, local
HAProxy TLS `8444`, AnyTLS `8445`, and acme.sh ALPN `10443`.

## Post-failure collection

Add this as a final `if: failure()` step for `gha`, or execute its target-state
portion inside `molecule-naive-proxy` for a Podman scenario. It is deliberately
read-only except for stopping diagnostic captures:

```yaml
- name: Collect naive_proxy failure evidence
  if: failure()
  run: |
    set +e

    sudo kill "$(cat /tmp/tcpdump-host.pid 2>/dev/null)" 2>/dev/null || true
    sudo kill "$(cat /tmp/tcpdump-pod.pid 2>/dev/null)" 2>/dev/null || true

    echo '::group::OS and toolchain'
    cat /etc/os-release
    uname -a
    python3 --version
    ansible --version
    molecule --version
    podman --version
    systemctl --version
    echo '::endgroup::'

    echo '::group::AppArmor and kernel policy'
    sudo aa-status || true
    cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || true
    cat /proc/sys/kernel/apparmor_restrict_unprivileged_unconfined 2>/dev/null || true
    cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || true
    sudo dmesg | grep -iE 'denied|audit|apparmor|seccomp' | tail -100 || true
    echo '::endgroup::'

    echo '::group::Podman and systemd state'
    sudo podman info
    systemctl --failed --no-pager
    sudo podman pod ls --no-trunc
    sudo podman ps -a --no-trunc
    sudo podman images --no-trunc
    sudo ss -tlnp
    echo '::endgroup::'

    echo '::group::Role journals'
    for unit in \
      podman-naive-pod.service \
      podman-naive-haproxy.service \
      podman-naive-decoy.service \
      podman-naive-backend.service \
      podman-naive-anytls.service \
      podman-naive-pebble.service \
      naive-acme-renew.service \
      naive-acme-renew.timer \
      podman-naive-molecule-client.service \
      podman-singbox-naive-molecule-client.service \
      podman-singbox-anytls-molecule-client.service; do
      echo "===== ${unit} ====="
      sudo journalctl -u "$unit" --no-pager -n 300 || true
    done
    echo '::endgroup::'

    echo '::group::Rendered state'
    sudo find /opt/naive-proxy -maxdepth 2 -type f -printf '%m %p\n' || true
    sudo sed -n '1,260p' /opt/naive-proxy/haproxy.cfg || true
    sudo sed -n '1,260p' /opt/naive-proxy/anytls.json || true
    sudo openssl x509 -in /opt/naive-proxy/certs/fullchain.pem \
      -noout -subject -issuer -serial -dates || true
    for file in /etc/systemd/system/podman-naive-*.service \
      /etc/systemd/system/naive-acme-renew.*; do
      [ ! -f "$file" ] || { echo "===== ${file} ====="; sudo cat "$file"; }
    done
    echo '::endgroup::'

    echo '::group::Network policy'
    ip addr show
    ip route show table all
    sudo nft list ruleset || true
    sudo iptables -L -n -v || true
    sudo iptables -t nat -L -n -v || true
    cat /etc/subuid || true
    cat /etc/subgid || true
    echo '::endgroup::'

    echo '::group::Capture summaries'
    sudo tcpdump -r /tmp/naive-host.pcap -n \
      'tcp[tcpflags] & (tcp-syn|tcp-fin|tcp-rst) != 0' 2>&1 | tail -200 || true
    sudo tcpdump -r /tmp/naive-pod.pcap -n -q 2>&1 | tail -300 || true
    echo '::endgroup::'
```

Upload the full Molecule output, journals, rendered configs, and pcaps as job
artifacts rather than relying on the truncated console tail. Packet captures
and complete HAProxy traces can contain sensitive metadata.

For a Podman scenario, a compact outer/inner state split is often enough to
start:

```yaml
- name: Collect nested Podman state
  if: failure()
  run: |
    set +e
    podman ps -a --no-trunc
    podman exec molecule-naive-proxy systemctl --failed --no-pager
    podman exec molecule-naive-proxy podman pod ls --no-trunc
    podman exec molecule-naive-proxy podman ps -a --no-trunc
    podman exec molecule-naive-proxy journalctl \
      -u podman-naive-haproxy.service --no-pager -n 300
    podman cp molecule-naive-proxy:/tmp/naive-pod.pcap \
      /tmp/naive-pod.pcap || true
```

## Supported client reproduction

Do not restore the former snippet that downloaded an old Naive binary and ran
it directly on the runner. The current `default`/`debian-bookworm` verification
runs the pinned official Naive client in a nested container; the stress
scenarios run the released-SFA tuple in their own TUN-enabled client container.
Use those paths so version, trust, routes, and cleanup remain controlled by the
harness.
