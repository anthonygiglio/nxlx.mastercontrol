# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The Raspberry Pi's boot files for the AV (composite) output, edited with a backup (D127).

Under the full KMS driver the composite video output is off by default. The Raspberry Pi documentation (config.txt,
video options) says it is on only with `enable_tvout=1` (which, on a Pi 4 and a Pi 5, also disables the HDMI outputs: "On
supported models, you must disable HDMI output to enable composite output"), with `,composite` added to the end of the
`dtoverlay=vc4-kms-v3d` line of /boot/firmware/config.txt, and that its standard is NTSC unless
`vc4.tv_norm=<NTSC|NTSC-J|NTSC-443|PAL|PAL-M|PAL-N|PAL60|SECAM>` is added to the single line of
/boot/firmware/cmdline.txt. The kernel's overlay README says the same of the parameter: "composite: Enable the composite
output (disables all other outputs)" on a Pi 4. A change takes effect at the next start of the node.

This module is root's: pvj-sysd calls `apply` when the Owner asks (the panel never writes these files), and the panel
only reads them (`state`). It changes nothing but these, and only these ways:

* config.txt: `,composite` appended to the parameters of each active `dtoverlay=vc4-kms-v3d` (or `-pi4`) line, and a
  small marked block `[all]` / `enable_tvout=1` at the end. Turning it off takes both out again, and nothing else.
* cmdline.txt: `vc4.tv_norm=<norm>` set (the one line stays one line); turning it off removes it.
* Before a file is changed it is copied beside itself as `<name>.nxlx-<seconds>` (the newest KEEP of those are kept),
  and the first time also as `<name>.nxlx-original`, which is never removed. The new text is written to a file beside
  it and renamed over the old one. The boot partition is its own filesystem, so this holds under the read-only root too.

A node that does not use the full KMS driver (no `vc4-kms-v3d` line) is refused with a plain sentence, and nothing is
written."""

import os
import re
import shutil
import time

CONFIG_PATHS = ("/boot/firmware/config.txt", "/boot/config.txt")
CMDLINE_PATHS = ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt")
NORMS = ("NTSC", "NTSC-J", "NTSC-443", "PAL", "PAL-M", "PAL-N", "PAL60", "SECAM")
DEFAULT_NORM = "NTSC"              # the owner is in Canada; it is also the kernel's own default
KEEP = 3                           # timestamped backups kept for each file
MAX_BYTES = 256 * 1024             # a config file bigger than this is not one of ours to edit
BEGIN = "# nxlx.mastercontrol AV output: begin (taken out again when the AV output is turned off)"
END = "# nxlx.mastercontrol AV output: end"
_OVERLAY = re.compile(r"^(\s*dtoverlay\s*=\s*)(vc4-kms-v3d(?:-pi4)?)((?:,[^#\s]*)?)(\s*(?:#.*)?)$")
_NORM = re.compile(r"^vc4\.tv_norm=([A-Za-z0-9-]+)$")


class BootError(Exception):
    """A change that was refused, in plain words."""


def _find(paths, root):
    for p in paths:
        full = os.path.join(root, p.lstrip("/"))
        if os.path.isfile(full):
            return full
    return None


def _read(path):
    with open(path, "r", encoding="utf-8", errors="strict") as f:
        text = f.read(MAX_BYTES + 1)
    if len(text) > MAX_BYTES:
        raise BootError("%s is too large to be a boot configuration" % os.path.basename(path))
    return text


def _tokens(params):
    return [t for t in params.lstrip(",").split(",") if t]


def _is_composite(token):
    return token == "composite" or re.fullmatch(r"composite=(1|true|on|yes)", token) is not None


def _is_composite_any(token):
    return token == "composite" or token.startswith("composite=")


def config_enabled(text):
    """True when an active vc4-kms-v3d line turns the composite parameter on."""
    for line in text.splitlines():
        m = _OVERLAY.match(line)
        if m and any(_is_composite(t) for t in _tokens(m.group(3))):
            return True
    return False


def has_kms(text):
    return any(_OVERLAY.match(line) for line in text.splitlines())


def tvout_on(text):
    return any(re.fullmatch(r"\s*enable_tvout\s*=\s*1\s*(#.*)?", line) for line in text.splitlines())


def cmdline_norm(text):
    for tok in text.split():
        m = _NORM.match(tok)
        if m:
            return m.group(1)
    return None


def edit_config(text, enable):
    """The config text with the AV output on or off. Raises BootError where the file is not one that can be edited."""
    if not has_kms(text):
        raise BootError("this node does not use the full KMS driver (no dtoverlay=vc4-kms-v3d line in config.txt), so the AV "
                        "output cannot be switched here")
    out = []
    skipping = removed = False
    for line in text.splitlines():
        if line.strip() == BEGIN:
            skipping = removed = True
            continue
        if skipping:
            if line.strip() == END:
                skipping = False
            continue
        m = _OVERLAY.match(line)
        if m:
            tokens = [t for t in _tokens(m.group(3)) if not _is_composite_any(t)]
            if enable:
                tokens.append("composite")
            line = m.group(1) + m.group(2) + ("," + ",".join(tokens) if tokens else "") + m.group(4)
        out.append(line)
    if skipping:
        raise BootError("config.txt has the start of this node's AV block and not its end; fix or remove it by hand")
    if removed or enable:
        while out and not out[-1].strip():              # the blank line that was put before the block goes with it
            out.pop()
    if enable:
        out += ["", BEGIN, "[all]", "enable_tvout=1", END]
    return "\n".join(out) + "\n"


def edit_cmdline(text, norm):
    """The one line of cmdline.txt with vc4.tv_norm set to `norm` (None takes it out)."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) != 1:
        raise BootError("cmdline.txt does not hold exactly one line, so it is not changed")
    tokens = [t for t in lines[0].split() if not _NORM.match(t)]
    if norm:
        tokens.append("vc4.tv_norm=" + norm)
    return " ".join(tokens) + "\n"


def state(root="/"):
    """What the boot files say: {"found", "kms", "composite", "tvout", "norm", "config", "cmdline"}; nothing raises."""
    out = {"found": False, "kms": False, "composite": False, "tvout": False, "norm": None, "config": None, "cmdline": None}
    config, cmdline = _find(CONFIG_PATHS, root), _find(CMDLINE_PATHS, root)
    out["config"], out["cmdline"] = config, cmdline
    try:
        if config:
            text = _read(config)
            out.update(found=True, kms=has_kms(text), composite=config_enabled(text), tvout=tvout_on(text))
        if cmdline:
            out["norm"] = cmdline_norm(_read(cmdline))
    except (OSError, ValueError, BootError):
        pass
    return out


def _backup(path, now):
    """A copy beside the file; the original once, the newest KEEP timestamped ones. Returns the new copy's path."""
    original = path + ".nxlx-original"
    if not os.path.exists(original):
        shutil.copy2(path, original)
    mine = path + ".nxlx-%d" % int(now)
    shutil.copy2(path, mine)
    olds = sorted(p for p in (os.path.join(os.path.dirname(path), n) for n in os.listdir(os.path.dirname(path)))
                  if re.fullmatch(re.escape(path) + r"\.nxlx-[0-9]+", p))
    for p in olds[:-KEEP]:
        try:
            os.unlink(p)
        except OSError:
            pass
    return mine


def _replace(path, text):
    tmp = path + ".nxlx-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass                                    # a FAT partition keeps no modes
    os.replace(tmp, path)


def apply(enable, norm=None, root="/", now=time.time):
    """Turn the AV output on or off in the boot files. Returns {"changed", "backups", "state"}. Raises BootError."""
    config, cmdline = _find(CONFIG_PATHS, root), _find(CMDLINE_PATHS, root)
    if not config or not cmdline:
        raise BootError("this node has no boot configuration to change (not a Raspberry Pi, or its boot files are elsewhere)")
    if enable and norm not in NORMS:
        raise BootError("the TV standard must be one of %s" % ", ".join(NORMS))
    try:
        old_config, old_cmdline = _read(config), _read(cmdline)
    except (OSError, ValueError) as e:
        raise BootError("the boot files could not be read (%s)" % (getattr(e, "strerror", None) or type(e).__name__))
    new_config = edit_config(old_config, enable)
    new_cmdline = edit_cmdline(old_cmdline, norm if enable else None)
    new_config = old_config if new_config.rstrip("\n") == old_config.rstrip("\n") else new_config       # a missing last newline is no change
    new_cmdline = old_cmdline if new_cmdline.rstrip("\n") == old_cmdline.rstrip("\n") else new_cmdline
    backups, changed = [], False
    try:
        if new_config != old_config:
            backups.append(_backup(config, now()))
            _replace(config, new_config)
            changed = True
        if new_cmdline != old_cmdline:
            backups.append(_backup(cmdline, now()))
            _replace(cmdline, new_cmdline)
            changed = True
    except OSError as e:
        try:                                    # the first file was written and the second was not: put the first back
            if new_config != old_config:
                _replace(config, old_config)
        except OSError:
            pass
        raise BootError("the boot files could not be written (%s); nothing was left changed" % (getattr(e, "strerror", None) or type(e).__name__))
    return {"changed": changed, "backups": [os.path.basename(b) for b in backups], "state": state(root)}
