#!/usr/bin/env python3
"""Compare naive_proxy binary/image pins with authoritative upstreams."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError
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


def url_exists(url: str) -> bool:
    request = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status == 200
    except HTTPError as error:
        if error.code == 404:
            return False
        raise


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


def docker_hub_tags(repository: str) -> list[dict[str, Any]]:
    payload = fetch_json(
        f"https://hub.docker.com/v2/repositories/{repository}/tags"
        "?page_size=100&ordering=last_updated"
    )
    return payload["results"]


def newest_tag(tags: list[dict[str, Any]], pattern: str) -> str:
    """Return the highest version-sorted tag matching `pattern` (numeric groups)."""
    regex = re.compile(pattern)
    candidates: list[tuple[tuple[int, ...], str]] = []
    for entry in tags:
        match = regex.fullmatch(entry["name"])
        if match:
            candidates.append((tuple(map(int, match.groups())), entry["name"]))
    if not candidates:
        raise RuntimeError(f"no tags match {pattern}")
    return max(candidates)[1]


def numeric_image_tag(repository: str, suffix: str = "") -> str:
    """Newest explicit `X.Y.Z<suffix>` patch tag."""
    return newest_tag(docker_hub_tags(repository), rf"(\d+)\.(\d+)\.(\d+){re.escape(suffix)}")


def minor_line_tag(repository: str, suffix: str = "") -> tuple[str, str]:
    """Newest stable `X.Y<suffix>` minor-line tag and the patch tag it currently resolves to."""
    tags = docker_hub_tags(repository)
    line = newest_tag(tags, rf"(\d+)\.(\d+){re.escape(suffix)}")
    digests = {entry["name"]: entry.get("digest") for entry in tags}
    patch_pattern = re.compile(rf"{re.escape(line.removesuffix(suffix))}\.\d+{re.escape(suffix)}")
    resolved = next(
        (
            name
            for name, digest in digests.items()
            if patch_pattern.fullmatch(name) and digest and digest == digests.get(line)
        ),
        "?",
    )
    return line, resolved


def go_string_list(source: str, statement: str) -> list[str]:
    """Collect quoted strings from every uncommented `<statement>(...)` call."""
    values: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or statement not in stripped:
            continue
        values.extend(re.findall(r'"([^"]+)"', stripped.split(statement, 1)[1]))
    return values


def android_ci_go_version(workflow: str) -> str:
    """Go version of the job that builds SFA's libbox.aar."""
    jobs = yaml.safe_load(workflow)["jobs"]
    for step in jobs["build_android_library"]["steps"]:
        version = (step.get("with") or {}).get("go-version")
        if version:
            return str(version)
    raise RuntimeError("build_android_library has no setup-go go-version")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when any local pin is stale")
    args = parser.parse_args()

    defaults = yaml_file(ROLE_DIR / "defaults/main.yml")
    internal_vars = yaml_file(ROLE_DIR / "vars/main.yml")
    benchmark = yaml_file(MOLECULE_DIR / "shared/vars/benchmark.yml")
    base = yaml_file(MOLECULE_DIR / "shared/base.yml")
    build_vars = base["provisioner"]["inventory"]["group_vars"]["all"]

    naive = latest_release("klzgrad/naiveproxy")["tag_name"]
    sing_release = latest_release("SagerNet/sing-box")
    sing_stable = sing_release["tag_name"]
    pebble = latest_release("letsencrypt/pebble")["tag_name"].removeprefix("v")
    haproxy, haproxy_resolved = minor_line_tag("library/haproxy", "-alpine")
    caddy, caddy_resolved = minor_line_tag("library/caddy", "-alpine")
    acme = numeric_image_tag("neilpang/acme.sh")
    busybox = numeric_image_tag("library/busybox")
    iperf = fetch_json("https://hub.docker.com/v2/repositories/networkstatic/iperf3/tags/latest")
    iperf_ref = f"docker.io/networkstatic/iperf3@{iperf['digest']}"

    # Released SFA: main/version.properties plus the APK in the stable release.
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

    # The libbox.aar inside that APK is built by sing-box CI at the same tag.
    sing_raw = "https://raw.githubusercontent.com/SagerNet/sing-box/" + sfa_version
    ci_go_version = android_ci_go_version(fetch_text(f"{sing_raw}/.github/workflows/build.yml"))
    if ci_go_version != sfa_go_version:
        raise RuntimeError(
            f"SFA GO_VERSION={sfa_go_version} but sing-box {sfa_version} "
            f"build_android_library uses Go {ci_go_version}; review the tuple manually"
        )
    android_tags = go_string_list(
        fetch_text(f"{sing_raw}/cmd/internal/build_libbox/main.go"),
        "sharedTags = append(sharedTags,",
    )
    if "with_naive_outbound" not in android_tags:
        raise RuntimeError("build_libbox sharedTags no longer contain with_naive_outbound")
    expected_tags = android_tags + ["with_purego"]

    ldflags = fetch_text(f"{sing_raw}/release/LDFLAGS").strip()
    linker_source = fetch_text(f"{sing_raw}/cmd/internal/build_shared/flags.go")
    if any(token not in linker_source for token in ldflags.split()):
        raise RuntimeError(
            "release/LDFLAGS and build_shared.LinkerFlags (libbox) disagree; review manually"
        )

    cronet_commit = fetch_text(f"{sing_raw}/.github/CRONET_GO_VERSION").strip()
    go_mod = fetch_text(f"{sing_raw}/go.mod")
    cronet_pin = re.search(r"github\.com/sagernet/cronet-go v[^\s]+-([0-9a-f]+)", go_mod)
    if cronet_pin is None or not cronet_commit.startswith(cronet_pin.group(1)):
        raise RuntimeError("sing-box go.mod and CRONET_GO_VERSION disagree")
    local_cronet = build_vars["singbox_cronet_commit"]
    cronet_library = (
        f"https://raw.githubusercontent.com/sagernet/cronet-go/{local_cronet}/lib/linux_amd64/libcronet.so"
    )
    if not url_exists(cronet_library):
        raise RuntimeError(f"pinned cronet-go commit has no Linux library: {cronet_library}")

    checks = [
        ("Naive backend", defaults["naive_proxy_naive_version"], naive),
        ("sing-box server", defaults["naive_proxy_singbox_image_tag"], sing_stable),
        ("sing-box stress", build_vars["singbox_build_version"], sfa_version),
        ("stress Go", build_vars["singbox_build_go_version"], sfa_go_version),
        ("stress ldflags", build_vars["singbox_build_ldflags"], ldflags),
        ("stress cronet", local_cronet, cronet_commit),
        ("HAProxy line", defaults["naive_proxy_haproxy_image_tag"], haproxy),
        ("Caddy line", defaults["naive_proxy_decoy_image_tag"], caddy),
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
        f"local={','.join(build_vars['singbox_build_tags'])}  "
        f"upstream(build_libbox)+purego={','.join(expected_tags)}"
    )
    print(
        "Released SFA source"
        f"  VERSION_NAME={sfa['VERSION_NAME']}  GO_VERSION={sfa['GO_VERSION']}"
        f"  asset={sfa_asset}  android-ci-go={ci_go_version}"
    )
    print(
        "Minor lines currently resolve to"
        f"  HAProxy={haproxy_resolved}  Caddy={caddy_resolved}"
    )
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CLI should collapse network/schema errors.
        print(f"version audit failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
