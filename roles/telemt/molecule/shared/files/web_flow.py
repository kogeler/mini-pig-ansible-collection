#!/usr/bin/env python3
"""Exercise the public Telemt WEB carrier without exposing bearer tokens."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import re
import ssl


FRAME_OPEN = 0x01
FRAME_DATA = 0x02
FRAME_CLOSE = 0x03
FRAME_PONG = 0x06


def request(
    connection: http.client.HTTPSConnection,
    method: str,
    path: str,
    host: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request_headers = {"Host": host, "Cookie": ""}
    request_headers.update(headers or {})
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    return response.status, response_headers, response.read()


def frame(kind: int, stream_id: int, payload: bytes = b"") -> bytes:
    return bytes(
        [kind, (stream_id >> 16) & 0xFF, (stream_id >> 8) & 0xFF, stream_id & 0xFF]
    ) + len(payload).to_bytes(4, "big") + payload


def parse_frames(batch: bytes) -> list[tuple[int, int, bytes]]:
    frames: list[tuple[int, int, bytes]] = []
    offset = 0
    while offset < len(batch):
        assert len(batch) - offset >= 8, "truncated WEB frame header"
        kind = batch[offset]
        stream_id = int.from_bytes(batch[offset + 1 : offset + 4], "big")
        payload_size = int.from_bytes(batch[offset + 4 : offset + 8], "big")
        end = offset + 8 + payload_size
        assert end <= len(batch), "truncated WEB frame payload"
        frames.append((kind, stream_id, batch[offset + 8 : end]))
        offset = end
    return frames


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--secret-mode", choices=("plain", "dd"), required=True)
    parser.add_argument("--carrier", choices=("https", "https-lanes"), required=True)
    parser.add_argument("--decoy-marker", required=True)
    args = parser.parse_args()

    secret = bytes.fromhex(args.secret)
    if args.secret_mode == "dd":
        secret = b"\xdd" + secret
    capability = base64.urlsafe_b64encode(
        hmac.new(
            secret,
            b"tdesktop-web-proxy-bridge-v1\n" + args.host.encode("ascii"),
            hashlib.sha256,
        ).digest()
    ).rstrip(b"=").decode("ascii")

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["http/1.1"])
    connection = http.client.HTTPSConnection("127.0.0.1", 443, context=context, timeout=15)

    status, headers, bridge = request(
        connection, "GET", f"/?bridge={capability}", args.host
    )
    assert status == 200, f"bridge status={status}"
    bridge_text = bridge.decode("utf-8")
    assert "bootstrap='" in bridge_text
    assert f"carrier='{args.carrier}'" in bridge_text
    assert capability not in bridge_text
    assert "no-store" in headers.get("cache-control", "")
    bootstrap_match = re.search(r"bootstrap='([A-Za-z0-9_-]{43})'", bridge_text)
    assert bootstrap_match, "bootstrap token is missing"
    bootstrap = bootstrap_match.group(1)

    wrong_capability = "A" * 43
    status, headers, decoy = request(
        connection, "GET", f"/?bridge={wrong_capability}", args.host
    )
    assert status == 200
    assert args.decoy_marker.encode() in decoy
    assert "bootstrap='" not in decoy.decode("utf-8", errors="replace")
    assert "x-session-token" not in headers

    status, headers, malformed = request(
        connection, "GET", f"/?bridge={capability}&probe=1", args.host
    )
    assert status == 200
    assert args.decoy_marker.encode() in malformed
    assert "x-session-token" not in headers

    hello = frame(0x10, 0, b"\x01")
    status, headers, welcome = request(
        connection,
        "POST",
        "/api/v1/session",
        args.host,
        body=hello,
        headers={
            "Authorization": f"Bearer {bootstrap}",
            "Content-Type": "application/octet-stream",
        },
    )
    assert status == 200, f"session status={status}"
    assert welcome == frame(0x11, 0)
    assert headers.get("x-carrier-mode") == args.carrier
    session = headers.get("x-session-token", "")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", session), "invalid session token"

    carrier_headers = {
        "Authorization": f"Bearer {session}",
        "Content-Type": "application/octet-stream",
        "X-Up-Seq": "1",
    }
    if args.carrier == "https-lanes":
        carrier_headers["X-Lane-ID"] = "0"
    status, headers, body = request(
        connection,
        "POST",
        "/api/v1/up",
        args.host,
        body=frame(FRAME_PONG, 0),
        headers=carrier_headers,
    )
    assert status == 204, f"uplink status={status}"
    assert headers.get("x-up-ack") == "1"
    assert not body

    down_headers = {
        "Authorization": f"Bearer {session}",
        "X-Down-Cursor": "0",
    }
    if args.carrier == "https-lanes":
        down_headers["X-Lane-ID"] = "0"
    status, headers, body = request(
        connection, "POST", "/api/v1/down", args.host, headers=down_headers
    )
    assert status == 204, f"downlink status={status}"
    assert headers.get("x-down-cursor") == "0"
    assert not body

    # Exercise the real logical-stream relay boundary. Each stream receives a
    # complete but invalid 64-byte inner MTProxy handshake. Telemt must admit
    # both WEB streams, reject the inner handshakes, and return CLOSE frames;
    # invalid inner users never fall through to the HTTP decoy.
    stream_ids = (1, 2)
    invalid_handshake = bytes(64)
    if args.carrier == "https":
        stream_batch = b"".join(
            frame(FRAME_OPEN, stream_id)
            + frame(FRAME_DATA, stream_id, invalid_handshake)
            for stream_id in stream_ids
        )
        status, headers, body = request(
            connection,
            "POST",
            "/api/v1/up",
            args.host,
            body=stream_batch,
            headers={
                "Authorization": f"Bearer {session}",
                "Content-Type": "application/octet-stream",
                "X-Up-Seq": "2",
            },
        )
        assert status == 204, f"stream uplink status={status}"
        assert headers.get("x-up-ack") == "2"
    else:
        for stream_id in stream_ids:
            status, headers, body = request(
                connection,
                "POST",
                "/api/v1/up",
                args.host,
                body=(
                    frame(FRAME_OPEN, stream_id)
                    + frame(FRAME_DATA, stream_id, invalid_handshake)
                ),
                headers={
                    "Authorization": f"Bearer {session}",
                    "Content-Type": "application/octet-stream",
                    "X-Up-Seq": "1",
                    "X-Lane-ID": str(stream_id),
                },
            )
            assert status == 204, f"lane {stream_id} uplink status={status}"
            assert headers.get("x-up-ack") == "1"

    closed_streams: set[int] = set()
    if args.carrier == "https":
        cursor = "0"
        for _ in range(8):
            status, headers, body = request(
                connection,
                "POST",
                "/api/v1/down",
                args.host,
                headers={
                    "Authorization": f"Bearer {session}",
                    "X-Down-Cursor": cursor,
                },
            )
            assert status in (200, 204), f"stream downlink status={status}"
            cursor = headers.get("x-down-cursor", cursor)
            if status == 200:
                closed_streams.update(
                    stream_id
                    for kind, stream_id, payload in parse_frames(body)
                    if kind == FRAME_CLOSE and not payload
                )
            if closed_streams == set(stream_ids):
                break
    else:
        for stream_id in stream_ids:
            cursor = "0"
            for _ in range(8):
                status, headers, body = request(
                    connection,
                    "POST",
                    "/api/v1/down",
                    args.host,
                    headers={
                        "Authorization": f"Bearer {session}",
                        "X-Down-Cursor": cursor,
                        "X-Lane-ID": str(stream_id),
                    },
                )
                assert status in (200, 204), (
                    f"lane {stream_id} downlink status={status}"
                )
                cursor = headers.get("x-down-cursor", cursor)
                if status == 200:
                    closed_streams.update(
                        frame_stream_id
                        for kind, frame_stream_id, payload in parse_frames(body)
                        if kind == FRAME_CLOSE and not payload
                    )
                if stream_id in closed_streams:
                    break
    assert closed_streams == set(stream_ids), (
        f"missing logical-stream CLOSE frames: {closed_streams}"
    )

    status, headers, body = request(
        connection,
        "DELETE",
        "/api/v1/session",
        args.host,
        headers={"Authorization": f"Bearer {session}"},
    )
    assert status == 204, f"delete status={status}"
    assert "x-session-token" not in headers
    assert not body

    print(
        "WEB flow passed: "
        f"carrier={args.carrier}, mode={args.secret_mode}, streams={len(stream_ids)}"
    )


if __name__ == "__main__":
    main()
