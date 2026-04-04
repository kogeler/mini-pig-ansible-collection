# naive_proxy — CI Debug Playbook

Reusable debug snippets for GitHub Actions workflow. Copy the relevant sections
into `.github/workflows/molecule.yml` when investigating failures.

## Relax host security (Ubuntu 24.04)

```yaml
- name: Relax host security restrictions
  run: |
    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
    sudo sysctl -w kernel.yama.ptrace_scope=0
    sudo systemctl stop apparmor || true
    sudo systemctl disable apparmor || true
    echo "apparmor_restrict_unprivileged_userns=$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo N/A)"
    echo "yama.ptrace_scope=$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo N/A)"
    sudo aa-status 2>&1 | head -5 || true
```

## tcpdump — host level

Capture SOCKS5 client traffic (port 1080) and HAProxy TLS (port 8443).
Start **before** converge so the capture covers the entire session.

```yaml
- name: Start host tcpdump
  run: |
    sudo tcpdump -i any -w /tmp/naive-host.pcap \
      'port 8443 or port 1080' &
    echo $! > /tmp/tcpdump-host.pid
    sleep 1
    echo "tcpdump host pid=$(cat /tmp/tcpdump-host.pid)"
```

## tcpdump — pod network namespace

Capture plain HTTP between HAProxy and naive backend (port 8080),
HAProxy and decoy (port 8081), and HTTPS frontend internal (port 8444).
Start **before** verify — the pod must already exist.

```yaml
- name: Start pod tcpdump
  run: |
    INFRA_ID=$(sudo podman pod inspect naive-pod --format '{{.InfraContainerID}}' 2>/dev/null || echo "")
    echo "Infra container ID=$INFRA_ID"
    if [ -n "$INFRA_ID" ]; then
      INFRA_PID=$(sudo podman inspect --format '{{.State.Pid}}' "$INFRA_ID" 2>/dev/null || echo 0)
      echo "Infra container PID=$INFRA_PID"
    else
      INFRA_PID=$(sudo podman inspect --format '{{.State.Pid}}' naive-haproxy 2>/dev/null || echo 0)
      echo "Fallback to naive-haproxy PID=$INFRA_PID"
    fi
    if [ "$INFRA_PID" -gt 0 ] 2>/dev/null; then
      sudo nsenter -t "$INFRA_PID" -n tcpdump -i lo -w /tmp/naive-pod.pcap \
        'port 8080 or port 8081 or port 8444' &
      echo $! > /tmp/tcpdump-pod.pid
      sleep 1
      echo "tcpdump pod pid=$(cat /tmp/tcpdump-pod.pid)"
      ps -p "$(cat /tmp/tcpdump-pod.pid)" && echo "tcpdump running" || echo "tcpdump NOT running"
    else
      echo "ERROR: could not find any container PID for pod netns"
    fi
```

## Manual SOCKS5 diagnostic

Runs the naive client on the host (not in a container) and tests three paths.
Useful for isolating whether the problem is in the client binary or the
HAProxy/backend path.

```yaml
- name: Manual SOCKS5 diagnostic
  run: |
    set +e
    echo "=== Install pebble CA ==="
    sudo curl -sk https://naive.test:15000/roots/0 -o /usr/local/share/ca-certificates/pebble-ca.crt
    sudo update-ca-certificates

    echo "=== Start naive client ==="
    sudo /tmp/naiveproxy-v143.0.7499.109-2-linux-x64/naive \
      --listen=socks://127.0.0.1:1080 \
      --proxy=https://testuser:testpass@naive.test:8443 \
      --log &
    NAIVE_PID=$!
    sleep 3

    echo "=== Test 1: HTTPS proxy (should work) ==="
    curl -v --max-time 10 --proxy-insecure \
      -x https://testuser:testpass@naive.test:8443 \
      http://naive.test:8081/ 2>&1 || true

    echo ""
    echo "=== Test 2: SOCKS5 tunnel (fails in CI) ==="
    curl -v --max-time 10 \
      --socks5-hostname 127.0.0.1:1080 \
      http://naive.test:8081/ 2>&1 || true

    echo ""
    echo "=== Test 3: SOCKS5 to external site ==="
    curl -v --max-time 10 \
      --socks5-hostname 127.0.0.1:1080 \
      http://httpbin.org/ip 2>&1 || true

    echo ""
    echo "=== Naive client log ==="
    sleep 2
    kill $NAIVE_PID 2>/dev/null || true
    wait $NAIVE_PID 2>/dev/null || true
    cat /tmp/naive-client.log 2>/dev/null || echo "no log file"

    echo ""
    echo "=== Naive backend container logs ==="
    sudo podman logs naive-backend 2>&1 | tail -30 || true

    echo ""
    echo "=== HAProxy container logs (last 30) ==="
    sudo podman logs naive-haproxy 2>&1 | tail -30 || true
```

## Collect debug info (post-failure)

Full environment and service state dump. Add as a step that runs
`if: steps.<step>.outcome == 'failure'`.

```yaml
- name: Collect debug info
  if: failure()
  run: |
    set +e

    # Stop tcpdumps
    sudo kill "$(cat /tmp/tcpdump-host.pid 2>/dev/null)" 2>/dev/null || true
    sudo kill "$(cat /tmp/tcpdump-pod.pid 2>/dev/null)" 2>/dev/null || true
    sleep 2

    echo "::group::tcpdump host: SOCKS5 (port 1080)"
    sudo tcpdump -r /tmp/naive-host.pcap -n -X 'port 1080' 2>&1 | tail -200 || echo "no pcap"
    echo "::endgroup::"

    echo "::group::tcpdump host: port 8443 SYN/FIN/RST"
    sudo tcpdump -r /tmp/naive-host.pcap -n 'port 8443 and (tcp[tcpflags] & (tcp-syn|tcp-fin|tcp-rst) != 0)' 2>&1 | tail -100 || echo "no pcap"
    echo "::endgroup::"

    echo "::group::tcpdump pod: HAProxy to backend (port 8080, plain HTTP)"
    sudo tcpdump -r /tmp/naive-pod.pcap -n -A 'port 8080' 2>&1 | tail -300 || echo "no pcap"
    echo "::endgroup::"

    echo "::group::tcpdump pod: HAProxy to decoy (port 8081)"
    sudo tcpdump -r /tmp/naive-pod.pcap -n -A 'port 8081' 2>&1 | tail -100 || echo "no pcap"
    echo "::endgroup::"

    echo "::group::tcpdump pod: HAProxy HTTPS frontend (port 8444)"
    sudo tcpdump -r /tmp/naive-pod.pcap -n -q 'port 8444' 2>&1 | tail -50 || echo "no pcap"
    echo "::endgroup::"

    echo "::group::OS and environment"
    cat /etc/os-release
    uname -a
    echo "podman: $(podman --version 2>&1 || echo 'not found')"
    echo "ansible: $(ansible --version | head -1)"
    echo "molecule: $(molecule --version | head -1)"
    echo "python: $(python3 --version)"
    systemctl --version | head -2
    echo "::endgroup::"

    echo "::group::AppArmor status"
    sudo aa-status 2>&1 || true
    cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo "sysctl not found"
    cat /proc/sys/kernel/apparmor_restrict_unprivileged_unconfined 2>/dev/null || echo "sysctl not found"
    echo "::endgroup::"

    echo "::group::Podman info"
    sudo podman info 2>&1 | head -60
    echo "::endgroup::"

    echo "::group::Network and DNS"
    cat /etc/hosts
    ip addr show 2>/dev/null | head -40
    echo "::endgroup::"

    echo "::group::Systemd failed units"
    systemctl --failed --no-pager 2>&1
    echo "::endgroup::"

    echo "::group::Podman pods and containers"
    sudo podman pod ls --no-trunc 2>&1 || true
    sudo podman ps -a --no-trunc 2>&1 || true
    echo "::endgroup::"

    echo "::group::Pod and container logs"
    for unit in podman-naive-pod podman-naive-haproxy podman-naive-decoy podman-naive-backend podman-naive-pebble naive-acme-renew naive-molecule-client; do
      echo "=== journalctl -u ${unit}.service ==="
      sudo journalctl -u "${unit}.service" --no-pager -n 200 2>&1 || true
    done
    echo "::endgroup::"

    echo "::group::Naive client log"
    sudo cat /tmp/naive-client.log 2>/dev/null || echo "no naive client log"
    echo "::endgroup::"

    echo "::group::ACME / certificate state"
    ls -la /opt/naive-proxy/certs/ 2>/dev/null || echo "certs dir not found"
    openssl x509 -in /opt/naive-proxy/certs/fullchain.pem -noout -subject -issuer -dates 2>/dev/null || echo "no cert"
    echo "::endgroup::"

    echo "::group::HAProxy config"
    cat /opt/naive-proxy/haproxy.cfg 2>/dev/null || echo "haproxy.cfg not found"
    echo "::endgroup::"

    echo "::group::Rendered systemd units"
    for f in /etc/systemd/system/podman-naive-*.service /etc/systemd/system/naive-acme-renew.* /etc/systemd/system/podman-naive-molecule-client.service; do
      if [ -f "$f" ]; then
        echo "=== $f ==="
        sudo cat "$f"
      fi
    done
    echo "::endgroup::"

    echo "::group::dmesg (denied/audit)"
    sudo dmesg | grep -iE "denied|audit|apparmor|seccomp" | tail -50 || echo "no matches"
    echo "::endgroup::"

    echo "::group::nftables / iptables"
    sudo nft list ruleset 2>&1 | head -100 || echo "nft not found"
    sudo iptables -L -n -v 2>&1 | head -50 || echo "iptables not found"
    sudo iptables -t nat -L -n -v 2>&1 | head -50 || echo "iptables nat not found"
    echo "::endgroup::"

    echo "::group::Listening ports"
    sudo ss -tlnp 2>&1 || true
    echo "::endgroup::"

    echo "::group::Container image list"
    sudo podman images 2>&1 || true
    echo "::endgroup::"

    echo "::group::/etc/subuid and /etc/subgid"
    cat /etc/subuid 2>/dev/null || true
    cat /etc/subgid 2>/dev/null || true
    echo "::endgroup::"
```
