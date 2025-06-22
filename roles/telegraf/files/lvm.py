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
    "": 0, "ok": 0, "clean": 0,
    "partial": 1,
    "degraded": 2,
    "resync": 3, "recover": 3,
    "mismatch": 4,
    "suspended": 5
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
        used_total = tokens[idx + 4]                 # e.g. 819198/819200
        dirty      = int(tokens[idx + 11])           # e.g. 6728
    except (IndexError, ValueError) as exc:
        raise RuntimeError("unexpected dmsetup output") from exc

    m = re.match(r"\d+/(\d+)", used_total)
    if not m:
        raise RuntimeError(f"cannot parse cache size '{used_total}'")
    total = int(m.group(1))

    return total, dirty

def main() -> None:
    # Query LVM in JSON format, including segment info
    lvs_json = json.loads(run(
        "lvs -a --segments "
        "-o vg_name,lv_name,lv_path,segtype,lv_role,"
        "lv_health_status,sync_percent,copy_percent "
        "--reportformat json"
    ))

    timestamp = int(time.time() * 1e9)  # nanoseconds for Influx
    lines = []

    for rpt in lvs_json["report"]:
        # Support all possible array names: lv / lv_segments / seg
        segs = rpt.get("lv", []) + rpt.get("lv_segments", []) + rpt.get("seg", [])
        for lv in segs:
            vg   = lv["vg_name"]
            name = lv["lv_name"]
            segtype = lv.get("segtype")
            path = lv.get("lv_path") or f"/dev/{vg}/{name}"

            # --- RAID metrics ---
            if segtype and segtype.startswith("raid"):
                h = HEALTH.get(lv.get("lv_health_status", "").lower(), 6)
                lines.append(f"lvm_raid_health,vg={vg},lv={name} value={h} {timestamp}")

                sync = lv.get("sync_percent") or lv.get("copy_percent")
                if sync:
                    lines.append(
                        f"lvm_raid_sync_percent,vg={vg},lv={name} "
                        f"value={float(sync)} {timestamp}"
                    )

            # --- dm-cache metrics ---
            if segtype == "cache" and path.startswith("/dev"):
                try:
                    total, dirty = parse_cache_status(path)
                    ratio = 100.0 * dirty / total if total else 0.0
                    lines += [
                        f"lvm_cache_dirty_blocks,vg={vg},lv={name} "
                        f"value={dirty} {timestamp}",
                        f"lvm_cache_total_blocks,vg={vg},lv={name} "
                        f"value={total} {timestamp}",
                        f"lvm_cache_dirty_ratio,vg={vg},lv={name} "
                        f"value={ratio:.2f} {timestamp}",
                    ]
                except Exception as err:
                    print(f"# WARN dmsetup {path}: {err}", file=sys.stderr)

    # Emit all collected metrics
    print("\n".join(lines))

if __name__ == "__main__":
    main()
