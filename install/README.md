# Installer

```
sudo install/install.sh            # install or update (safe to run again)
install/install.sh --dry-run       # show what it would do
sudo install/install.sh --uninstall [--purge]
```

What it does, on Debian, Ubuntu and Raspberry Pi OS (Bookworm, Trixie) for Pi 3, 4, 5 and x86:

1. Installs `mpv` and `python3` with apt (skipped with `--offline`; it then fails if something is missing).
2. Creates group `pvj` and the player account (its home is `/var/lib/pvj-player`; older installs are moved there without moving any files) (default: the user who ran `sudo`, else a system user `pvj-player`), adds it to `video`, `render`, `audio`, `input`, and adds the web user to `pvj`.
3. Copies the program to `/opt/pvj/releases/<version>` and switches `/opt/pvj/current` to it atomically. The previous release stays in place and its path is written to `/opt/pvj/previous`, which is the basis for rollback.
4. Writes `/etc/pvj/pvj.env` (media and USB folders) only if it does not exist, so your edits survive updates, and `/etc/pvj/install.json`.
5. Installs, enables and starts `pvj-player.service`, and links `pvj-player` and `pvj-selftest` into `/usr/local/bin`.

Program files are owned by root and not writable by the web user. `/var/lib/pvj` (settings) belongs to the web user, mode 2750: the player can read its media there but cannot create or replace files, so a player compromised by a hostile file cannot change the settings. `--uninstall` keeps `/etc/pvj`, media, users and the group unless you add `--purge` (which removes only `/etc/pvj`).

`--stage DIR` installs into a folder without touching users, apt or systemd; the tests and the future image build use it.

**NDI is not installed by this script.** The NDI input is opt-in per box (D62): the installer adds no NDI service, account, group or avahi package, and the image does not either. One root command at the box sets it up, `sudo pvj-ndi-runtime install "<the NDI SDK folder>"` (see [../pvj/NDI.md](../pvj/NDI.md)), and `sudo pvj-ndi-runtime remove` takes it off. On a box that was set up, this script writes the helper's unit again at every install or update and keeps it enabled; `--uninstall` removes it and leaves `/opt/pvj-ndi` (the owner's copy of NDI's library) unless `--purge`.

Verified: `tests/test_install.py` (stage mode) and one real-mode run in a throwaway container (user and group creation, links, uninstall). Not verified: apt installation on a real Pi, `systemctl` enable and start, and boot-time behaviour.

Not included yet: the web panel, OSC and the read-only root option; updates from a signed USB stick or the network; automated rollback command.
