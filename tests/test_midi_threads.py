# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The two threads of a MIDI input (one reads and stamps, one hands on), through real threads on a pipe.

From the second review of the controller code (D61). Where a finding is about the threads, its test runs them:
handing a time or a marker to the hub by hand cannot see an ordering between the two."""
import os
import threading
import time
import unittest

from pvj import auth as auth_mod, midi
from tests import test_controller_code as base

NOTE_JOIN, PAD = base.NOTE_JOIN, base.PAD
NOTE_OTHER = 99                 # mapped to nothing


def until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


def midi_threads():
    return [t for t in threading.enumerate() if t.name in ("midi", "midi-work") and t.is_alive()]


class InputBase(unittest.TestCase):
    def setUp(self):
        self.before = set(midi_threads())
        self.said = []

    def make(self, handler, **more):
        r, w = os.pipe()
        inp = midi.MidiInput("/dev/snd/midiC9D0", "Mini", handler, log=self.said.append, open_fn=lambda p: r, clock=time.monotonic, **more)
        inp.start()
        self.addCleanup(inp.stop)
        return inp, w

    def ours(self):
        return [t for t in midi_threads() if t not in self.before]


class WorkerTest(InputBase):
    """1. One bad message must not leave a controller deaf."""

    def test_a_handler_that_raises_once_still_gets_the_next_message(self):
        got = []

        def handler(source, msg, at=None):
            if msg is None:
                return
            got.append(msg[2])
            if len(got) == 1:
                raise OSError(28, "No space left on device")          # a save that failed, somewhere under the API
        inp, w = self.make(handler)
        os.write(w, bytes([0x90, 1, 127]))
        self.assertTrue(until(lambda: got == [1]))
        os.write(w, bytes([0x90, 2, 127]))
        self.assertTrue(until(lambda: got == [1, 2]), got)
        self.assertTrue(inp.alive)
        self.assertEqual(len([line for line in self.said if "OSError" in line]), 1, self.said)
        os.close(w)

    def test_many_bad_messages_fill_no_log(self):
        got = []

        def handler(source, msg, at=None):
            if msg is not None:
                got.append(msg[2])
                raise ValueError("bug")
        inp, w = self.make(handler)
        for n in range(50):
            os.write(w, bytes([0x90, n, 127]))
        self.assertTrue(until(lambda: len(got) == 50), len(got))
        self.assertLessEqual(len(self.said), 2, self.said)             # said once (and again only after a while)
        os.close(w)

    def test_an_input_whose_worker_is_gone_is_not_alive(self):
        def handler(source, msg, at=None):
            if msg is not None:
                raise SystemExit                                      # not an Exception: the thread ends
        inp, w = self.make(handler)
        self.assertTrue(inp.alive)
        os.write(w, bytes([0x90, 1, 127]))
        self.assertTrue(until(lambda: not inp._worker.is_alive()))
        self.assertTrue(inp._thread.is_alive())                       # the reader is still there
        self.assertFalse(inp.alive)
        os.close(w)

    def test_ticks_come_with_a_time_too(self):
        ticks = []
        inp, w = self.make(lambda source, msg, at=None: ticks.append(at) if msg is None else None)
        self.assertTrue(until(lambda: len(ticks) >= 2))
        self.assertTrue(all(isinstance(t, float) for t in ticks), ticks)
        os.close(w)


class OrderTest(InputBase):
    def test_a_fast_sweep_arrives_whole_and_in_order(self):
        got, sent = [], [(n * 7) % 128 for n in range(1500)]

        def handler(source, msg, at=None):
            if msg is not None:
                got.append((msg[3], at))
        inp, w = self.make(handler)
        for start in range(0, len(sent), 60):
            os.write(w, b"".join(bytes([0xB0, 20, v]) for v in sent[start:start + 60]))
        self.assertTrue(until(lambda: len(got) == len(sent)), len(got))
        self.assertEqual([g[0] for g in got], sent)
        self.assertEqual([g[1] for g in got], sorted(g[1] for g in got))      # and the read times never go back
        self.assertEqual(self.said, [])
        os.close(w)

    def test_stop_unplug_and_start_again_leave_no_thread_behind(self):
        inp, w = self.make(lambda source, msg, at=None: None)
        self.assertEqual(len(self.ours()), 2)
        inp.stop()
        self.assertEqual(self.ours(), [])
        os.close(w)
        inp2, w2 = self.make(lambda source, msg, at=None: None)       # plugged in again
        self.assertEqual(len(self.ours()), 2)
        os.close(w2)                                                  # unplugged: both threads end by themselves
        self.assertTrue(until(lambda: self.ours() == []), self.ours())
        self.assertFalse(inp2.alive)


class StopTest(InputBase):
    """5. Stopping several inputs whose handlers are stuck waits once, not once per thread."""

    def test_three_stuck_inputs_stop_in_about_one_wait(self):
        stuck, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def handler(source, msg, at=None):
            if msg is not None:
                stuck.set()
                release.wait(20)                                      # a player call that does not come back
        made = [self.make(handler) for _ in range(3)]
        for inp, w in made:
            os.write(w, bytes([0x90, 1, 127]))
        self.assertTrue(until(lambda: all(i.messages == 1 for i, _ in made)))
        began = time.monotonic()
        midi.stop_inputs([i for i, _ in made], timeout=0.6)
        took = time.monotonic() - began
        self.assertLess(took, 1.2, took)                              # one wait of 0.6 s, not three or six
        self.assertGreater(took, 0.5)
        release.set()
        for _, w in made:
            os.close(w)


class RealHub(base.BoxBase):
    """A hub that reads a pipe through its own two-thread input, with the fake clock under everything."""
    QUEUE = 4

    def setUp(self):
        super().setUp()
        self.on()
        self.hub.stop()
        self.pipes = {}

        def open_fn(path):
            r, w = os.pipe()
            self.pipes[path] = w
            return r
        self.real = midi.MidiHub(self.api, self.settings, log=self.said.append, open_fn=open_fn, lister=lambda: [PAD], namer=lambda p: "Mini",
                                 clock=lambda: self.t[0], describer=lambda p: self.cards[p], profiles=[], light_open_fn=self.no_lights)
        self.real.queue_max = self.QUEUE
        self.api.midi = self.real
        self.addCleanup(self.real.stop)
        self.sent = self.handled = self.dropped = 0
        inner = self.real.on_message

        def counted(source, msg, at=None):                            # so a test knows when a message has been handled to the end
            try:
                return inner(source, msg, at)
            finally:
                if msg is not None and msg != getattr(midi, "LOST", "lost"):
                    self.handled += 1
        self.real.on_message = counted
        self.real.scan()
        self.assertTrue(until(lambda: PAD in self.pipes and self.real.inputs[PAD].connected))
        self.inp = self.real.inputs[PAD]

    @staticmethod
    def no_lights(path):
        raise OSError("no lights in this test")

    def write(self, *data):
        self.sent += 1
        os.write(self.pipes[PAD], bytes(data))

    def drained(self):
        """Everything written so far was read and handled to the end (but for what the test knows was dropped)."""
        return until(lambda: self.handled == self.sent - self.dropped and self.inp._queue.empty())


class LostInOrder(RealHub):
    """2. "Messages were lost" has to be handled where the loss happened, not before what was queued earlier."""

    def test_the_reviewers_sequence_with_a_queue_that_really_fills(self):
        self.assertEqual(self.inp._queue.maxsize, self.QUEUE)
        self.t[0] = 1000.0
        with self.real._lock:                                         # the worker is busy: it has one message and waits
            self.write(0x90, NOTE_OTHER, 1)
            self.assertTrue(until(lambda: self.inp.messages == 1))
            self.write(0x90, NOTE_JOIN, 127)                          # the press, read at 1000.0, queued
            self.assertTrue(until(lambda: self.inp._queue.qsize() == 1))
            for n in range(self.QUEUE - 1):                           # the queue fills
                self.write(0x90, NOTE_OTHER, 1)
            self.assertTrue(until(lambda: self.inp._queue.full()))
            self.write(0x80, NOTE_JOIN, 0)                            # the release: dropped
            self.dropped += 1
            self.assertTrue(until(lambda: self.inp._lost))
        self.assertTrue(self.drained())
        self.t[0] = 1005.1
        self.write(0x90, NOTE_JOIN, 127)                              # a short tap, five seconds later
        self.assertTrue(self.drained())
        self.t[0] = 1005.2
        self.write(0x80, NOTE_JOIN, 0)
        self.assertTrue(self.drained())
        time.sleep(0.05)
        self.assertIsNone(self.digits())
        self.assertEqual(self.state()["controller"]["status"]["made_this_hour"], 0)
        self.t[0] = 1010.0                                            # and a real hold afterwards is a hold
        self.write(0x90, NOTE_JOIN, 127)
        self.assertTrue(self.drained())
        self.t[0] = 1013.5
        self.write(0x80, NOTE_JOIN, 0)
        self.assertTrue(until(lambda: self.digits() is not None))

    def test_a_real_hold_through_the_two_threads(self):
        self.t[0] = 2000.0
        self.write(0x90, NOTE_JOIN, 127)
        self.assertTrue(self.drained())
        self.t[0] = 2000.1
        self.write(0x80, NOTE_JOIN, 0)                                # a tap
        self.assertTrue(self.drained())
        time.sleep(0.05)
        self.assertIsNone(self.digits())
        self.write(0x90, NOTE_JOIN, 127)
        self.assertTrue(self.drained())
        self.t[0] = 2003.6
        self.write(0x80, NOTE_JOIN, 0)
        self.assertTrue(until(lambda: self.digits() is not None))


class HubAndWorker(RealHub):
    def test_a_message_that_raises_in_the_hub_does_not_deafen_the_controller(self):
        real, state = self.api.handle, {"n": 0}
        self.settings.data["control"]["midi"]["map"].append(midi.validate_entry({"kind": "note", "number": 61, "action": "stop"}))

        def handle(method, path, body, device, client):
            if device is midi.MIDI_DEVICE:
                state["n"] += 1
                if state["n"] == 1:
                    raise OSError(28, "No space left on device")      # Api.handle catches ApiError only
            return real(method, path, body, device, client)
        self.api.handle = handle
        self.write(0x90, 61, 127)
        self.assertTrue(until(lambda: state["n"] == 1))
        self.write(0x80, 61, 0)
        self.t[0] += 1
        self.write(0x90, 61, 127)
        self.assertTrue(until(lambda: state["n"] == 2), state)
        self.assertTrue(self.inp.alive)

    def test_scan_replaces_an_input_whose_worker_is_gone(self):
        first = self.inp
        first.on_message = lambda source, msg, at=None: (_ for _ in ()).throw(SystemExit) if msg is not None else None
        self.write(0x90, NOTE_OTHER, 1)
        self.assertTrue(until(lambda: not first._worker.is_alive()))
        self.assertFalse(first.alive)
        self.real.scan()
        self.assertTrue(until(lambda: self.real.inputs.get(PAD) is not None and self.real.inputs[PAD] is not first))
        self.assertTrue(until(lambda: self.real.inputs[PAD].alive))


class TickFlush(base.BoxBase):
    """4. A slow flush of a fader, on the tick of an input that stamps its messages, does not end a real hold."""

    def test_a_slow_flush_on_a_stamped_tick_keeps_the_hold(self):
        self.on()
        self.settings.data["control"]["midi"]["map"].append(midi.validate_entry({"kind": "cc", "number": 20, "action": "opacity"}))
        real = self.api.handle

        def slow(method, path, body, device, client):
            if path == "/api/control" and self.flushing:
                self.t[0] += 3.2
            return real(method, path, body, device, client)
        self.flushing = False
        self.api.handle = slow
        t0 = self.t[0]
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127), t0)
        self.hub.on_message("Mini", ("cc", 0, 20, 10), t0 + 0.2)
        self.hub.on_message("Mini", ("cc", 0, 20, 90), t0 + 0.2)       # held back: the newest value of a sweep
        self.assertTrue(self.hub.mapper.pending)
        self.t[0] = t0 + 0.3
        self.flushing = True
        self.hub.on_message("Mini", None, t0 + 0.3)                    # the tick of a stamped input: the flush takes 3.2 s
        self.assertGreater(self.t[0] - t0, 3.0)
        self.assertFalse(self.hub.mapper.pending)
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0), t0 + 3.6)
        self.assertIsNotNone(self.digits())

    def test_an_unstamped_slow_flush_still_ends_it(self):
        self.on()
        self.settings.data["control"]["midi"]["map"].append(midi.validate_entry({"kind": "cc", "number": 20, "action": "opacity"}))
        real = self.api.handle

        def slow(method, path, body, device, client):
            if path == "/api/control" and self.flushing:
                self.t[0] += 3.2
            return real(method, path, body, device, client)
        self.flushing = False
        self.api.handle = slow
        self.hub.on_message("Mini", ("on", 0, NOTE_JOIN, 127))
        self.hub.on_message("Mini", ("cc", 0, 20, 10))
        self.hub.on_message("Mini", ("cc", 0, 20, 90))
        self.t[0] += 0.1
        self.flushing = True
        self.hub.on_message("Mini", None)
        self.hub.on_message("Mini", ("off", 0, NOTE_JOIN, 0))
        self.assertIsNone(self.digits())


class Reserve(unittest.TestCase):
    """3. The places kept for the PIN hold in every order of pairing."""

    def setUp(self):
        import tempfile
        from pvj.settings import Settings
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        base.switch(self.settings, owner=True)
        self.t = [1000.0]
        self.a = auth_mod.Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)
        self.room = auth_mod.MAX_DEVICES - auth_mod.FULL_RESERVED

    def add(self, count, role, via=None):
        have = len(self.settings.data["devices"])
        for n in range(count):
            d = {"id": "%08x" % (have + n), "name": "d", "role": role, "token_hash": "%064x" % (have + n), "created": 1_700_000_000}
            if via:
                d["via"] = via
            self.settings.data["devices"].append(d)

    def pin_pairs_every_reserved_place(self):
        for n in range(auth_mod.FULL_RESERVED):
            self.assertEqual(self.a.pair(self.a.current_pin, "owner %d" % n, "pin%d" % n)[1]["role"], "full")

    def test_owner_codes_first_then_guests(self):
        self.add(auth_mod.FULL_RESERVED, "full", "controller")        # twenty full access codes were paired
        self.add(self.room - auth_mod.FULL_RESERVED, "live")          # then others, up to what is left of the shared places
        for role in ("view", "live"):
            with self.assertRaises(auth_mod.TooManyDevices):
                self.a.invite("one more", role)
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.create_controller_code("join")
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.create_controller_code("owner")
        self.assertEqual(len(self.settings.data["devices"]), self.room)
        self.pin_pairs_every_reserved_place()

    def test_guests_first_then_owner_codes(self):
        self.add(self.room, "live")
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.create_controller_code("owner")
        self.pin_pairs_every_reserved_place()

    def test_pin_devices_do_not_count_against_the_shared_places(self):
        self.add(auth_mod.FULL_RESERVED, "full")                      # paired with the PIN
        self.add(self.room - 1, "live")
        self.a.create_controller_code("owner")                        # the last shared place is still there
        self.assertEqual(self.a.pair(self.a.controller_digits()[1], "x", "c")[1]["role"], "full")
        with self.assertRaises(auth_mod.TooManyDevices):
            self.a.invite("one more", "view")


if __name__ == "__main__":
    unittest.main()
