#!/usr/bin/env bash

set -uo pipefail
LC_ALL=C
export PAGER=cat
export SYSTEMD_PAGER=cat
export SYSTEMD_LESS=FRSXMK

usage() {
    cat <<'EOF'
Usage:
  sudo ./lvm_diag_stdout.sh /path/to/lvm.py

Examples:
  sudo ./lvm_diag_stdout.sh /usr/local/bin/lvm.py
  sudo ./lvm_diag_stdout.sh /etc/telegraf/scripts/lvm.py
EOF
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

sanitize_name() {
    local in="$1"
    in="${in#/dev/}"
    in="${in//\//_}"
    in="${in//[^a-zA-Z0-9_.-]/_}"
    printf '%s' "$in"
}

run_section() {
    local name="$1"
    shift
    local rc=0

    echo "===== BEGIN ${name} ====="
    echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "$ $*"
    "$@" 2>&1 || rc=$?
    echo "# rc=${rc}"
    echo "===== END ${name} ====="
    echo
    return 0
}

run_optional_section() {
    local name="$1"
    local cmd="$2"
    shift 2

    if command_exists "$cmd"; then
        run_section "$name" "$cmd" "$@"
    else
        echo "===== BEGIN ${name} ====="
        echo "# command not found: ${cmd}"
        echo "# rc=127"
        echo "===== END ${name} ====="
        echo
    fi
}

print_list_section() {
    local name="$1"
    shift
    local values=("$@")

    echo "===== BEGIN ${name} ====="
    if (( ${#values[@]} == 0 )); then
        echo "(none)"
    else
        printf '%s\n' "${values[@]}"
    fi
    echo "===== END ${name} ====="
    echo
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -ne 1 ]]; then
    usage
    exit 1
fi

LVM_PY_PATH="$1"
LVS_COLS="vg_name,lv_name,lv_path,lv_attr,lv_active,segtype,lv_role,lv_health_status,sync_percent,copy_percent,devices"
LOG_RE="lvm|dm-|device-mapper|raid|I/O error|failed to IDENTIFY|Buffer I/O error|blk_update_request|critical medium error"

echo "===== BEGIN META ====="
echo "timestamp_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "hostname: $(hostname -f 2>/dev/null || hostname)"
echo "kernel: $(uname -r)"
echo "user: $(id -un)"
echo "uid: $(id -u)"
echo "lvm_py_path: ${LVM_PY_PATH}"
echo "===== END META ====="
echo

echo "===== BEGIN command_availability ====="
for cmd in \
    lvm pvs vgs lvs pvdisplay vgdisplay pvck vgck lvmdevices \
    dmsetup lsblk blkid udevadm smartctl mdadm journalctl nvme; do
    if command_exists "$cmd"; then
        echo "${cmd}: yes ($(command -v "$cmd"))"
    else
        echo "${cmd}: no"
    fi
done
echo "===== END command_availability ====="
echo

run_section "lsblk_all" lsblk -e7 -o NAME,KNAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,PKNAME,MODEL,SERIAL,WWN,STATE,ROTA,TRAN
run_section "blkid_all" blkid
run_section "blkid_lvm2_member" blkid -t TYPE=LVM2_member -o full
run_section "disk_by_id" ls -l /dev/disk/by-id
run_section "disk_by_uuid" ls -l /dev/disk/by-uuid

run_section "lvm_version" lvm version
run_section "pvs_basic" pvs -a -o pv_name,vg_name,pv_attr,pv_size,pv_free,dev_size
run_section "pvs_extended" pvs -a -o pv_name,pv_uuid,pv_missing,pv_attr,vg_name,pv_size,pv_free,dev_size,pe_start,pe_count,pe_alloc_count
run_section "pvs_json" pvs -a -o pv_name,pv_uuid,pv_missing,pv_attr,vg_name,pv_size,pv_free,dev_size --reportformat json
run_section "pvs_segments" pvs --segments -a -o pv_name,pv_uuid,pv_missing,vg_name,lv_name,seg_start_pe,seg_size_pe
run_section "vgs_basic" vgs -o vg_name,vg_attr,vg_size,vg_free,pv_count,lv_count
run_section "vgs_extended" vgs -o vg_name,vg_uuid,vg_attr,vg_size,vg_free,pv_count,lv_count,vg_mda_count,vg_mda_used_count
run_section "lvs_segments_text" lvs -a --segments -o "$LVS_COLS"
run_section "lvs_segments_json" lvs -a --segments -o "$LVS_COLS" --reportformat json
run_section "lvs_raid_only" lvs -a --segments -S 'segtype=~^raid' -o "$LVS_COLS"
run_section "lvs_all_text" lvs -a -o vg_name,lv_name,lv_attr,lv_active,lv_role,lv_layout,lv_health_status,devices
run_section "lvm_fullreport_json" lvm fullreport -a -o pv_name,pv_uuid,pv_missing,vg_name,lv_name,lv_attr,lv_active,lv_role,lv_health_status,segtype,devices --reportformat json
run_section "lvmconfig_devices" lvmconfig --type full devices/use_devicesfile devices/devicesfile devices/filter devices/global_filter
run_optional_section "lvmdevices_list" lvmdevices --list
run_optional_section "lvmdevices_check" lvmdevices --check

if [[ -f "$LVM_PY_PATH" ]]; then
    run_section "lvm_py" python3 "$LVM_PY_PATH"
else
    echo "===== BEGIN lvm_py ====="
    echo "File not found: $LVM_PY_PATH"
    echo "# rc=127"
    echo "===== END lvm_py ====="
    echo
fi

mapfile -t vg_names < <(
    vgs --noheadings -o vg_name 2>/dev/null \
        | awk '{$1=$1; if ($1 != "") print $1}' \
        | sort -u
)
print_list_section "vg_names" "${vg_names[@]}"

for vg in "${vg_names[@]}"; do
    safe="$(sanitize_name "$vg")"
    run_section "vgdisplay_${safe}" vgdisplay -v "$vg"
    run_optional_section "vgck_${safe}" vgck "$vg"
done

mapfile -t pv_paths < <(
    pvs --noheadings -o pv_name 2>/dev/null \
        | awk '{$1=$1; if ($1 ~ "^/dev/") print $1}' \
        | sort -u
)
print_list_section "pv_paths" "${pv_paths[@]}"

for pv in "${pv_paths[@]}"; do
    safe="$(sanitize_name "$pv")"
    run_section "pvdisplay_m_${safe}" pvdisplay -m "$pv"
    run_optional_section "pvck_${safe}" pvck "$pv"
done

run_section "dmsetup_tree" dmsetup ls --tree
run_section "dmsetup_info" dmsetup info -c
run_section "dmsetup_table_all" dmsetup table

run_section "proc_mdstat" cat /proc/mdstat
run_optional_section "mdadm_detail_scan" mdadm --detail --scan

mapfile -t md_arrays < <(
    awk '/^md[0-9]+/ {print "/dev/"$1}' /proc/mdstat 2>/dev/null | sort -u
)
print_list_section "md_arrays" "${md_arrays[@]}"

for md in "${md_arrays[@]}"; do
    safe="$(sanitize_name "$md")"
    run_optional_section "mdadm_detail_${safe}" mdadm --detail "$md"
done

mapfile -t raid_paths_all < <(
    lvs --noheadings --segments -a -S 'segtype=~^raid' --separator '|' -o lv_path 2>/dev/null \
        | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $1); if ($1 != "") print $1}' \
        | sort -u
)
print_list_section "raid_lv_paths_all" "${raid_paths_all[@]}"

mapfile -t raid_paths_public < <(
    lvs --noheadings --segments -a -S 'segtype=~^raid' --separator '|' -o lv_path,lv_role 2>/dev/null \
        | awk -F'|' '{
            path=$1; role=$2
            gsub(/^[ \t]+|[ \t]+$/, "", path)
            gsub(/^[ \t]+|[ \t]+$/, "", role)
            role=tolower(role)
            if (path != "" && role ~ /(^|,)public(,|$)/) print path
        }' \
        | sort -u
)
print_list_section "raid_lv_paths_public" "${raid_paths_public[@]}"

for lv_path in "${raid_paths_public[@]}"; do
    safe="$(sanitize_name "$lv_path")"
    run_section "dmsetup_status_${safe}" dmsetup status "$lv_path"
    run_section "dmsetup_table_${safe}" dmsetup table "$lv_path"
    run_section "lvs_details_${safe}" lvs -a -o "$LVS_COLS" "$lv_path"
done

mapfile -t disk_paths < <(
    lsblk -dn -o NAME,TYPE 2>/dev/null \
        | awk '$2 == "disk" {print "/dev/"$1}' \
        | sort -u
)
print_list_section "disk_paths" "${disk_paths[@]}"

for disk in "${disk_paths[@]}"; do
    safe="$(sanitize_name "$disk")"
    run_section "lsblk_disk_${safe}" lsblk -d -o NAME,KNAME,TYPE,SIZE,MODEL,SERIAL,WWN,ROTA,TRAN,STATE "$disk"
    run_optional_section "udevadm_props_${safe}" udevadm info --query=property --name="$disk"
    run_optional_section "smartctl_x_${safe}" smartctl -x "$disk"
done

run_section "dmesg_lvm_raid" bash -lc "dmesg -T | grep -Ei '$LOG_RE' | tail -n 500"
if command_exists journalctl; then
    run_section "journalctl_kernel_lvm_raid" bash -lc "journalctl -k -b --no-pager | grep -Ei '$LOG_RE' | tail -n 500"
else
    echo "===== BEGIN journalctl_kernel_lvm_raid ====="
    echo "# command not found: journalctl"
    echo "# rc=127"
    echo "===== END journalctl_kernel_lvm_raid ====="
    echo
fi
