#!/usr/bin/python
# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: qr_decode
short_description: Decode a QR code PNG back to its text payload
description:
  - Reads a QR code PNG from O(src) and returns the embedded text in RV(data).
  - The counterpart of M(kogeler.mini_pig.qr_code) — together they give a pure
    Python round-trip (encode then decode) with no external tools such as
    C(zbarimg).
  - Read-only — it never changes anything and always reports C(changed=false).
  - Decoding reuses the C(qrcode) library's own version/mask/error-correction
    block tables (so the decoder can never drift from the encoder) plus Pillow
    for pixel access (Debian/Ubuntu packages C(python3-qrcode) and
    C(python3-pil)). It targets clean, axis-aligned, computer-generated symbols
    (as produced by M(kogeler.mini_pig.qr_code)); it does NOT do Reed-Solomon
    error correction, perspective correction, or camera-grade detection.
version_added: "1.2.0"
options:
  src:
    description:
      - Path to the QR code PNG to decode.
    type: path
    required: true
requirements:
  - qrcode
  - pillow
author:
  - kogeler
"""

EXAMPLES = r"""
- name: Round-trip a generated client-config QR code
  block:
    - name: Encode the config into a PNG
      kogeler.mini_pig.qr_code:
        src: /tmp/singbox-host-alice-auto.json
        dest: /tmp/singbox-host-alice-auto.png
        minify_json: true
    - name: Decode it back
      kogeler.mini_pig.qr_decode:
        src: /tmp/singbox-host-alice-auto.png
      register: decoded
    - name: Assert the QR carries the same config object
      ansible.builtin.assert:
        that:
          - (decoded.data | from_json) == (lookup('file', '/tmp/singbox-host-alice-auto.json') | from_json)
  delegate_to: localhost
"""

RETURN = r"""
data:
  description: The decoded text payload.
  type: str
  returned: success
length:
  description: Byte length of the decoded payload (UTF-8).
  type: int
  returned: success
qr_version:
  description: QR version (1-40) detected from the symbol size.
  type: int
  returned: success
"""

import traceback

from ansible.module_utils.basic import AnsibleModule, missing_required_lib

QRCODE_IMP_ERR = None
try:
    import qrcode
    import qrcode.util as qr_util
    import qrcode.base as qr_base
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    QRCODE_IMP_ERR = traceback.format_exc()

PIL_IMP_ERR = None
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    PIL_IMP_ERR = traceback.format_exc()

# Format-information BCH mask (QR spec G15_MASK, 0b101010000010010).
_FORMAT_MASK = 0x5412
# Alphanumeric mode character set, indexed by 6-bit value.
_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


class _DecodeError(Exception):
    pass


def _read_matrix(path):
    """Sample the module grid out of a clean, axis-aligned QR PNG."""
    img = Image.open(path).convert('L')
    px = img.load()
    width, height = img.size

    def dark(x, y):
        return px[x, y] < 128

    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            if dark(x, y):
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if max_x < 0:
        raise _DecodeError("no dark pixels found — not a QR image")

    # The three finder patterns pin all four edges, so the dark bounding box is
    # the whole symbol. The top-left finder's top row is a solid 7-module run,
    # which gives the module size in pixels.
    run = 0
    x = min_x
    while x <= max_x and dark(x, min_y):
        run += 1
        x += 1
    module_px = run / 7.0
    if module_px <= 0:
        raise _DecodeError("could not measure module size")
    dim = int(round((max_x - min_x + 1) / module_px))
    if dim < 21 or (dim - 17) % 4 != 0:
        raise _DecodeError("unexpected symbol size (%d modules)" % dim)

    matrix = [[False] * dim for _ in range(dim)]
    for r in range(dim):
        for c in range(dim):
            cx = int(min_x + (c + 0.5) * module_px)
            cy = int(min_y + (r + 0.5) * module_px)
            matrix[r][c] = dark(cx, cy)
    return matrix, dim


def _read_format(matrix, dim):
    """Read the 15-bit format info (around the top-left finder) → (ec, mask)."""
    bits = 0
    for i in range(15):
        if i < 6:
            r, c = i, 8
        elif i < 8:
            r, c = i + 1, 8
        else:
            r, c = dim - 15 + i, 8
        if matrix[r][c]:
            bits |= (1 << i)
    data = ((bits ^ _FORMAT_MASK) >> 10) & 0x1F
    return (data >> 3) & 0x3, data & 0x7


def _function_modules(version, error_correction, mask, dim):
    """Rebuild the function-pattern map; data modules are left as None."""
    qr = qrcode.QRCode(version=version, error_correction=error_correction,
                       box_size=1, border=0)
    qr.modules_count = dim
    qr.modules = [[None] * dim for _ in range(dim)]
    qr.setup_position_probe_pattern(0, 0)
    qr.setup_position_probe_pattern(dim - 7, 0)
    qr.setup_position_probe_pattern(0, dim - 7)
    qr.setup_position_adjust_pattern()
    qr.setup_timing_pattern()
    qr.setup_type_info(True, mask)        # test=True → just reserve the cells
    if version >= 7:
        qr.setup_type_number(True)
    return qr.modules


def _read_codewords(matrix, fmap, mask, dim):
    """Walk the standard zigzag data placement, unmask, emit codeword bytes."""
    mask_func = qr_util.mask_func(mask)
    bits = []
    row = dim - 1
    inc = -1
    for col in range(dim - 1, 0, -2):
        if col <= 6:
            col -= 1
        col_range = (col, col - 1)
        while True:
            for c in col_range:
                if fmap[row][c] is None:
                    bit = matrix[row][c]
                    if mask_func(row, c):
                        bit = not bit
                    bits.append(1 if bit else 0)
            row += inc
            if row < 0 or dim <= row:
                row -= inc
                inc = -inc
                break
    codewords = []
    for i in range(0, (len(bits) // 8) * 8, 8):
        value = 0
        for j in range(8):
            value = (value << 1) | bits[i + j]
        codewords.append(value)
    return codewords


def _deinterleave(codewords, version, error_correction):
    """Reassemble the message data codewords from the interleaved stream."""
    blocks = qr_base.rs_blocks(version, error_correction)
    total_data = sum(b.data_count for b in blocks)
    data = codewords[:total_data]
    per_block = [[] for _ in blocks]
    max_dc = max(b.data_count for b in blocks)
    idx = 0
    for i in range(max_dc):
        for bi, b in enumerate(blocks):
            if i < b.data_count:
                per_block[bi].append(data[idx])
                idx += 1
    out = bytearray()
    for blk in per_block:
        out.extend(blk)
    return bytes(out)


def _parse_segments(message, version):
    """Parse the QR bitstream (byte / numeric / alphanumeric segments)."""
    bits = []
    for byte in message:
        for j in range(7, -1, -1):
            bits.append((byte >> j) & 1)
    pos = 0

    def take(n):
        nonlocal pos
        value = 0
        for _ in range(n):
            value = (value << 1) | bits[pos]
            pos += 1
        return value

    out = bytearray()
    while pos + 4 <= len(bits):
        mode = take(4)
        if mode == 0:                      # terminator
            break
        if mode == qr_util.MODE_8BIT_BYTE:
            n = take(qr_util.length_in_bits(qr_util.MODE_8BIT_BYTE, version))
            for _ in range(n):
                out.append(take(8))
        elif mode == qr_util.MODE_NUMBER:
            n = take(qr_util.length_in_bits(qr_util.MODE_NUMBER, version))
            while n >= 3:
                out.extend(("%03d" % take(10)).encode())
                n -= 3
            if n == 2:
                out.extend(("%02d" % take(7)).encode())
            elif n == 1:
                out.extend(("%d" % take(4)).encode())
        elif mode == qr_util.MODE_ALPHA_NUM:
            n = take(qr_util.length_in_bits(qr_util.MODE_ALPHA_NUM, version))
            while n >= 2:
                value = take(11)
                out.append(ord(_ALNUM[value // 45]))
                out.append(ord(_ALNUM[value % 45]))
                n -= 2
            if n == 1:
                out.append(ord(_ALNUM[take(6)]))
        else:
            raise _DecodeError("unsupported QR mode indicator %d" % mode)
    return out.decode('utf-8')


def decode_png(path):
    matrix, dim = _read_matrix(path)
    error_correction, mask = _read_format(matrix, dim)
    version = (dim - 17) // 4
    fmap = _function_modules(version, error_correction, mask, dim)
    codewords = _read_codewords(matrix, fmap, mask, dim)
    message = _deinterleave(codewords, version, error_correction)
    return _parse_segments(message, version), version


def main():
    module = AnsibleModule(
        argument_spec=dict(
            src=dict(type='path', required=True),
        ),
        supports_check_mode=True,
    )

    if not HAS_QRCODE:
        module.fail_json(msg=missing_required_lib('qrcode'), exception=QRCODE_IMP_ERR)
    if not HAS_PIL:
        module.fail_json(msg=missing_required_lib('Pillow'), exception=PIL_IMP_ERR)

    src = module.params['src']
    try:
        data, version = decode_png(src)
    except _DecodeError as e:
        module.fail_json(msg="failed to decode QR code '%s': %s" % (src, e))
    except (OSError, ValueError, IndexError, UnicodeDecodeError) as e:
        module.fail_json(msg="failed to decode QR code '%s': %s" % (src, e),
                         exception=traceback.format_exc())

    module.exit_json(changed=False, data=data, length=len(data.encode('utf-8')),
                     qr_version=version)


if __name__ == '__main__':
    main()
