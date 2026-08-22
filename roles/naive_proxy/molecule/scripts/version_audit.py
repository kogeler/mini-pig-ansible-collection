#!/usr/bin/env python3
"""Compare naive_proxy binary/image pins with authoritative upstreams."""

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
MOLECULE_DIR = ROLE_DIR / "molecule"
USER_AGENT = "mini-pig-naive-proxy-version-audit"


def fetch(url: str) -> bytes:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return response.read()


def fetch_json(url: str) -> Any:
    return json.loads(fetch(url))


def fetch_text(url: str) -> str:
    return fetch(url).decode("utf-8")


def github_releases(repository: str) -> list[dict[str, Any]]:
    return fetch_json(f"https://api.github.com/repos/{repository}/releases?per_page=100")


def latest_release(repository: str, prerelease: bool = False) -> dict[str, Any]:
    return next(
        release
        for release in github_releases(repository)
        if not release["draft"] and release["prerelease"] is prerelease
    )


def yaml_file(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when any local pin is stale")
    args = parser.parse_args()

    defaults = yaml_file(ROLE_DIR / "defaults/main.yml")
    internal_vars = yaml_file(ROLE_DIR / "vars/main.yml")
    common = yaml_file(MOLECULE_DIR / "shared/vars/common.yml")
    benchmark = yaml_file(MOLECULE_DIR / "shared/vars/benchmark.yml")
    base = yaml_file(MOLECULE_DIR / "shared/base.yml")
    build_vars = base["provisioner"]["inventory"]["group_vars"]["all"]

    naive = latest_release("klzgrad/naiveproxy")["tag_name"]
    sing_stable = latest_release("SagerNet/sing-box")["tag_name"]
    sing_release = latest_release("SagerNet/sing-box")
    caddy = latest_release("caddyserver/caddy")["tag_name"].removeprefix("v") + "-alpine"
    acme = latest_release("acmesh-official/acme.sh")["tag_name"].removeprefix("v")
    pebble = latest_release("letsencrypt/pebble")["tag_name"].removeprefix("v")
    cronet = latest_release("sagernet/cronet-go")["tag_name"]
    haproxy = numeric_image_tag("library/haproxy", "-alpine")
    busybox = numeric_image_tag("library/busybox")
    iperf = fetch_json("https://hub.docker.com/v2/repositories/networkstatic/iperf3/tags/latest")
    iperf_ref = f"docker.io/networkstatic/iperf3@{iperf['digest']}"

    sfa_properties = fetch_text(
        "https://raw.githubusercontent.com/SagerNet/sing-box-for-android/main/version.properties"
    )
    sfa = dict(line.split("=", 1) for line in sfa_properties.splitlines() if "=" in line)
    sfa_version = f"v{sfa['VERSION_NAME']}"
    sfa_go_version = sfa["GO_VERSION"].removeprefix("go")
    sfa_asset = f"SFA-{sfa['VERSION_NAME']}-universal.apk"
    release_assets = {asset["name"] for asset in sing_release["assets"]}
    if sfa_version != sing_stable or sfa_asset not in release_assets:
        raise RuntimeError(
            "SFA main/version.properties does not describe the latest released SFA APK: "
            f"VERSION_NAME={sfa['VERSION_NAME']}, sing-box release={sing_stable}, "
            f"expected asset={sfa_asset}"
        )

    sing_raw = "https://raw.githubusercontent.com/SagerNet/sing-box/" + sfa_version
    upstream_tags = fetch_text(f"{sing_raw}/release/DEFAULT_BUILD_TAGS").strip().split(",")
    expected_tags = upstream_tags + ["with_purego"]

    cronet_commit = fetch_text(f"{sing_raw}/.github/CRONET_GO_VERSION").strip()
    go_mod = fetch_text(f"{sing_raw}/go.mod")
    cronet_pin = re.search(r"github\.com/sagernet/cronet-go v[^\s]+-([0-9a-f]+)", go_mod)
    if cronet_pin is None or not cronet_commit.startswith(cronet_pin.group(1)):
        raise RuntimeError("sing-box go.mod and CRONET_GO_VERSION disagree")

    checks = [
        ("Naive backend", defaults["naive_proxy_naive_version"], naive),
        ("Naive test client", common["naive_proxy_naive_version"], naive),
        ("sing-box server", defaults["naive_proxy_singbox_image_tag"], sing_stable),
        ("sing-box stress", build_vars["singbox_build_version"], sfa_version),
        ("stress Go", build_vars["singbox_build_go_version"], sfa_go_version),
        ("stress cronet", build_vars["singbox_cronet_version"], cronet),
        ("HAProxy", defaults["naive_proxy_haproxy_image_tag"], haproxy),
        ("Caddy", defaults["naive_proxy_decoy_image_tag"], caddy),
        ("acme.sh", defaults["naive_proxy_acme_image_tag"], acme),
        ("Pebble", internal_vars["_naive_proxy_pebble_image_tag"], pebble),
        ("BusyBox helper", benchmark["iperf_helper_image"].rsplit(":", 1)[-1], busybox),
        ("iperf3 manifest", benchmark["iperf_image"], iperf_ref),
    ]

    width = max(len(label) for label, _, _ in checks)
    stale = False
    for label, local, upstream in checks:
        status = "ok" if local == upstream else "STALE"
        stale |= local != upstream
        print(f"{label:<{width}}  {status:<5}  local={local}  upstream={upstream}")

    tags_match = build_vars["singbox_build_tags"] == expected_tags
    stale |= not tags_match
    print(
        f"{'stress build tags':<{width}}  {'ok' if tags_match else 'STALE':<5}  "
        f"local={len(build_vars['singbox_build_tags'])}  upstream+purego={len(expected_tags)}"
    )
    print(
        "Released SFA source"
        f"  VERSION_NAME={sfa['VERSION_NAME']}  GO_VERSION={sfa['GO_VERSION']}"
        f"  asset={sfa_asset}"
    )
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CLI should collapse network/schema errors.
        print(f"version audit failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
