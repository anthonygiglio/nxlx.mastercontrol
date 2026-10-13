# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The NDI input against a REAL mpv, and in CI against the mpv the Pi runs (the job "ndi-mpv (Debian 13, the Pi's
mpv)" in a debian:trixie container).

Why: on 2026-10-12 a mended hand-over was green in the real-install job (Ubuntu's mpv 0.37) and still stuck on the
owner's Pi (Debian 13, mpv 0.40): the pipe was mpv's path, it had no track, and its core sat idle. A green run on
another mpv proved nothing for the box.

What is real here: the stand-in for NDI's library (tests/ndi_standin/standin.c) loaded by the helper's real code
(ndi.Service with its Receiver), a real pipe, the panel's real Api with its NDI input and its once-a-second watch,
and a real mpv started by the real Player (its own command line and its own loading of the pipe, with MKV_ONLY and
the pipe's options; only the outputs differ: --vo=null --ao=null). Each case is judged by mpv's own word, with the
same code the real-install test uses (tests/ndi_standin/mpvword.py).

What is not: systemd, the accounts and root's pipe (tests/real_install_test.sh has those), NDI's own library, a
screen, a sound device, a Raspberry Pi's processor.

It runs only where it is asked for (PVJ_NDI_MPV_TEST=1): it takes minutes, and its answer belongs to one mpv. There,
a missing mpv or C compiler is a failure, not a skip.

Two kinds of case, as in the real-install test. A case that passed on master's pvj is asked with `must` and is
always fatal. A case that failed there (the faults the Pi showed) is asked with `known` and is under the one switch:
with PVJ_NDI_FINDINGS_FATAL=yes it fails like any other, and CI's job sets that since the mending; without it, it
prints a line beginning FINDING and is counted as skipped, with the reason. CI's job also runs the still cases on
the pvj code from before the mending, where they must fail.

A third kind, since sound from another sender (D87): a case for a part of the product that is NOT BUILT YET is asked
with `not_built`. It is the same FINDING, printed and counted as skipped with its reason, and it has a switch of its
own, PVJ_NDI_NOT_BUILT_FATAL=yes, because CI's job runs with PVJ_NDI_FINDINGS_FATAL=yes and would be red for a part
nobody has written. Whoever builds the part sets it to yes in the job in the same change, and then folds the case
into `must`. The list of them is in pvj/NDI.md ("Sound from another sender: what the tests show")."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

from pvj import ndi
from pvj.player import Player
from tests import ndi_standin
from tests.ndi_standin import mpvword
from tests.test_ndi import FakeClient
from tests.test_server import ServerBase
from tests.test_support import LAN

ASKED = os.environ.get("PVJ_NDI_MPV_TEST") == "1"
FATAL = os.environ.get("PVJ_NDI_FINDINGS_FATAL", "no") == "yes"
NOT_BUILT_FATAL = os.environ.get("PVJ_NDI_NOT_BUILT_FATAL", "no") == "yes"      # the cases asked with `not_built`
WANT = os.environ.get("PVJ_NDI_MPV_WANT", "")      # the start of the version this run is for, such as "0.40."
MPV = shutil.which("mpv")

MOVING, SOUND, STILL = "PVJ-STANDIN (moving)", "PVJ-STANDIN (moving with sound)", "PVJ-STANDIN (one still then sound)"
THEN_STILL = "PVJ-STANDIN (moving then still with sound)"
STILL_SHORT_GAP, STILL_LONG_GAP = "PVJ-STANDIN (one still then sound after a short gap)", "PVJ-STANDIN (one still then sound after a long gap)"
THEN_STILL_GAP = "PVJ-STANDIN (moving then still with sound after a short gap)"
BIG_SOUND, BIG_CHANGES_SOUND = "PVJ-STANDIN (big moving with sound)", "PVJ-STANDIN (big changes size with sound)"
BIG_A_SOUND, BIG_B_SOUND = "PVJ-STANDIN (big at once A with sound)", "PVJ-STANDIN (big at once B with sound)"
BIG_CHANGES = "PVJ-STANDIN (big changes size)"
SOUND_ONLY, SOUND_ONLY_STOPS = "PVJ-STANDIN (sound only)", "PVJ-STANDIN (sound only stops after 12 s)"
SOUND_ONLY_APPEARS = "PVJ-STANDIN (sound only appears 5 s after the first picture)"
FLASHES, SOUND_ONLY_CLICKS = "PVJ-STANDIN (flashes)", "PVJ-STANDIN (sound only clicks 200 ms late)"
STANDIN_AT = {SOUND_ONLY_APPEARS: "10.255.255.1:5985"}      # where the stand-in says a source is that its finder does not report yet
SHOWN = ("path", "idle-active", "core-idle", "pause", "eof-reached", "time-pos", "audio-pts", "track-list/count", "track-list", "video-params",
         "audio-params", "demuxer-cache-state", "paused-for-cache", "seeking", "current-demuxer", "file-format", "mpv-version")


def say(line):
    print(line, file=sys.stderr)
    sys.stderr.flush()


def mpv_version():
    try:
        out = subprocess.run([MPV, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return "(mpv --version: %s)" % e
    return (out.splitlines() or ["(nothing)"])[0]


class ApiRig(mpvword.Rig):
    """The panel's Api asked directly as its owner's device, and the real mpv's socket."""

    def __init__(self, test):
        self.t, self.socket, self.pipe = test, test.real.socket_path, test.service.fifo

    def say(self, line):
        say(line)

    def page(self):
        code, out = self.t.h("GET", "/api/ndi")
        return out if code == 200 and isinstance(out, dict) else {"_http": code, "_said": out}

    def post_play(self, sid):
        return self.t.h("POST", "/api/play", {"ndi": sid})

    def post_sound(self, on):
        return self.t.h("POST", "/api/ndi", {"action": "sound", "on": on})

    def player_status(self):
        code, out = self.t.h("GET", "/api/status")
        return code, (out.get("player") if isinstance(out, dict) else None)

    def post_ndi(self, body):
        return self.t.h("POST", "/api/ndi", body)

    def receivers(self, name, bandwidth):
        # the stand-in's own count, read from the very file this test's helper loaded (the same process)
        return ndi_standin.receivers(self.t.standin, name, bandwidth)

    def choose_absent(self, name):
        # Written into the settings as the panel's own choice writes them (ndi.sound_from and its row among the
        # allowed sources), then told to the helper as after any change. The route cannot be used: it takes a sound
        # source from the list of what is on the network now, and this one is not there yet.
        sid, st = ndi.source_id(name, ndi.host_of(STANDIN_AT[name])), self.t.settings
        with st.lock:
            section = dict(st.data.get("ndi") or {})
            section.setdefault("addresses", [])
            rows = [r for r in section.get("sources", []) if r.get("id") != sid] + [{"id": sid, "name": name, "from": STANDIN_AT[name]}]
            section.update(sources=rows, sound=True, sound_from=sid)
            st.data["ndi"] = ndi.validate_saved(section)
            st.save()
        self.t.api.ndi._wanted()
        self.t.api.ndi.sync()
        if self.page().get("sound_from") != sid:
            raise mpvword.Failed("the settings were written with %r as the sound source and the page does not say so: %s"
                                 % (name, str(self.page().get("sound_from"))))
        self.say("the sound of every NDI picture comes from %r, which is not on the network now (written into the settings)" % name)
        return sid

    def next_clip(self):
        # A clip of this test's own in the media folder, in the form mpv is known to read here: the helper's own
        # Matroska writer, five seconds of a 16 x 16 picture. Played by the panel as any clip is.
        name = "next-clip.mkv"
        with open(os.path.join(self.t.media, name), "wb") as f:
            f.write(ndi.mkv_header(16, 16, 30.0))
            for n in range(150):
                f.write(ndi.mkv_block_head(1, n / 30.0, 512) + bytes(512))
        code, out = self.t.h("POST", "/api/play", {"file": name})
        if code != 200:
            raise mpvword.Failed("POST /api/play for the clip %r answered HTTP %s: %s" % (name, code, str(out)[:300]))

        def has_it():
            m = mpvword.ask(self.socket, "path", "time-pos")
            return m["path"] if isinstance(m["path"], str) and m["path"].endswith(name) and mpvword.number(m["time-pos"]) else None
        path = mpvword.wait(has_it, 10, every=0.2)
        if not path:
            raise mpvword.Failed("mpv did not play the clip %r within 10 seconds: %s" % (name, mpvword.ask(self.socket, "path", "time-pos", "idle-active")))
        return path


@unittest.skipUnless(ASKED, "asked for with PVJ_NDI_MPV_TEST=1 only (CI's job ndi-mpv, on the Pi's mpv): it takes minutes and its answer belongs to one mpv")
class NdiOnARealMpv(ServerBase):
    @classmethod
    def setUpClass(cls):
        if not MPV or not ndi_standin.CC:
            raise AssertionError("PVJ_NDI_MPV_TEST=1 and %s is not here: this run was asked for and cannot say anything"
                                 % ("mpv" if not MPV else "a C compiler"))
        cls.build = tempfile.mkdtemp(prefix="pvj-ndi-mpv-")
        cls.lib = os.path.join(cls.build, ndi.LIB_NAME)
        ndi_standin.build(cls.lib)
        cls.version = mpv_version()
        say("THE MPV OF THIS RUN: %s" % cls.version)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.build, ignore_errors=True)

    def setUp(self):
        super().setUp()
        say("\n=== %s" % self.id().rsplit(".", 1)[-1])
        self.token = self.call("POST", "/api/pair", {"pin": self.pin, "name": "Studio laptop"})[1]["token"]
        self.owner = self.auth.authenticate(self.token)
        # The real player: its own command line (Player.mpv_command, as `pvj-player serve` builds it) and nothing
        # of ours but the outputs and a log.
        self.mpv_log = os.path.join(self.tmp, "mpv.log")
        self.real = Player(extra_args=["--vo=null", "--ao=null", "--log-file=" + self.mpv_log], rundir=self.rundir)
        self.addCleanup(self.real.stop)
        self.api.player = self.real
        self.real._spawn()
        # The helper's real code with the stand-in. A copy of the library for each test: what the stand-in remembers
        # (a source that changed size stays changed) must not pass from one test to the next.
        self.ndidir = tempfile.mkdtemp(prefix="pvj-ndi-")
        self.addCleanup(shutil.rmtree, self.ndidir, True)
        lib = os.path.join(self.ndidir, "lib-" + ndi.LIB_NAME)
        shutil.copy(self.lib, lib)
        self.standin = lib                 # the file the helper loads: the stand-in's count of receivers is read from this one
        self.helper_said = []
        self.service = ndi.Service(self.ndidir, lib, loader=ndi.CtypesLibrary, problem=lambda p: None, log=self.helper_said.append)
        self.addCleanup(self.service.close)
        reg, st = self.api.registry, self.settings
        self.panel_said = []
        self.api.ndi = ndi.Input(FakeClient(self.service), self.service.fifo,
                                 ndi.Wanted(lambda: reg.enabled("inputs-ndi"), lambda: st.data, log=self.panel_said.append),
                                 log=self.panel_said.append)
        self.assertEqual(self.h("POST", "/api/modules/inputs-ndi", {"enabled": True})[0], 200)
        # the panel's once-a-second look at the helper (server.py: ndi_watch), which opens a source again
        self.stop_watch = threading.Event()
        self.addCleanup(self.stop_watch.set)
        threading.Thread(target=self.watch, name="ndi-watch", daemon=True).start()
        self.rig = ApiRig(self)

    def watch(self):
        while not self.stop_watch.wait(1.0):
            try:
                self.api.ndi_tick()
            except Exception as e:
                self.panel_said.append("NDI watch: %s" % e)

    def h(self, method, path, body=None):
        return self.api.handle(method, path, body or {}, self.owner, LAN)

    def told(self):
        """Everything one run needs to say what went wrong."""
        say("--- GET /api/ndi: %s" % self.rig.page())
        say("--- mpv (%s) over its socket:" % self.version)
        for k, v in sorted(mpvword.ask(self.real.socket_path, *SHOWN).items()):
            say("    %s = %r" % (k, v))
        say("--- what the helper wrote: %s" % self.helper_said[-12:])
        say("--- what the panel wrote: %s" % self.panel_said[-12:])
        try:
            with open(self.mpv_log, errors="replace") as f:
                lines = f.read().splitlines()
        except OSError as e:
            lines = ["(no log: %s)" % e]
        say("--- the end of mpv's own log (%d lines in all):" % len(lines))
        for line in lines[-70:]:
            say("    " + line[:300])

    def must(self, case, *args, **kw):
        """A case that passed when it was written: fatal."""
        try:
            case(self.rig, *args, **kw)
        except mpvword.Failed as e:
            self.told()
            self.fail("on %s: %s" % (self.version, e))

    def known(self, words, case, *args, **kw):
        """A case that is known to fail today: a FINDING until the fault is mended, then fatal by the one switch."""
        self.finding(FATAL, "PVJ_NDI_FINDINGS_FATAL", "", words, case, *args, **kw)

    def not_built(self, words, case, *args, **kw):
        """A case for a part of the product that is not built yet: the same FINDING, by a switch of its own (see
        the top of this file for why)."""
        self.finding(NOT_BUILT_FATAL, "PVJ_NDI_NOT_BUILT_FATAL", "NOT BUILT YET, ", words, case, *args, **kw)

    def finding(self, fatal, switch, kind, words, case, *args, **kw):
        try:
            case(self.rig, *args, **kw)
        except mpvword.Failed as e:
            self.told()
            if fatal:
                self.fail("on %s: %s" % (self.version, e))
            say("FINDING, %s(%s): %s. %s" % (kind, self.version, words, e) if kind else "FINDING (%s): %s. %s" % (self.version, words, e))
            self.skipTest("FINDING, %snot fatal while %s is not 'yes': %s: %s" % (kind, switch, words, str(e)[:400]))
        say("GOOD, and it was a known finding (%s): %s%s. If this is the mended code, make findings fatal (%s)." % (self.version, kind, words, switch))

    # ---- the mpv this run is for
    def test_00_the_mpv_is_the_one_this_run_is_for(self):
        if not WANT:
            self.skipTest("no version was asked for (PVJ_NDI_MPV_WANT); this run's mpv is: %s" % self.version)
        self.assertRegex(self.version, r"^mpv v?%s" % WANT.replace(".", r"\."),
                         "this job is for the Pi's mpv; if Debian moved on, so will the Pi at its next upgrade: look at both")

    # ---- a source by itself, long enough to mean something
    def test_10_a_moving_source_sound_off(self):
        def case(rig):
            rig.start(MOVING, "raw")
            rig.watch("a moving source, sound off", MOVING, seconds=12, audio=False)
        self.must(case)

    def test_11_a_moving_source_with_its_sound(self):
        def case(rig):
            rig.sound("on")
            rig.start(SOUND, "matroska")
            rig.watch("a moving source with its sound", SOUND, seconds=12, audio=True)
        self.must(case)

    def test_12_a_big_moving_source_with_its_sound(self):
        def case(rig):
            rig.sound("on")
            rig.start(BIG_SOUND, "matroska")
            rig.watch("a 1280 x 720 source with its sound", BIG_SOUND, seconds=12, audio=True)
        self.must(case)

    def test_13_one_still_picture_then_only_sound_for_thirty_five_seconds(self):
        # The Pi's failure (Test Patterns with sound on): the pipe is mpv's path and it never opens it. If this is
        # GOOD on the Pi's mpv with code that is stuck on the Pi, this job does not match the Pi yet.
        self.known("one still picture then only sound, with sound on", mpvword.case_still, STILL, seconds=35)

    def test_14_a_moving_source_with_sound_whose_picture_stops(self):
        # STUCK on mpv 0.40 with master's pvj (the first run of this job, 2026-10-12): when the picture stopped, mpv
        # paused for its cache and the sound stopped with it (audio-pts stood at 4.88 for 30 seconds).
        self.known("a moving source with sound whose picture stopped", mpvword.case_goes_still, THEN_STILL, seconds=30)

    # Sound that comes clearly AFTER the first picture, as a real sender's does (the Pi, 2026-10-12: 11 ms, and 4.7 s
    # after a switch of the sound). There mpv 0.40 did not start the sound at all: it waits until the picture has
    # reached the first sound's time, and a still picture never does (cured by initial-audio-sync=no). These are
    # judged by audio-pts at real time, never by time-pos. CI's job runs them a second time on origin/master's pvj,
    # where they must FAIL: a case that passes on the code that was stuck on the Pi judges nothing.
    def test_15_one_still_picture_then_sound_after_a_short_gap(self):
        self.known("one still picture then sound that comes 0.8 s after it", mpvword.case_still, STILL_SHORT_GAP, seconds=35)

    def test_16_one_still_picture_then_sound_after_a_long_gap(self):
        self.known("one still picture then sound that comes 6 s after it", mpvword.case_still, STILL_LONG_GAP, seconds=40, late=14)

    def test_17_a_moving_source_whose_picture_stops_with_sound_after_a_short_gap(self):
        self.known("a moving source whose picture stopped, its sound after a short gap", mpvword.case_goes_still, THEN_STILL_GAP, seconds=30)

    # ---- sound against picture on a moving source, by mpv's own account (mpvword: the bounds and what they do not show)
    def test_30_sync_a_moving_source_with_its_sound(self):
        self.must(mpvword.case_sync, SOUND)

    def test_31_sync_first_sound_late_by_a_fifth_of_a_second(self):
        # on the code before the sound track began at 0 this gave avsync 0.206 for 1.8 seconds (measured 2026-10-12)
        self.must(mpvword.case_sync, "PVJ-STANDIN (moving with sound late by 0.2 s)")

    def test_32_sync_first_sound_late_by_eight_tenths_of_a_second(self):
        self.must(mpvword.case_sync, "PVJ-STANDIN (moving with sound late by 0.8 s)")

    def test_33_sync_first_sound_late_by_five_seconds(self):
        self.must(mpvword.case_sync, "PVJ-STANDIN (moving with sound late by 5 s)")

    def test_34_sync_after_a_re_open_at_another_size(self):
        self.must(mpvword.case_sync_resized, BIG_CHANGES_SOUND)

    def test_35_sync_across_a_pause_of_twenty_seconds(self):
        self.must(mpvword.case_sync_paused, "PVJ-STANDIN (moving with sound pauses 20 s)")

    # ---- the hand-overs
    def test_20_the_same_source_twice_in_a_row(self):
        self.must(mpvword.case_twice, STILL)

    def test_21_a_then_b_at_once_both_with_sound(self):
        # a SILENT LOSS on mpv 0.37 and on 0.40 with master's pvj (2026-10-12)
        def case(rig):
            rig.sound("on")
            mpvword.case_follow(rig, BIG_A_SOUND, BIG_B_SOUND)
        self.known("one source with sound straight after another with sound", case)

    def test_22_sound_switched_on_and_off_under_a_big_source(self):
        self.must(mpvword.case_sound, BIG_SOUND)

    def test_23_sound_switched_off_and_on_under_a_big_source_that_played_with_sound(self):
        self.must(mpvword.case_soundon, BIG_SOUND)

    def test_24_a_source_with_sound_that_changes_size(self):
        def case(rig):
            rig.sound("on")
            mpvword.case_size(rig, BIG_CHANGES_SOUND)
        self.must(case)

    def test_25_the_chain_with_nothing_cleaned_between_its_links(self):
        # Two of its links failed on mpv 0.40 with master's pvj (2026-10-12): the still one was the Pi's own picture
        # of the fault (the pipe as mpv's path, track-list/count 0), and the source after it was lost without a word.
        self.known("the chain of hand-overs with nothing cleaned between them", mpvword.case_chain)

    # ---- sound from another sender (D87): the picture of one source, the sound of another. What each case shows and
    # what none of them can is in pvj/NDI.md ("Sound from another sender: what the tests show").
    def test_40_the_picture_of_one_source_with_the_sound_of_a_sound_only_source(self):
        self.must(mpvword.case_sound_from, MOVING, SOUND_ONLY, seconds=30)

    def test_41_the_picture_changes_size_and_the_sound_connection_is_opened_once(self):
        self.must(mpvword.case_sound_from_resized, BIG_CHANGES, SOUND_ONLY)

    def test_42_the_sound_source_is_away_at_the_press_and_comes_five_seconds_later(self):
        self.must(mpvword.case_sound_from_late, MOVING, SOUND_ONLY_APPEARS)

    def test_43_the_sound_source_stops_and_the_picture_goes_on(self):
        self.must(mpvword.case_sound_from_stops, MOVING, SOUND_ONLY_STOPS)

    def test_44_sound_that_is_200_ms_late_by_the_stand_in_is_in_sync_by_mpvs_own_word(self):
        self.must(mpvword.case_sound_offset_unseen, FLASHES, SOUND_ONLY_CLICKS)

    def test_45_the_sound_delay_is_mpvs_audio_delay_for_the_ndi_load_and_is_taken_back_on_the_next_clip(self):
        # NOT BUILT YET when this was written (2026-10-12): ndi.sound_delay_ms is stored, checked and sent to the
        # helper, and nothing sets mpv's audio-delay (pvj/NDI.md, "The delay").
        self.not_built("ndi.sound_delay_ms is applied as mpv's audio-delay for the NDI load and taken back on the next clip",
                       mpvword.case_sound_delay, FLASHES, SOUND_ONLY_CLICKS)

    def test_46_play_on_the_sound_only_source_is_refused_in_plain_words(self):
        self.must(mpvword.case_sound_only_play, SOUND_ONLY)

    def test_47_play_on_the_sound_only_source_while_a_picture_plays_with_its_sound(self):
        # For the record: not one of the cases this job was asked to judge, and never fatal. What it prints says
        # what became of the picture that played before the refused press.
        self.finding(False, "(no switch: for the record)", "FOR THE RECORD, ", "after a refused Play on the sound-only source, the picture before it plays or has a reason",
                     mpvword.case_sound_only_play_under_a_picture, MOVING, SOUND_ONLY)


if __name__ == "__main__":
    unittest.main()
