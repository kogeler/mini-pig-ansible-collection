#!/usr/bin/env bash

# TEMPORARY CI DIAGNOSTICS: remove with the workflow hook after the Podman
# restart failure is resolved.
set -uo pipefail
exec 2>&1

run_diagnostic() {
  local title="$1"
  shift

  printf '::group::%s\n' "$title"
  "$@"
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'Diagnostic command exited with rc=%s\n' "$rc"
  fi
  printf '::endgroup::\n'
}

units=(
  podman-telemt-pod.service
  podman-telemt-pebble.service
  podman-telemt-decoy.service
  podman-telemt.service
)

run_diagnostic "Host and Podman versions" bash -c '
  uname -a
  systemd --version
  podman --version
  runc --version || true
  crun --version || true
'
run_diagnostic "Podman host configuration" sudo podman info --debug
run_diagnostic "Podman pods" sudo podman pod ps --no-trunc
run_diagnostic "Podman containers" sudo podman ps --all --pod --no-trunc
run_diagnostic "Failed systemd units" sudo systemctl --failed --no-pager -l
run_diagnostic "Podman cgroup units" sudo systemctl list-units \
  --all --no-pager -l 'machine-libpod*' 'libpod*'

for unit in "${units[@]}"; do
  run_diagnostic "systemctl status ${unit}" \
    sudo systemctl status "$unit" --no-pager -l
  run_diagnostic "systemctl show ${unit}" \
    sudo systemctl show "$unit" --no-pager \
      --property=ActiveState,SubState,Result,MainPID,ControlPID,ControlGroup \
      --property=ExecMainCode,ExecMainStatus,ExecStart,ExecStop,ExecStopPost
  run_diagnostic "journalctl ${unit}" \
    sudo journalctl -xeu "$unit" --no-pager -n 300 -o short-precise
  run_diagnostic "systemctl cat ${unit}" \
    sudo systemctl cat "$unit" --no-pager
done

run_diagnostic "Inspect telemt pod" sudo podman pod inspect telemt-pod
for container in telemt-pebble telemt-decoy telemt; do
  run_diagnostic "Inspect ${container}" sudo podman inspect "$container"
done

printf '::group::Container ID files\n'
for cidfile in /run/podman-telemt*.ctr-id; do
  if [ -f "$cidfile" ]; then
    printf '%s: ' "$cidfile"
    sudo cat "$cidfile"
  fi
done
printf '::endgroup::\n'

exit 0
