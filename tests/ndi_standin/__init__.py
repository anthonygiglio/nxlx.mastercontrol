# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The stand-in for NDI's library (standin.c) and what the tests that use it share. Tests only; nothing of NDI's."""
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
