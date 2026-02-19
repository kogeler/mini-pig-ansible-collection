#!/usr/bin/env python3

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Collect LVM-RAID health and dm-cache statistics
and print them in Influx line-protocol for Telegraf [[inputs.exec]].

✓ Tested on Debian 12, lvm2 ≥ 2.03, kernels with metadata2/writeback caches.
"""

import json
import re
import shlex
import subprocess
import sys
import time

# Helper to run shell commands
run = lambda cmd: subprocess.check_output(shlex.split(cmd), text=True)

# Map textual health to numeric levels for easy alerting
HEALTH = {
    "": 0,
    "ok": 0,
    "clean": 0,
    "partial": 1,
    "degraded": 2,
    "resync": 3,
    "recover": 3,
    "mismatch": 4,
    "suspended": 5,
}


def parse_cache_status(devpath: str):
    """
    Parse `dmsetup status <devpath>` for a dm-cache device and return
    (total_cache_blocks, dirty_cache_blocks).

    Field layout (kernel doc):
      cache <md_blk_sz> <used_md/total_md>
            <cache_blk_sz> <used_cache/total_cache>
            <read_hit> <read_miss> <write_hit> <write_miss>
            <demotions> <promotions> <dirty> <#features> ...
                       ↑ offset +4                 ↑ offset +11
    """
    tokens = run(f"dmsetup status {devpath}").split()

    try:
        idx = tokens.index("cache")
    except ValueError as exc:
        raise RuntimeError("'cache' target not found") from exc

    # Used/total cache blocks is the token at +4 from 'cache'
    try:
        used_total = tokens[idx + 4]  # e.g. 819198/819200
        dirty = int(tokens[idx + 11])  # e.g. 6728
    except (IndexError, ValueError) as exc:
        raise RuntimeError("unexpected dmsetup output") from exc

    m = re.match(r"\d+/(\d+)", used_total)
    if not m:
        raise RuntimeError(f"cannot parse cache size '{used_total}'")
    total = int(m.group(1))

    return total, dirty


def main() -> None:
    # Query LVM in JSON format, including segment info
    lvs_json = json.loads(
        run(
            "lvs -a --segments "
            "-o vg_name,lv_name,lv_path,segtype,lv_active,"
            "lv_health_status,sync_percent,copy_percent "
            "--reportformat json"
        )
    )

    timestamp = int(time.time() * 1e9)  # nanoseconds for Influx
    lines = []
    raid_health = {}
    raid_active = {}
    raid_sync = {}
    cache_stats = {}

    for rpt in lvs_json["report"]:
        # Support all possible array names: lv / lv_segments / seg
        segs = rpt.get("lv", []) + rpt.get("lv_segments", []) + rpt.get("seg", [])
        for lv in segs:
            vg = lv["vg_name"]
            name = lv["lv_name"]
            segtype = lv.get("segtype")
            path = lv.get("lv_path") or f"/dev/{vg}/{name}"
            lv_active = (lv.get("lv_active") or "").strip().lower()

            # --- RAID metrics ---
            if segtype and segtype.startswith("raid"):
                health_status = (lv.get("lv_health_status") or "").strip().lower()
                h = HEALTH.get(health_status, 6)
                key = (vg, name)
                active_value = 1 if lv_active == "active" else 0
                if key in raid_health:
                    raid_health[key] = max(raid_health[key], h)
                else:
                    raid_health[key] = h
                if key in raid_active:
                    raid_active[key] = max(raid_active[key], active_value)
                else:
                    raid_active[key] = active_value

                sync_raw = (
                    (lv.get("sync_percent") or lv.get("copy_percent") or "")
                    .strip()
                    .rstrip("%")
                )
                if sync_raw:
                    try:
                        sync_value = float(sync_raw)
                    except ValueError:
                        print(
                            f"# WARN lvs sync_percent parse failed for {vg}/{name}: '{sync_raw}'",
                            file=sys.stderr,
                        )
                    else:
                        if key in raid_sync:
                            raid_sync[key] = min(raid_sync[key], sync_value)
                        else:
                            raid_sync[key] = sync_value

            # --- dm-cache metrics ---
            if segtype == "cache" and path.startswith("/dev"):
                try:
                    total, dirty = parse_cache_status(path)
                    ratio = 100.0 * dirty / total if total else 0.0
                    cache_stats[(vg, name)] = (dirty, total, ratio)
                except Exception as err:
                    print(f"# WARN dmsetup {path}: {err}", file=sys.stderr)

    for (vg, name), health in sorted(raid_health.items()):
        lines.append(f"lvm_raid_health,vg={vg},lv={name} value={health} {timestamp}")
        lines.append(
            f"lvm_raid_active,vg={vg},lv={name} value={raid_active[(vg, name)]} {timestamp}"
        )

    for (vg, name), sync_value in sorted(raid_sync.items()):
        lines.append(
            f"lvm_raid_sync_percent,vg={vg},lv={name} value={sync_value} {timestamp}"
        )

    for (vg, name), (dirty, total, ratio) in sorted(cache_stats.items()):
        lines += [
            f"lvm_cache_dirty_blocks,vg={vg},lv={name} value={dirty} {timestamp}",
            f"lvm_cache_total_blocks,vg={vg},lv={name} value={total} {timestamp}",
            f"lvm_cache_dirty_ratio,vg={vg},lv={name} value={ratio:.2f} {timestamp}",
        ]

    # Emit all collected metrics
    print("\n".join(lines))


if __name__ == "__main__":
    main()
