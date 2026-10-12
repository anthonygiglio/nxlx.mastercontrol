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

The known fault uses the same convention and the same switch as the real-install test: while
PVJ_NDI_FINDINGS_FATAL is not "yes", a case that is known to fail today prints a line beginning FINDING and is
counted as skipped, with the reason; set to "yes", it fails. A case that passed when it was written is fatal from
the start."""
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
WANT = os.environ.get("PVJ_NDI_MPV_WANT", "")      # the start of the version this run is for, such as "0.40."
MPV = shutil.which("mpv")

MOVING, SOUND, STILL = "PVJ-STANDIN (moving)", "PVJ-STANDIN (moving with sound)", "PVJ-STANDIN (one still then sound)"
THEN_STILL = "PVJ-STANDIN (moving then still with sound)"
BIG_SOUND, BIG_CHANGES_SOUND = "PVJ-STANDIN (big moving with sound)", "PVJ-STANDIN (big changes size with sound)"
BIG_A_SOUND, BIG_B_SOUND = "PVJ-STANDIN (big at once A with sound)", "PVJ-STANDIN (big at once B with sound)"
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
        try:
            case(self.rig, *args, **kw)
        except mpvword.Failed as e:
            self.told()
            if FATAL:
                self.fail("on %s: %s" % (self.version, e))
            say("FINDING (%s): %s. %s" % (self.version, words, e))
            self.skipTest("FINDING, not fatal while PVJ_NDI_FINDINGS_FATAL is not 'yes': %s: %s" % (words, str(e)[:400]))
        say("GOOD, and it was a known finding (%s): %s. If this is the mended code, make findings fatal." % (self.version, words))

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
        # Not known to fail and not known to pass when it was written (no mpv on the machine it was written on); of
        # the same kind as the one above (the picture ends, the sound goes on), so a finding until a run was read.
        self.known("a moving source with sound whose picture stopped", mpvword.case_goes_still, THEN_STILL, seconds=30)

    # ---- the hand-overs
    def test_20_the_same_source_twice_in_a_row(self):
        self.must(mpvword.case_twice, STILL)

    def test_21_a_then_b_at_once_both_with_sound(self):
        # a SILENT LOSS on the real-install job (mpv 0.37) on 2026-10-12
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
        # holds the still link and what follows it: two of its links failed on the real-install job on 2026-10-12
        self.known("the chain of hand-overs with nothing cleaned between them", mpvword.case_chain)


if __name__ == "__main__":
    unittest.main()
