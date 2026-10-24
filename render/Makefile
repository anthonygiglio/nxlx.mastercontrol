# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# pvj-render: make            (the program, here)
#             make WERROR=1   (every warning is an error: CI)
#             make TEST_HOOKS=1 (the tests' hooks, PVJ_RENDER_TEST_*: CI; never the box's own build. make clean
#                              first when switching, as the objects do not know which build they were)
#             make clean
# Needs a C11 compiler, make, pkg-config and the headers of libmpv, EGL, GLES, GBM and libdrm
# (Debian and Ubuntu: gcc make pkg-config libmpv-dev libegl-dev libgles-dev libgbm-dev libdrm-dev).

PKGS = mpv egl glesv2 gbm libdrm
PKG_CONFIG ?= pkg-config
CC ?= cc
CFLAGS ?= -O2 -g
WARN = -Wall -Wextra -Wshadow -Wformat=2 -Wno-unused-parameter
ifeq ($(WERROR),1)
WARN += -Werror
endif
DEFS =
ifeq ($(TEST_HOOKS),1)
DEFS += -DPVJ_RENDER_TEST_HOOKS
endif
HARDEN = -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE
LDHARDEN = -pie -Wl,-z,relro,-z,now

SRC = src/main.c src/out.c src/ipc.c src/hook.c src/json.c
OBJ = $(SRC:.c=.o)

pvj-render: $(OBJ)
	$(CC) $(LDFLAGS) $(LDHARDEN) -o $@ $(OBJ) $$($(PKG_CONFIG) --libs $(PKGS)) -lpthread -lm

src/%.o: src/%.c src/pvj.h src/json.h
	$(CC) -std=c11 $(CFLAGS) $(DEFS) $(WARN) $(HARDEN) $$($(PKG_CONFIG) --cflags $(PKGS)) -c -o $@ $<

clean:
	rm -f pvj-render $(OBJ)

.PHONY: clean
