<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D137. On a PC node, Automatic sound is the HDMI sound of the only screen, or of a screen told apart beyond doubt; otherwise the Owner chooses (2026-11-14)

What: on any node that is not a Pi, the sound output Automatic (`/api/audio`, `device: auto`) is chosen like this.

1. Which HDMI or DisplayPort sound PCMs have a screen is read from ALSA's jack controls, `HDMI/DP,pcm=N Jack`, with `amixer -c N contents` (alsa-utils, which the installer already installs).
2. **One PCM with a screen:** Automatic uses it, whatever the names say. This is the common case, one projector on a PC.
3. **Several:** Automatic uses one only when every PCM's ELD (ALSA's `ELD` control of that PCM) and every connected HDMI or DisplayPort screen's EDID has a monitor name, maker and product that no other on its side has, and the screen the picture is on has the same three as exactly one PCM. Otherwise Automatic stays mpv's own choice, as before, and `GET /api/audio` gives `automatic_note`, which the panel's sound section shows: "Several HDMI screens: choose the sound output by hand".

The screen the picture is on is the one the player said it draws on (the screen report, D128), else the chosen output when connected, else the first connected HDMI screen, then DisplayPort. The mpv name used is `alsa/plughw:CARD=X,DEV=N` (the one measured), else `alsa/hdmi:CARD=X,DEV=k` (k from the PCM's "HDMI k" name), and only when mpv lists it. The periodic check (`ensure_audio`) and the player's restart after an output change (`apply_audio`) follow the picture. An output the Owner chose by hand is kept as it is. A Pi is unchanged: its vc4hdmi rule comes first, and the PC rule, amixer included, never runs on a Pi.

Why, from what was measured on the Lenovo M710q (Debian 13, Intel Kaby Lake HD 630, three capture cards on HDMI-A-1, -2 and -3, 2026-11-14), with speaker-test playing 440 Hz on each PCM:

- PCM 3 reached HDMI-A-1 (also with mpv on `alsa/plughw:CARD=PCH,DEV=3`, at -24 dB); PCM 8 reached HDMI-A-2; PCM 7 reached neither (presumably HDMI-A-3, which had no recorder). On Automatic, before this, the sound went to the analog jack.
- The HDA driver on Kaby Lake gives PCMs to pins as screens come, so neither pin order nor port order says which PCM is which screen. A first version that went by port order gave HDMI-A-2 PCM 7: wrong.
- The ELDs and EDIDs contradict each other: the ELD maker was 0x2157 on all three PCMs, the product 0x2131 for PCM 3 and 7 and 0x2130 for PCM 8; the EDIDs gave 0x2131 for HDMI-A-1 and -2 and 0x2130 for -3. Pairing by product would put HDMI-A-3 on PCM 8, yet PCM 8's tone came out on HDMI-A-2. So with screens that look alike, names and models are not trusted at all, not even for the one that looks unique.
- The `/proc/asound/cardN/eld#C.N` files were not readable that way on the node (and come three a port with DP MST); the ALSA controls are what is read.

**The pick is held steady** (the review): the periodic check asks every few seconds, and a read that fails, times out (amixer is given 3 seconds) or finds no screen for a moment (a jack drops while a screen changes mode) keeps the last pick, so the sound is not moved to the analog jack and back in a show. A different pick for the same screen is taken only after two reads in a row give it. The first pick, and the pick for another screen after the output was changed, are taken at once. One request reads amixer once.

**Assumptions for other boards**, unmeasured: that the jack controls tell true which PCMs have a screen (on the M710q they matched the three screens); that where every screen is told apart by name, maker and product, ELD and EDID pair correctly (the M710q could not show it, its screens being alike); that AMD and NVIDIA HDMI sound give the same controls.

Cost / rules out: with several HDMI screens of one kind (two capture cards of one model, a pair of projectors of one model), Automatic does not reach any of them, and the Owner chooses the sound output by hand. amixer is run on each periodic check on a PC (once per card). Two monitors on one DP MST port are not told apart. A screen really unplugged keeps its last pick until the next good read says otherwise; a real move of the sound takes one more periodic check.
