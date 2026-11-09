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

SRC = src/main.c src/out.c src/ipc.c src/hook.c src/json.c src/fx.c src/still.c
OBJ = $(SRC:.c=.o)

pvj-render: $(OBJ)
	$(CC) $(LDFLAGS) $(LDHARDEN) -o $@ $(OBJ) $$($(PKG_CONFIG) --libs $(PKGS)) -lpthread -lm

src/%.o: src/%.c src/pvj.h src/json.h
	$(CC) -std=c11 $(CFLAGS) $(DEFS) $(WARN) $(HARDEN) $$($(PKG_CONFIG) --cflags $(PKGS)) -c -o $@ $<

# pvj-link (D117): the Ableton Link follower, a program of its own in C++, built only when asked for, and never by
# the plain "make" above: make pvj-link. Needs a C++17 compiler and Link's headers, which pull in asio's
# (Debian and Ubuntu: g++ ableton-link-dev libasio-dev; nothing of Link is kept in this repository). LINK_INCLUDE is for
# a folder of headers that is not a system one. pvj-link-peer is a stand-in peer for tests/test_pvj_link.py, never installed.
CXX ?= c++
CXXFLAGS ?= -O2
LINK_INCLUDE ?=
LINK_DEFS = -DLINK_PLATFORM_LINUX=1 $(if $(LINK_INCLUDE),-isystem $(LINK_INCLUDE))
LINK_WARN = -Wall -Wextra -Wshadow
ifeq ($(WERROR),1)
LINK_WARN += -Werror
endif

pvj-link: link/pvj-link.cpp
	$(CXX) -std=c++17 $(CXXFLAGS) $(LINK_DEFS) $(LINK_WARN) $(HARDEN) $(LDFLAGS) $(LDHARDEN) -o $@ $< -pthread -latomic

pvj-link-peer: link/link-peer.cpp
	$(CXX) -std=c++17 $(CXXFLAGS) $(LINK_DEFS) $(LINK_WARN) $(HARDEN) $(LDFLAGS) $(LDHARDEN) -o $@ $< -pthread -latomic

# pvj-raster (D129): the one program that decodes an uploaded picture, run by systemd as a throwaway process with no
# network and hard limits (install/pvj-raster@.service). Plain make does not build it: make pvj-raster. Needs libpng and
# libjpeg headers (Debian and Ubuntu: libpng-dev libjpeg-dev). The SVG reader is nanosvg, vendored unchanged in
# raster/vendor with its licence; -isystem keeps its own warnings out of ours.
pvj-raster: raster/pvj-raster.c raster/vendor/nanosvg.h raster/vendor/nanosvgrast.h
	$(CC) -std=c11 $(CFLAGS) $(WARN) $(HARDEN) -isystem raster/vendor $$($(PKG_CONFIG) --cflags libpng libjpeg) $(LDFLAGS) $(LDHARDEN) -o $@ $< $$($(PKG_CONFIG) --libs libpng libjpeg) -lm

clean:
	rm -f pvj-render pvj-link pvj-link-peer pvj-raster $(OBJ)

.PHONY: clean
