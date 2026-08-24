#!/usr/bin/env python3
"""Compare telemt runtime image pins with authoritative upstream releases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.request import Request, urlopen

import yaml


ROLE_DIR = Path(__file__).resolve().parents[2]
USER_AGENT = "mini-pig-telemt-version-audit"


def fetch_json(url: str) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def latest_release(repository: str) -> dict[str, Any]:
    releases = fetch_json(f"https://api.github.com/repos/{repository}/releases?per_page=100")
    return next(
        release
        for release in releases
        if not release["draft"] and not release["prerelease"]
    )


def numeric_image_tag(repository: str, suffix: str = "") -> str:
    payload = fetch_json(
        f"https://hub.docker.com/v2/repositories/{repository}/tags"
        "?page_size=100&ordering=last_updated"
    )
    pattern = re.compile(rf"^(\d+)\.(\d+)\.(\d+){re.escape(suffix)}$")
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for entry in payload["results"]:
        match = pattern.fullmatch(entry["name"])
        if match:
            candidates.append((tuple(map(int, match.groups())), entry["name"]))
    if not candidates:
        raise RuntimeError(f"no numeric tags found for {repository}")
    return max(candidates)[1]


def yaml_file(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when a local pin is stale")
    args = parser.parse_args()

    defaults = yaml_file(ROLE_DIR / "defaults/main.yml")
    internal_vars = yaml_file(ROLE_DIR / "vars/main.yml")

    checks = [
        ("Telemt", defaults["telemt_image_tag"], latest_release("telemt/telemt")["tag_name"]),
        (
            "HAProxy",
            defaults["telemt_haproxy_image_tag"],
            numeric_image_tag("library/haproxy", "-alpine"),
        ),
        (
            "Caddy",
            defaults["telemt_decoy_image_tag"],
            latest_release("caddyserver/caddy")["tag_name"].removeprefix("v") + "-alpine",
        ),
        (
            "acme.sh",
            defaults["telemt_acme_image_tag"],
            latest_release("acmesh-official/acme.sh")["tag_name"].removeprefix("v"),
        ),
        (
            "Pebble",
            internal_vars["_telemt_pebble_image_tag"],
            latest_release("letsencrypt/pebble")["tag_name"].removeprefix("v"),
        ),
    ]

    width = max(len(label) for label, _, _ in checks)
    stale = False
    for label, local, upstream in checks:
        status = "ok" if local == upstream else "STALE"
        stale |= local != upstream
        print(f"{label:<{width}}  {status:<5}  local={local}  upstream={upstream}")
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - collapse network/schema failures for CLI use.
        print(f"version audit failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
