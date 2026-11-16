#!/bin/bash -e
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
