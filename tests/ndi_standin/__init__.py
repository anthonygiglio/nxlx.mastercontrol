# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The stand-in for NDI's library (standin.c) and what the tests that use it share. Tests only; nothing of NDI's."""
import ctypes
import os
import shutil
import subprocess

SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "standin.c")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def build(path):
    """Build the stand-in at `path` with this machine's C compiler; returns what the compiler printed (its
    warnings). Raises AssertionError with the compiler's words if it did not build."""
    done = subprocess.run([CC, "-shared", "-fPIC", "-pthread", "-O2", "-Wall", "-Wextra", "-o", path, SOURCE],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if done.returncode != 0:
        raise AssertionError("the stand-in did not build:\n" + done.stdout)
    return done.stdout


SOUND_ALONE, PICTURE = 10, 100          # the two bandwidths the helper asks with (ndi.BANDWIDTH_AUDIO_ONLY, ndi.BANDWIDTH_HIGHEST)


def receivers(path, name, bandwidth, open_now=False):
    """How many receivers the stand-in at `path` made IN THIS PROCESS for the source called `name` with that
    bandwidth (SOUND_ALONE or PICTURE); with `open_now`, only those not given back. `path` must be the very file the
    helper's code loaded: the same file loaded again is the same library with the same memory, a copy of it is not."""
    lib = ctypes.CDLL(path)
    lib.pvj_standin_receivers.restype = ctypes.c_int
    lib.pvj_standin_receivers.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    return lib.pvj_standin_receivers(name.encode("utf-8"), bandwidth, 1 if open_now else 0)
