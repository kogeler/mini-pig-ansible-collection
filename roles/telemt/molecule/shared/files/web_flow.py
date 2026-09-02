#!/usr/bin/env python3
"""Exercise Telemt WEB through HAProxy and round-trip req_pq to Telegram."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import sys
import time

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

FRAME_OPEN = 0x01
FRAME_DATA = 0x02
FRAME_CLOSE = 0x03
FRAME_WINDOW = 0x04
FRAME_PING = 0x05
FRAME_PONG = 0x06

REQ_PQ_CONSTRUCTOR = 0x60469778
RES_PQ_CONSTRUCTOR = 0x05162463
TELEGRAM_DC_ID = 1
MAX_MTPROTO_PACKET = 1024 * 1024
TELEGRAM_ROUND_TRIP_SECS = 30
STREAM_CLOSE_SECS = 12

RESERVED_HEADER_PREFIXES = {
    b"HEAD",
    b"POST",
    b"GET ",
    b"\xee\xee\xee\xee",
    b"\xdd\xdd\xdd\xdd",
    b"\x16\x03\x01\x02",
}


def request(
    connection: httpx.Client,
    method: str,
    path: str,
    host: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request_headers = {"Host": host, "Cookie": ""}
    request_headers.update(headers or {})
    response = connection.request(
        method,
        path,
        content=body,
        headers=request_headers,
    )
    assert response.http_version == "HTTP/2", (
        f"public WEB request negotiated {response.http_version}, expected HTTP/2"
    )
    response_headers = {key.lower(): value for key, value in response.headers.items()}
    return response.status_code, response_headers, response.content


def frame(kind: int, stream_id: int, payload: bytes = b"") -> bytes:
    return (
        bytes(
            [kind, (stream_id >> 16) & 0xFF, (stream_id >> 8) & 0xFF, stream_id & 0xFF]
        )
        + len(payload).to_bytes(4, "big")
        + payload
    )


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


class MtprotoWireProbe:
    """Minimal client-side MTProxy obfuscated2 and packet framing."""

    def __init__(self, secret: bytes, secret_mode: str, dc_id: int) -> None:
        assert len(secret) == 16, "MTProxy secret must be 16 bytes"
        self._secret_mode = secret_mode
        self._decrypted = bytearray()
        self._nonce = os.urandom(16)

        protocol = b"\xef" * 4 if secret_mode == "plain" else b"\xdd" * 4
        raw = self._new_handshake(protocol, dc_id)

        outgoing_key = hashlib.sha256(raw[8:40] + secret).digest()
        outgoing_iv = raw[40:56]
        reversed_key_iv = raw[8:56][::-1]
        incoming_key = hashlib.sha256(reversed_key_iv[:32] + secret).digest()
        incoming_iv = reversed_key_iv[32:48]

        self._encryptor = Cipher(
            algorithms.AES(outgoing_key), modes.CTR(outgoing_iv)
        ).encryptor()
        self._decryptor = Cipher(
            algorithms.AES(incoming_key), modes.CTR(incoming_iv)
        ).decryptor()

        # Encrypting all 64 bytes advances the client-to-proxy CTR stream. Only
        # its final eight bytes replace the corresponding clear handshake bytes.
        encrypted_handshake = self._encryptor.update(raw)
        self.handshake = raw[:56] + encrypted_handshake[56:64]

    @staticmethod
    def _new_handshake(protocol: bytes, dc_id: int) -> bytes:
        while True:
            raw = bytearray(os.urandom(64))
            if raw[0] == 0xEF:
                continue
            if bytes(raw[:4]) in RESERVED_HEADER_PREFIXES:
                continue
            if raw[4:8] == bytes(4):
                continue
            raw[56:60] = protocol
            raw[60:62] = struct.pack("<h", dc_id)
            return bytes(raw)

    @staticmethod
    def _message_id() -> int:
        seconds, nanoseconds = divmod(time.time_ns(), 1_000_000_000)
        fraction = (nanoseconds << 32) // 1_000_000_000
        return ((seconds << 32) | fraction) & ~0x03

    def request_bytes(self) -> bytes:
        request_payload = struct.pack("<I", REQ_PQ_CONSTRUCTOR) + self._nonce
        padding_size = 4 * (1 + secrets.randbelow(32))
        message_payload = request_payload + os.urandom(padding_size)
        message = (
            bytes(8)
            + struct.pack("<Q", self._message_id())
            + struct.pack("<I", len(message_payload))
            + message_payload
        )
        assert len(message) % 4 == 0

        if self._secret_mode == "plain":
            word_count = len(message) // 4
            if word_count < 127:
                transport = bytes([word_count]) + message
            else:
                transport = b"\x7f" + word_count.to_bytes(3, "little") + message
        else:
            padding = os.urandom(1 + secrets.randbelow(3))
            body = message + padding
            transport = struct.pack("<I", len(body)) + body
        return self._encryptor.update(transport)

    def feed(self, encrypted: bytes) -> bool:
        self._decrypted.extend(self._decryptor.update(encrypted))
        while True:
            message = self._pop_message()
            if message is None:
                return False
            if self._is_matching_res_pq(message):
                return True

    def _pop_message(self) -> bytes | None:
        if self._secret_mode == "plain":
            return self._pop_abridged_message()
        return self._pop_secure_message()

    def _pop_abridged_message(self) -> bytes | None:
        if not self._decrypted:
            return None
        first = self._decrypted[0]
        if first in (0x7F, 0xFF):
            if len(self._decrypted) < 4:
                return None
            header_size = 4
            word_count = int.from_bytes(self._decrypted[1:4], "little")
        elif first < 0x7F:
            header_size = 1
            word_count = first
        else:
            header_size = 1
            word_count = first - 0x80
        message_size = word_count * 4
        assert (
            0 < message_size <= MAX_MTPROTO_PACKET
        ), f"invalid abridged MTProto packet size={message_size}"
        end = header_size + message_size
        if len(self._decrypted) < end:
            return None
        message = bytes(self._decrypted[header_size:end])
        del self._decrypted[:end]
        return message

    def _pop_secure_message(self) -> bytes | None:
        if len(self._decrypted) < 4:
            return None
        wire_size = int.from_bytes(self._decrypted[:4], "little") & 0x7FFFFFFF
        assert (
            4 <= wire_size <= MAX_MTPROTO_PACKET
        ), f"invalid secure MTProto packet size={wire_size}"
        end = 4 + wire_size
        if len(self._decrypted) < end:
            return None
        padding_size = wire_size % 4
        message_end = end - padding_size
        message = bytes(self._decrypted[4:message_end])
        del self._decrypted[:end]
        return message

    def _is_matching_res_pq(self, message: bytes) -> bool:
        assert len(message) >= 20, "truncated unencrypted MTProto response"
        assert message[:8] == bytes(8), "unexpected encrypted MTProto response"
        message_id = int.from_bytes(message[8:16], "little")
        assert message_id % 4 == 1, "MTProto response has an invalid message id"
        payload_size = int.from_bytes(message[16:20], "little")
        assert (
            payload_size <= len(message) - 20
        ), "truncated unencrypted MTProto response payload"
        payload = message[20 : 20 + payload_size]
        assert len(payload) >= 20, "truncated resPQ payload"
        constructor = int.from_bytes(payload[:4], "little")
        assert (
            constructor == RES_PQ_CONSTRUCTOR
        ), f"unexpected MTProto constructor=0x{constructor:08x}"
        assert payload[4:20] == self._nonce, "resPQ nonce does not match req_pq"
        return True


class WebCarrier:
    """Sequence and cursor state for one WEB session."""

    def __init__(
        self,
        connection: httpx.Client,
        host: str,
        carrier: str,
        session: str,
    ) -> None:
        self._connection = connection
        self._host = host
        self._carrier = carrier
        self._session = session
        self._up_sequences: dict[int, int] = {}
        self._down_cursors: dict[int, str] = {}

    def _state_key(self, lane_id: int) -> int:
        return 0 if self._carrier == "https" else lane_id

    def uplink(self, lane_id: int, batch: bytes, label: str) -> None:
        key = self._state_key(lane_id)
        sequence = self._up_sequences.get(key, 0) + 1
        headers = {
            "Authorization": f"Bearer {self._session}",
            "Content-Type": "application/octet-stream",
            "X-Up-Seq": str(sequence),
        }
        if self._carrier == "https-lanes":
            headers["X-Lane-ID"] = str(lane_id)
        status, response_headers, body = request(
            self._connection,
            "POST",
            "/api/v1/up",
            self._host,
            body=batch,
            headers=headers,
        )
        assert status == 204, f"{label} uplink status={status}"
        assert response_headers.get("x-up-ack") == str(
            sequence
        ), f"{label} uplink acknowledgement mismatch"
        assert not body, f"{label} uplink returned a body"
        self._up_sequences[key] = sequence

    def downlink(
        self, lane_id: int, label: str
    ) -> tuple[int, list[tuple[int, int, bytes]]]:
        key = self._state_key(lane_id)
        cursor = self._down_cursors.get(key, "0")
        headers = {
            "Authorization": f"Bearer {self._session}",
            "X-Down-Cursor": cursor,
        }
        if self._carrier == "https-lanes":
            headers["X-Lane-ID"] = str(lane_id)
        status, response_headers, body = request(
            self._connection,
            "POST",
            "/api/v1/down",
            self._host,
            headers=headers,
        )
        assert status in (200, 204), f"{label} downlink status={status}"
        next_cursor = response_headers.get("x-down-cursor")
        assert (
            next_cursor is not None and next_cursor.isdigit()
        ), f"{label} downlink cursor is missing"
        self._down_cursors[key] = next_cursor
        if status == 204:
            assert not body, f"{label} empty downlink returned a body"
            return status, []
        assert body, f"{label} non-empty downlink returned no frames"
        return status, parse_frames(body)

    def answer_pings(self, frames: list[tuple[int, int, bytes]]) -> None:
        pongs = [
            frame(FRAME_PONG, 0, payload)
            for kind, stream_id, payload in frames
            if kind == FRAME_PING and stream_id == 0
        ]
        if pongs:
            self.uplink(0, b"".join(pongs), "PING response")


def telegram_round_trip(
    carrier: WebCarrier,
    secret: bytes,
    secret_mode: str,
    stream_id: int,
) -> None:
    probe = MtprotoWireProbe(secret, secret_mode, TELEGRAM_DC_ID)
    carrier.uplink(
        stream_id,
        frame(FRAME_OPEN, stream_id)
        + frame(FRAME_DATA, stream_id, probe.handshake + probe.request_bytes()),
        "Telegram req_pq",
    )

    received_bytes = 0
    deadline = time.monotonic() + TELEGRAM_ROUND_TRIP_SECS
    while time.monotonic() < deadline:
        _, frames = carrier.downlink(stream_id, "Telegram resPQ")
        carrier.answer_pings(frames)
        matched = False
        closed = False
        for kind, frame_stream_id, payload in frames:
            if frame_stream_id != stream_id:
                continue
            if kind == FRAME_DATA:
                received_bytes += len(payload)
                if not matched and probe.feed(payload):
                    matched = True
            elif kind == FRAME_CLOSE:
                closed = True
        if matched:
            close_batch = b""
            if received_bytes:
                close_batch += frame(
                    FRAME_WINDOW, stream_id, received_bytes.to_bytes(4, "big")
                )
            if not closed:
                close_batch += frame(FRAME_CLOSE, stream_id)
            if close_batch:
                carrier.uplink(stream_id, close_batch, "Telegram stream close")
            return
        assert not closed, "logical stream closed before Telegram resPQ"
    raise AssertionError(f"Telegram resPQ deadline exceeded for DC {TELEGRAM_DC_ID}")


def verify_rejected_streams(carrier: WebCarrier, carrier_mode: str) -> None:
    stream_ids = (2, 3)
    invalid_handshake = bytes(64)
    if carrier_mode == "https":
        carrier.uplink(
            0,
            b"".join(
                frame(FRAME_OPEN, stream_id)
                + frame(FRAME_DATA, stream_id, invalid_handshake)
                for stream_id in stream_ids
            ),
            "invalid streams",
        )
        closed_streams: set[int] = set()
        deadline = time.monotonic() + STREAM_CLOSE_SECS
        while time.monotonic() < deadline and closed_streams != set(stream_ids):
            _, frames = carrier.downlink(0, "invalid streams")
            carrier.answer_pings(frames)
            closed_streams.update(
                stream_id
                for kind, stream_id, payload in frames
                if kind == FRAME_CLOSE and not payload
            )
    else:
        closed_streams = set()
        for stream_id in stream_ids:
            carrier.uplink(
                stream_id,
                frame(FRAME_OPEN, stream_id)
                + frame(FRAME_DATA, stream_id, invalid_handshake),
                f"invalid lane {stream_id}",
            )
            deadline = time.monotonic() + STREAM_CLOSE_SECS
            while time.monotonic() < deadline and stream_id not in closed_streams:
                _, frames = carrier.downlink(stream_id, f"invalid lane {stream_id}")
                carrier.answer_pings(frames)
                closed_streams.update(
                    frame_stream_id
                    for kind, frame_stream_id, payload in frames
                    if kind == FRAME_CLOSE and not payload
                )
    assert closed_streams == set(
        stream_ids
    ), f"missing logical-stream CLOSE frames: {closed_streams}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--secret", default=os.environ.get("TELEMT_WEB_FLOW_SECRET"))
    parser.add_argument("--secret-mode", choices=("plain", "dd"), required=True)
    parser.add_argument("--carrier", choices=("https", "https-lanes"), required=True)
    parser.add_argument("--decoy-marker", required=True)
    args = parser.parse_args()
    if not args.secret:
        parser.error("--secret or TELEMT_WEB_FLOW_SECRET is required")

    access_secret = bytes.fromhex(args.secret)
    capability_secret = access_secret
    if args.secret_mode == "dd":
        capability_secret = b"\xdd" + access_secret
    capability = (
        base64.urlsafe_b64encode(
            hmac.new(
                capability_secret,
                b"tdesktop-web-proxy-bridge-v1\n" + args.host.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )

    connection = httpx.Client(
        base_url=f"https://{args.host}:443",
        http2=True,
        verify=False,
        timeout=15,
        trust_env=False,
    )
    session = ""
    try:
        status, headers, bridge = request(
            connection, "GET", f"/?bridge={capability}", args.host
        )
        assert status == 200, f"bridge status={status}"
        bridge_text = bridge.decode("utf-8")
        assert 'const bootstrap="' in bridge_text
        assert "const negotiationEnabled=false" in bridge_text
        assert capability not in bridge_text
        assert "no-store" in headers.get("cache-control", "")
        bootstrap_match = re.search(
            r'const bootstrap="([A-Za-z0-9_-]{43})"', bridge_text
        )
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

        carrier = WebCarrier(connection, args.host, args.carrier, session)
        carrier.uplink(0, frame(FRAME_PONG, 0), "initial PONG")
        status, initial_frames = carrier.downlink(0, "initial")
        assert status == 204, f"initial downlink status={status}"
        assert not initial_frames

        telegram_round_trip(carrier, access_secret, args.secret_mode, stream_id=1)
        verify_rejected_streams(carrier, args.carrier)
    finally:
        active_error = sys.exc_info()[0] is not None
        cleanup_error: Exception | None = None
        if session:
            try:
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
            except Exception as error:  # Preserve the primary probe failure.
                cleanup_error = error
        connection.close()
        if cleanup_error is not None and not active_error:
            raise cleanup_error

    print(
        "WEB Telegram E2E passed: "
        f"http=HTTP/2, carrier={args.carrier}, mode={args.secret_mode}, "
        f"dc={TELEGRAM_DC_ID}, response=resPQ, rejected_streams=2"
    )


if __name__ == "__main__":
    main()
