# Image build (Raspberry Pi OS Lite, Trixie, 64-bit)

`.github/workflows/image.yml` builds `nxlx-mastercontrol-*.img.xz` with [pi-gen](https://github.com/RPi-Distro/pi-gen) (`arm64` branch) and uploads it, with `SHA256SUMS`, as a workflow artifact. Run it from the Actions tab (it takes about 35 minutes) or by pushing a `v*` tag.

The image works on Pi 3, 4 and 5 (all 64-bit capable). It contains Raspberry Pi OS Lite plus `mpv`, `python3` and this repository's `mctl/`, `bin/` and `install/`, installed with the offline installer, with `pvj-player`, `pvj-web` (the panel, port 80) and `pvj-netd` enabled. There is no `git` and no `tests/` folder on the image.

- **No default password.** The first boot asks for a user name and password. SSH is off; enable it with `raspi-config` after setting a password.
- **Media** goes to `/var/lib/pvj/video`. Before turning on the read-only root (`sudo pvj-rootfs enable`), move media to a second disk or USB drive, because `/` is in RAM afterwards.
- **Build inputs** are `image/config` and `image/stage-pvj/`. The workflow copies them into a pi-gen checkout, so nothing here modifies pi-gen itself.

## Not verified

The image has been built in CI several times and **booted once**, on a Raspberry Pi 4 (2026-09-30): it starts the panel, and after two service fixes (see the journal) the player starts at boot and recovers from a kill. Everything else on the checklist is still untested on hardware. `tests/test_image.py` only checks the files. To test it, follow "Option C" in [../tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md). Things that can still break: the pi-gen branch or stage names changing (the workflow input `pi_gen_ref` lets you pin one), `qemu` setup on the runner, disk space, and the installer step inside the chroot. Pin `pi_gen_ref` to a commit once a build works. Boot the resulting image on each of Pi 3, 4 and 5 and run `pvj-selftest --play`.
