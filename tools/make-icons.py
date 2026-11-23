#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Write the panel's icon files into mctl/web from the one drawing in mctl/icon.py (D151, docs/WEBAPP.md).

    tools/make-icons.py            write the files
    tools/make-icons.py --check    write nothing; say which files differ from the drawing, exit 1 if any does

The files are committed. tests/test_webapp.py fails when a committed file is not what this script makes, so after a
change to the drawing: run this, look at the pictures, commit them with the change. Standard library only; no
service outside this repository is used.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mctl import icon  # noqa: E402

WEB = os.path.join(ROOT, "mctl", "web")


def differing(web=WEB):
    """The names whose file in `web` is missing or is not what the drawing gives."""
    out = []
    for name, data in sorted(icon.files().items()):
        try:
            with open(os.path.join(web, name), "rb") as f:
                have = f.read()
            # a PNG by its picture, not its bytes: zlib packs the same picture differently from one version to another
            same = icon.read(have) == icon.read(data) if name.endswith(".png") else have == data
        except (OSError, ValueError):
            same = False
        if not same:
            out.append(name)
    return out


def write(web=WEB):
    for name, data in sorted(icon.files().items()):
        with open(os.path.join(web, name), "wb") as f:
            f.write(data)
    return sorted(icon.files())


def main(argv):
    if argv[1:] == ["--check"]:
        bad = differing()
        for name in bad:
            print("mctl/web/%s is not what mctl/icon.py draws; run tools/make-icons.py" % name)
        return 1 if bad else 0
    if argv[1:]:
        print(__doc__)
        return 2
    for name in write():
        print("wrote mctl/web/" + name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
