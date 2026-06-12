#!/usr/bin/python
# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

DOCUMENTATION = r"""
---
module: qr_code
short_description: Render a string (or a file's content) into a QR code PNG
description:
  - Encodes O(data) (or the content of O(src)) into a QR code and writes it to
    O(dest) as a PNG image.
  - Idempotent — the PNG is rewritten only when its bytes would actually change,
    so re-running a play does not report a change.
  - Typically run with C(delegate_to) pointed at localhost so the image is
    produced next to a generated config on the controller. The controller then
    needs the C(qrcode) Python library with Pillow (Debian/Ubuntu packages
    C(python3-qrcode) and C(python3-pil)).
  - The QR specification caps a single symbol at roughly 2953 bytes (version 40,
    error-correction level V(L)); larger payloads fail with a clear error. Use
    O(minify_json) to shrink JSON payloads to their most compact form.
version_added: "1.2.0"
options:
  data:
    description:
      - The string to encode. Mutually exclusive with O(src).
    type: str
  src:
    description:
      - Path to a file whose UTF-8 content is encoded. Mutually exclusive with
        O(data).
    type: path
  dest:
    description:
      - Path of the PNG file to write.
    type: path
    required: true
  minify_json:
    description:
      - When V(true) the payload is parsed as JSON and re-serialised in the most
        compact form (no insignificant whitespace) before encoding. Use it to
        fit a large pretty-printed JSON config inside a single QR code.
    type: bool
    default: false
  error_correction:
    description:
      - QR error-correction level. Higher levels survive more damage but hold
        less data — V(L) recovers ~7%% and gives the maximum capacity, V(H)
        recovers ~30%% with the least capacity.
    type: str
    choices: [L, M, Q, H]
    default: L
  box_size:
    description:
      - Size in pixels of each QR module (the small square cells).
    type: int
    default: 8
  border:
    description:
      - Width of the quiet zone around the symbol, in modules. The QR
        specification mandates a minimum of 4.
    type: int
    default: 4
extends_documentation_fragment:
  - ansible.builtin.files
requirements:
  - qrcode
  - pillow
author:
  - kogeler
"""

EXAMPLES = r"""
- name: Encode a sing-box client config into a QR PNG next to it
  kogeler.mini_pig.qr_code:
    src: /etc/ansible/configs/singbox-host-alice-auto.json
    dest: /etc/ansible/configs/singbox-host-alice-auto.png
    minify_json: true
    error_correction: L
    mode: "0600"
  delegate_to: localhost

- name: Encode an arbitrary string
  kogeler.mini_pig.qr_code:
    data: "https://example.com"
    dest: /tmp/link.png
"""

RETURN = r"""
dest:
  description: Path of the PNG that was (or would be) written.
  type: str
  returned: always
qr_version:
  description: QR version (1-40) the library selected to hold the payload.
  type: int
  returned: success
payload_bytes:
  description: Byte length of the encoded payload (after optional minification).
  type: int
  returned: success
"""

import io
import json
import os
import tempfile
import traceback

from ansible.module_utils.basic import AnsibleModule, missing_required_lib

QRCODE_IMP_ERR = None
try:
    import qrcode
    from qrcode.constants import (
        ERROR_CORRECT_L,
        ERROR_CORRECT_M,
        ERROR_CORRECT_Q,
        ERROR_CORRECT_H,
    )
    from qrcode.exceptions import DataOverflowError
    HAS_QRCODE = True
    _EC = {
        'L': ERROR_CORRECT_L,
        'M': ERROR_CORRECT_M,
        'Q': ERROR_CORRECT_Q,
        'H': ERROR_CORRECT_H,
    }
except ImportError:
    HAS_QRCODE = False
    QRCODE_IMP_ERR = traceback.format_exc()
    _EC = {}

# qrcode renders the PNG through Pillow's image factory; check it separately so
# the operator gets a precise "install Pillow" message instead of a stack trace.
PIL_IMP_ERR = None
try:
    import PIL  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    PIL_IMP_ERR = traceback.format_exc()


class _PayloadTooLarge(Exception):
    """The payload exceeds the largest QR symbol (version 40)."""


def render_png(data, ec, box_size, border):
    qr = qrcode.QRCode(
        version=None,                 # None -> fit the smallest version that holds the data
        error_correction=ec,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    try:
        # With auto-fit, exceeding version 40 surfaces as a ValueError
        # ("Invalid version (was 41 ...)"); a fixed version raises
        # DataOverflowError. Treat both as "too large" and keep the scope to the
        # fit step so genuine rendering errors below are not swallowed.
        qr.make(fit=True)
    except (DataOverflowError, ValueError) as e:
        raise _PayloadTooLarge(str(e))
    img = qr.make_image()             # default factory -> Pillow PNG image
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue(), qr.version


def main():
    module = AnsibleModule(
        argument_spec=dict(
            data=dict(type='str'),
            src=dict(type='path'),
            dest=dict(type='path', required=True),
            minify_json=dict(type='bool', default=False),
            error_correction=dict(type='str', default='L', choices=['L', 'M', 'Q', 'H']),
            box_size=dict(type='int', default=8),
            border=dict(type='int', default=4),
        ),
        mutually_exclusive=[['data', 'src']],
        required_one_of=[['data', 'src']],
        add_file_common_args=True,
        supports_check_mode=True,
    )

    if not HAS_QRCODE:
        module.fail_json(msg=missing_required_lib('qrcode'), exception=QRCODE_IMP_ERR)
    if not HAS_PIL:
        module.fail_json(msg=missing_required_lib('Pillow'), exception=PIL_IMP_ERR)

    p = module.params
    dest = p['dest']

    if p['src'] is not None:
        try:
            with open(p['src'], 'rb') as fh:
                payload = fh.read().decode('utf-8')
        except (OSError, UnicodeDecodeError) as e:
            module.fail_json(msg="cannot read src '%s': %s" % (p['src'], e))
    else:
        payload = p['data']

    if p['minify_json']:
        try:
            payload = json.dumps(json.loads(payload), separators=(',', ':'), ensure_ascii=False)
        except ValueError as e:
            module.fail_json(msg="minify_json: payload is not valid JSON: %s" % e)

    payload_bytes = len(payload.encode('utf-8'))

    try:
        png, version = render_png(payload, _EC[p['error_correction']], p['box_size'], p['border'])
    except _PayloadTooLarge as e:
        module.fail_json(
            msg=("payload of %d bytes does not fit in a single QR code at "
                 "error-correction level %s (the spec caps a symbol at ~2953 "
                 "bytes at level L). Reduce the data — e.g. fewer servers per "
                 "config — or lower the error-correction level. (qrcode: %s)"
                 % (payload_bytes, p['error_correction'], e)),
            payload_bytes=payload_bytes,
        )

    # Idempotency: a byte-identical PNG already on disk means no change. Pillow's
    # PNG encoder is deterministic for the same input, so a plain compare holds.
    changed = True
    if os.path.exists(dest):
        try:
            with open(dest, 'rb') as fh:
                changed = fh.read() != png
        except OSError:
            changed = True

    if changed and not module.check_mode:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(dest) or '.')
        try:
            with os.fdopen(tmp_fd, 'wb') as fh:
                fh.write(png)
            module.atomic_move(tmp_path, dest)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # Apply ownership/mode once the file is in place (skipped for a check-mode
    # create, where dest does not yet exist).
    if os.path.exists(dest):
        file_args = module.load_file_common_arguments(module.params, path=dest)
        changed = module.set_fs_attributes_if_different(file_args, changed)

    module.exit_json(changed=changed, dest=dest, qr_version=version, payload_bytes=payload_bytes)


if __name__ == '__main__':
    main()
