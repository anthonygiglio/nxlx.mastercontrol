# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What a node keeps and decides about the nodes it trusts (D132, slice 2; pvj/handover.py): the signed list checked
against the pinned root and stored beside tls/, acceptance by a fingerprint typed back, the deny file, the switch,
the card, and the one question "is this node approved". Every root is a throwaway; nothing here calls another node."""
import copy
import json
import os
import shutil
import stat
import tempfile
import unittest

import tests  # noqa: F401  (the tests package, as every test module imports it: tests/test_lockrank.py)
from pvj import fleetlist as FL, handover as hv
from pvj.settings import Settings
from tests.test_boxcert import boxcert, make_request, openssls
from tests.test_fleetlist import FleetListBase, PASS


def slurp(path, mode="r"):
    with open(path, mode) as f:
        return f.read()

FLEET = "0123456789abcdef"
ME = "a" * 32
PEER = {"id": "b" * 32, "name": "leyline", "key": "2" * 64, "carry": "live"}
THIRD = {"id": "c" * 32, "name": "third", "key": "3" * 64, "carry": "none"}


class StubHttps:
    """Only what Handover asks of the HttpsBox: the pinned root and the certificate in use."""

    def __init__(self, root, cert):
        self.root, self.cert = root, cert

    def root_pem(self):
        return self.root

    def cert_pem(self):
        return self.cert


class Events:
    def __init__(self):
        self.lines = []

    def __call__(self, kind, text, who=None):
        self.lines.append((kind, text, who))


class HandoverBase(FleetListBase):
    def setUp(self):
        self.state = os.path.join(self.tmp, "state-%s" % self._testMethodName[:40])
        os.makedirs(self.state)
        self.settings = Settings(os.path.join(self.state, "settings.json"))
        self.settings.load()
        self.events = Events()
        self.key, self.cert = self.node_cert()
        self.me = self.make(self.cert)
        self.my_key = FL.key_hash(FL.certificate_der(self.cert))

    def node_cert(self, which="one", name="node-01.local"):
        work = self.workdir()
        key, csr = make_request(self.exe, work, [name], [], cn=name)
        out = os.path.join(work, "cert.pem")
        boxcert.sign(self.exe, self.roots[which][2], csr, [], [], [], 30, out, lambda p: PASS, out=lambda *a: None)
        return key, slurp(out)

    def make(self, cert, root=None, state=None):
        h = hv.Handover(state or self.state, StubHttps(self.roots["one"][1] if root is None else root, cert), self.settings,
                        lambda: {"id": ME, "name": "node-01"}, event=self.events, openssl=self.exe)
        return h

    def data(self, serial=1, fleet=FLEET, nodes=None, **more):
        mine = {"id": ME, "name": "node-01", "key": self.my_key, "carry": "full"}
        d = {"v": 1, "fleet": fleet, "name": "Rig", "serial": serial, "issued": "2026-11-09",
             "nodes": copy.deepcopy(nodes if nodes is not None else [mine, PEER])}
        d.update(more)
        return d

    def pair(self, serial=1, which="one", **kw):
        body = FL.canonical(self.data(serial, **kw))
        return body, self.sign_message(FL.signed_bytes(FLEET if "fleet" not in kw else kw["fleet"], serial, body), which)

    def files(self):
        return sorted(os.listdir(self.state))


class Storing(HandoverBase):
    def test_a_list_signed_by_the_pinned_root_is_taken_and_kept_beside_tls(self):
        body, sig = self.pair(3)
        status = self.me.take_list(body, sig, "Anthony iPad")
        self.assertEqual((status["list"]["serial"], status["list"]["fleet"]), (3, FLEET))
        self.assertEqual([n["name"] for n in status["list"]["nodes"]], ["leyline", "node-01"])
        self.assertEqual([n for n in self.files() if n != "settings.json"], ["fleet.json", "fleet.sig"])
        self.assertEqual(slurp(os.path.join(self.state, "fleet.json"), "rb"), body)
        self.assertEqual(slurp(os.path.join(self.state, "fleet.sig")).split(), sig.split())
        for name in ("fleet.json", "fleet.sig"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, name)).st_mode), 0o640)
        self.assertEqual([n for n in os.listdir(self.state) if n.endswith(".tmp") or n.endswith(".new")], [])
        self.assertNotIn("fleet.json", json.dumps(self.settings.data))
        self.assertEqual(self.events.lines, [("handover", "The fleet list (serial 3, 2 nodes) was loaded.", "Anthony iPad")])
        # told again with the same bytes: no new event, no rewrite
        self.me.take_list(body, sig, "Anthony iPad")
        self.assertEqual(len(self.events.lines), 1)

    def test_a_new_start_reads_the_files_again_and_checks_them(self):
        body, sig = self.pair(2)
        self.me.take_list(body, sig)
        again = self.make(self.cert)
        again.load()
        self.assertEqual(again.list.serial, 2)
        self.assertIsNone(again.problem)

    def test_files_that_do_not_check_out_are_not_in_use_and_say_why(self):
        body, sig = self.pair(2)
        self.me.take_list(body, sig)
        path = os.path.join(self.state, "fleet.json")
        with open(path, "wb") as f:
            f.write(body.replace(b"leyline", b"leylinX"))
        cold = self.make(self.cert)
        cold.load()
        self.assertIsNone(cold.list)
        self.assertIn("not signed by this node's root", cold.problem)
        os.unlink(os.path.join(self.state, "fleet.sig"))
        cold.load()
        self.assertIn("without its signature", cold.problem)
        self.assertIsNone(cold.approved(PEER["id"], PEER["key"]))

    def test_a_crash_between_the_two_renames_leaves_the_old_pair_to_fall_back_on(self):
        old = self.pair(1)
        new = self.pair(2)
        self.me.take_list(*old)
        self.me.take_list(*new)
        self.assertEqual(self.me.list.serial, 2)
        # the new signature is in place and the new list is not (the crash): the pair does not match
        shutil.copy(os.path.join(self.state, "fleet.json.prev"), os.path.join(self.state, "fleet.json"))
        cold = self.make(self.cert)
        cold.load()
        self.assertEqual(cold.list.serial, 1)               # the previous pair, which does match

    def test_the_list_is_checked_against_the_pinned_root_and_a_stranger_is_refused(self):
        body, sig = self.pair(1, which="two")
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.take_list(body, sig)
        self.assertIn("not signed by this node's root", cm.exception.message)
        self.assertEqual([n for n in self.files() if n != "settings.json"], [])
        self.assertIsNone(self.me.list)
        self.assertEqual(self.events.lines, [])

    def test_a_node_with_no_root_or_no_certificate_says_so_and_stores_nothing(self):
        body, sig = self.pair(1)
        for h, word in ((self.make(self.cert, root=""), "no root certificate"), (self.make(""), "no certificate yet")):
            with self.assertRaises(hv.HandoverError) as cm:
                h.take_list(body, sig)
            self.assertIn(word, cm.exception.message)
        self.assertFalse(os.path.exists(os.path.join(self.state, "fleet.json")))

    def test_a_list_that_does_not_name_this_node_is_refused(self):
        body, sig = self.pair(1, nodes=[PEER, THIRD])
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.take_list(body, sig)
        self.assertIn("does not name this node", cm.exception.message)
        self.assertFalse(os.path.exists(os.path.join(self.state, "fleet.json")))

    def test_a_list_that_names_this_node_with_another_key_is_refused(self):
        mine = {"id": ME, "name": "node-01", "key": "9" * 64, "carry": "full"}
        body, sig = self.pair(1, nodes=[mine, PEER])
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.take_list(body, sig)
        self.assertIn("another key", cm.exception.message)
        self.assertFalse(os.path.exists(os.path.join(self.state, "fleet.json")))

    def test_serials_only_go_up_and_a_refused_list_changes_nothing(self):
        self.me.take_list(*self.pair(5))
        kept = {n: slurp(os.path.join(self.state, n), "rb") for n in ("fleet.json", "fleet.sig")}
        for serial, word in ((4, "older"), (1, "older")):
            with self.assertRaises(hv.HandoverError) as cm:
                self.me.take_list(*self.pair(serial))
            self.assertIn(word, cm.exception.message)
        other_contents = self.pair(5, nodes=[{"id": ME, "name": "node-01", "key": self.my_key, "carry": "live"}, PEER])
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.take_list(*other_contents)
        self.assertIn("same serial", cm.exception.message)
        self.assertEqual({n: slurp(os.path.join(self.state, n), "rb") for n in kept}, kept)
        self.assertEqual(self.me.list.serial, 5)
        self.me.take_list(*self.pair(6))
        self.assertEqual(self.me.list.serial, 6)
        self.assertEqual(slurp(os.path.join(self.state, "fleet.json"), "rb"), self.pair(6)[0])

    def test_a_list_of_another_fleet_is_refused_once_one_is_held(self):
        self.me.take_list(*self.pair(2))
        body = FL.canonical(self.data(9, fleet="fedcba9876543210"))
        sig = self.sign_message(FL.signed_bytes("fedcba9876543210", 9, body))
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.take_list(body, sig)
        self.assertIn("another fleet", cm.exception.message)
        self.assertEqual(self.me.list.fleet, FLEET)

    def test_a_list_that_is_changed_or_badly_shaped_is_refused_before_anything_is_written(self):
        body, sig = self.pair(1)
        for bad in (body.replace(b"full", b"live"), b"{}", b"", body + b"x"):
            with self.assertRaises(hv.HandoverError):
                self.me.take_list(bad, sig)
        with self.assertRaises(hv.HandoverError):
            self.me.take_list(body, "!!!")
        self.assertFalse(os.path.exists(os.path.join(self.state, "fleet.json")))

    def test_a_wipe_removes_every_file_and_forgets_everything(self):
        self.me.take_list(*self.pair(1))
        self.me.take_list(*self.pair(2))                        # leaves a .prev
        self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "live", hv.fingerprint(THIRD["key"]))
        self.me.deny_add("4" * 64, "gone", "node-01")
        for name in ("fleet.json", "fleet.json.prev", "fleet.sig", "fleet.sig.prev", "pairings.json", "deny.json"):
            self.assertTrue(os.path.exists(os.path.join(self.state, name)), name)
        self.assertEqual(self.me.wipe(), [])
        self.assertEqual([n for n in self.files() if n != "settings.json"], [])
        self.assertIsNone(self.me.list)
        self.assertIsNone(self.me.approved(PEER["id"], PEER["key"]))
        self.assertEqual(self.me.status()["accepted"], [])


class Accepting(HandoverBase):
    def accept(self, node=THIRD, carry="live", typed=None, h=None):
        return (h or self.me).accept_node(node["id"], node["name"], node["key"], carry, hv.fingerprint(node["key"]) if typed is None else typed)

    def test_an_acceptance_needs_the_fingerprint_typed_back(self):
        for typed in (None, "", "3F:9A", "3" * 63, "4" * 64, hv.fingerprint(THIRD["key"])[:-5], 5, ["3" * 64]):
            with self.assertRaises(hv.HandoverError, msg=repr(typed)) as cm:
                self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "live", typed)
            self.assertIn("fingerprint typed does not match", cm.exception.message)
        self.assertFalse(os.path.exists(os.path.join(self.state, "pairings.json")))
        self.assertEqual(self.events.lines, [])
        status = self.accept()
        self.assertEqual(status["accepted"], [{"id": THIRD["id"], "name": "third", "carry": "live"}])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, "pairings.json")).st_mode), 0o640)
        self.assertEqual(self.events.lines[0][:2], ("handover", "third was accepted by its key fingerprint (carry live)."))

    def test_how_the_fingerprint_is_typed_does_not_matter(self):
        key = "3f9a" * 16
        for typed in (key, key.upper(), " ".join(key[i:i + 4] for i in range(0, 64, 4)), ":".join(key[i:i + 2] for i in range(0, 64, 2)).upper(), "-".join(key[i:i + 8] for i in range(0, 64, 8))):
            self.assertTrue(hv.same_fingerprint(typed, key), typed)
        self.assertFalse(hv.same_fingerprint(key[:-1] + "0", key))
        self.assertEqual(hv.fingerprint("ab" * 32), " ".join(["abab"] * 16))

    def test_an_acceptance_is_kept_and_read_again_at_start(self):
        self.accept()
        again = self.make(self.cert)
        again.load()
        self.assertEqual(again.approved(THIRD["id"], THIRD["key"]), {"via": "pairing", "name": "third", "carry": "live"})

    def test_what_may_not_be_accepted(self):
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.accept_node(ME, "node-01", "5" * 64, "live", "5" * 64)
        self.assertIn("this node itself", cm.exception.message)
        with self.assertRaises(hv.HandoverError):
            self.me.accept_node("d" * 32, "twin", self.my_key, "live", self.my_key)
        for node, carry in ((dict(THIRD, id="short"), "live"), (dict(THIRD, name="a b"), "live"), (dict(THIRD, key="x"), "live"), (THIRD, "owner"), (THIRD, None)):
            with self.assertRaises(hv.HandoverError):
                self.me.accept_node(node["id"], node["name"], node["key"], carry, node["key"])
        self.accept()
        with self.assertRaises(hv.HandoverError) as cm:
            self.accept(dict(THIRD, id="d" * 32, name="copy"))              # the same key under another id
        self.assertIn("already has that key", cm.exception.message)
        with self.assertRaises(hv.HandoverError) as cm:
            self.accept(dict(THIRD, key="6" * 64))                           # the same id with another key
        self.assertIn("another key", cm.exception.message)
        self.assertEqual(len(self.me.status()["accepted"]), 1)

    def test_accepting_again_changes_the_carry_and_removing_takes_it_away(self):
        self.accept(carry="live")
        self.accept(carry="full")
        self.assertEqual(self.me.approved(THIRD["id"], THIRD["key"])["carry"], "full")
        self.me.remove_acceptance(THIRD["id"], "Anthony iPad")
        self.assertIsNone(self.me.approved(THIRD["id"], THIRD["key"]))
        with self.assertRaises(hv.HandoverError) as cm:
            self.me.remove_acceptance(THIRD["id"])
        self.assertEqual(cm.exception.status, 404)

    def test_a_denied_key_cannot_be_accepted(self):
        self.me.deny_add(THIRD["key"], "third", "leyline")
        with self.assertRaises(hv.HandoverError) as cm:
            self.accept()
        self.assertIn("removed from the cluster", cm.exception.message)

    def test_the_number_of_acceptances_is_bounded(self):
        for i in range(hv.MAX_PAIRINGS):
            n = {"id": "%032x" % (i + 100), "name": "n%d" % i, "key": "%064x" % (i + 100)}
            self.accept(n)
        with self.assertRaises(hv.HandoverError) as cm:
            self.accept({"id": "%032x" % 999, "name": "extra", "key": "%064x" % 999})
        self.assertIn("at most", cm.exception.message)


class Denying(HandoverBase):
    def test_the_deny_file_is_kept_read_back_and_private(self):
        self.assertTrue(self.me.deny_add("4" * 64, "gone", "leyline"))
        self.assertFalse(self.me.deny_add("4" * 64, "gone", "leyline"))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, "deny.json")).st_mode), 0o640)
        again = self.make(self.cert)
        again.load()
        self.assertEqual(again.denied_keys(), {"4" * 64})
        self.assertEqual(again.status()["removed"], [{"name": "gone", "by": "leyline"}])
        self.assertTrue(again.deny_remove("4" * 64))
        self.assertFalse(again.deny_remove("4" * 64))
        cold = self.make(self.cert)
        cold.load()
        self.assertEqual(cold.denied_keys(), set())

    def test_a_node_never_denies_itself_and_bad_keys_are_refused(self):
        for key in (self.my_key, "x", "", None, 5, "4" * 63):
            with self.assertRaises(hv.HandoverError):
                self.me.deny_add(key, "x", "y")
        self.assertFalse(os.path.exists(os.path.join(self.state, "deny.json")))

    def test_the_deny_file_is_bounded_and_a_damaged_one_is_ignored(self):
        for i in range(hv.MAX_DENY):
            self.me.deny_add("%064x" % (i + 1), "n", "m")
        with self.assertRaises(hv.HandoverError):
            self.me.deny_add("%064x" % 9999, "n", "m")
        with open(os.path.join(self.state, "deny.json"), "w") as f:
            f.write("{not json")
        cold = self.make(self.cert)
        cold.load()
        self.assertEqual(cold.denied_keys(), set())
        with open(os.path.join(self.state, "deny.json"), "w") as f:
            json.dump({"v": 1, "entries": [{"key": "4" * 64, "name": "ok", "by": "x", "n": 1}, {"key": "nope", "name": "bad", "by": "x", "n": 2}, 5, {"key": "5" * 64}]}, f)
        cold.load()
        self.assertEqual(cold.denied_keys(), {"4" * 64})


class Approving(HandoverBase):
    def setUp(self):
        super().setUp()
        self.me.take_list(*self.pair(1, nodes=[{"id": ME, "name": "node-01", "key": self.my_key, "carry": "live"}, PEER]))

    def test_a_node_on_the_list_with_its_key_is_approved_and_this_nodes_own_carry_applies(self):
        self.assertEqual(self.me.approved(PEER["id"], PEER["key"]), {"via": "list", "name": "leyline", "carry": "live"})

    def test_an_id_alone_is_not_enough_and_nor_is_a_key_alone(self):
        self.assertIsNone(self.me.approved(PEER["id"], "9" * 64))              # the id is public: a cloned id with its own key
        self.assertIsNone(self.me.approved("d" * 32, PEER["key"]))             # a key under an id the list does not give it
        self.assertIsNone(self.me.approved("d" * 32, "9" * 64))
        for junk in (None, 5, [], {}):
            self.assertIsNone(self.me.approved(junk, PEER["key"]))
            self.assertIsNone(self.me.approved(PEER["id"], junk))

    def test_the_root_alone_approves_nobody(self):
        """A node with a certificate from the root, on no list and in no acceptance, is approved by nothing."""
        self.assertIsNone(self.me.approved(THIRD["id"], THIRD["key"]))
        self.assertIsNone(self.make(self.cert).approved(PEER["id"], PEER["key"]))          # nor does a node that holds no list

    def test_this_node_is_never_its_own_peer(self):
        self.assertIsNone(self.me.approved(ME, self.my_key))
        self.assertIsNone(self.me.approved(ME, PEER["key"]))
        self.assertIsNone(self.me.approved(PEER["id"], self.my_key))

    def test_an_accepted_node_is_approved_with_the_carry_chosen_and_the_list_wins_when_both_say_so(self):
        self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "full", THIRD["key"])
        self.assertEqual(self.me.approved(THIRD["id"], THIRD["key"]), {"via": "pairing", "name": "third", "carry": "full"})
        self.me.accept_node(PEER["id"], PEER["name"], PEER["key"], "full", PEER["key"])
        self.assertEqual(self.me.approved(PEER["id"], PEER["key"])["via"], "list")
        self.assertEqual(self.me.approved(PEER["id"], PEER["key"])["carry"], "live")

    def test_a_carry_of_none_is_approved_but_gives_nothing(self):
        self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "none", THIRD["key"])
        self.assertEqual(self.me.approved(THIRD["id"], THIRD["key"])["carry"], "none")
        self.assertIsNone(hv.carried_role("full", "none"))

    def test_the_deny_list_wins_over_the_list_and_over_an_acceptance(self):
        self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "full", THIRD["key"])
        self.me.deny_add(PEER["key"], "leyline", "x")
        self.me.deny_add(THIRD["key"], "third", "x")
        self.assertIsNone(self.me.approved(PEER["id"], PEER["key"]))
        self.assertIsNone(self.me.approved(THIRD["id"], THIRD["key"]))
        self.me.deny_remove(PEER["key"])
        self.assertIsNotNone(self.me.approved(PEER["id"], PEER["key"]))

    def test_a_renewed_key_of_this_node_stops_the_list_from_counting(self):
        """If this node's certificate changes to another key, the held list no longer names it: nothing is approved."""
        _key, other = self.node_cert()
        moved = self.make(other)
        moved.list = self.me.list
        self.assertIsNone(moved.approved(PEER["id"], PEER["key"]))

    def test_the_role_a_device_gets_is_never_higher_than_it_had_or_than_the_carry(self):
        table = {("full", "full"): "full", ("full", "live"): "live", ("full", "none"): None, ("live", "full"): "live",
                 ("live", "live"): "live", ("live", "none"): None, ("view", "full"): None, ("view", "live"): None,
                 ("view", "none"): None, ("root", "full"): None, ("full", "owner"): None, (None, "full"): None, ("full", None): None}
        for (role, carry), want in table.items():
            self.assertEqual(hv.carried_role(role, carry), want, (role, carry))


class Floors(HandoverBase):
    """What a node remembers when the files on disk do not check out at start (the root briefly missing, a renewed key)."""

    def test_booting_without_the_root_an_older_serial_is_still_refused(self):
        self.me.take_list(*self.pair(5))
        cold = self.make(self.cert, root="")                       # the root is not there at start
        cold.load()
        self.assertIsNone(cold.list)
        self.assertIn("below 5", cold.problem)
        self.assertEqual((cold.floor.fleet, cold.floor.serial), (FLEET, 5))
        cold.https.root = self.roots["one"][1]                       # the root is back
        for serial in (4, 1):
            with self.assertRaises(hv.HandoverError) as cm:
                cold.take_list(*self.pair(serial))
            self.assertIn("older", cm.exception.message)
        other = self.pair(5, nodes=[{"id": ME, "name": "node-01", "key": self.my_key, "carry": "live"}, PEER])
        with self.assertRaises(hv.HandoverError) as cm:
            cold.take_list(*other)
        self.assertIn("same serial", cm.exception.message)
        self.assertIsNone(cold.list)
        cold.take_list(*self.pair(5))                                 # the same list again is fine
        self.assertEqual(cold.list.serial, 5)
        self.assertIsNone(cold.floor)

    def test_after_a_renewal_with_a_new_key_another_fleets_list_is_refused(self):
        self.me.take_list(*self.pair(3))
        _key, renewed = self.node_cert()                             # a new key: the held list no longer names this node
        cold = self.make(renewed)
        cold.load()
        self.assertIsNone(cold.list)
        self.assertEqual(cold.floor.fleet, FLEET)
        new_key = FL.key_hash(FL.certificate_der(renewed))
        mine = {"id": ME, "name": "node-01", "key": new_key, "carry": "full"}
        stranger = FL.canonical(self.data(9, fleet="fedcba9876543210", nodes=[mine, PEER]))
        with self.assertRaises(hv.HandoverError) as cm:
            cold.take_list(stranger, self.sign_message(FL.signed_bytes("fedcba9876543210", 9, stranger)))
        self.assertIn("another fleet", cm.exception.message)
        lower = FL.canonical(self.data(2, nodes=[mine, PEER]))
        with self.assertRaises(hv.HandoverError) as cm:
            cold.take_list(lower, self.sign_message(FL.signed_bytes(FLEET, 2, lower)))
        self.assertIn("older", cm.exception.message)
        newer = FL.canonical(self.data(4, nodes=[mine, PEER]))                # the owner's new list for the new key
        cold.take_list(newer, self.sign_message(FL.signed_bytes(FLEET, 4, newer)))
        self.assertEqual(cold.list.serial, 4)

    def test_a_newer_list_that_does_not_check_out_is_not_replaced_by_the_older_pair(self):
        self.me.take_list(*self.pair(1))
        self.me.take_list(*self.pair(2))
        self.assertIsNotNone(self.me.approved(PEER["id"], PEER["key"]))
        with open(os.path.join(self.state, "fleet.sig"), "w") as f:
            f.write("AAAAAAAAAAAAAAAAAAAA")                          # serial 2 is on disk and its signature does not hold
        cold = self.make(self.cert)
        cold.load()
        self.assertIsNone(cold.list)
        self.assertIsNone(cold.approved(PEER["id"], PEER["key"]))      # nothing the newer list may have dropped is approved
        self.assertEqual(cold.floor.serial, 2)
        self.assertIn("below 2", cold.problem)
        with self.assertRaises(hv.HandoverError):
            cold.take_list(*self.pair(1))

    def test_a_damaged_file_leaves_no_floor_and_a_first_list_is_taken(self):
        with open(os.path.join(self.state, "fleet.json"), "w") as f:
            f.write("{nonsense")
        cold = self.make(self.cert)
        cold.load()
        self.assertIsNone(cold.floor)
        cold.take_list(*self.pair(1))
        self.assertEqual(cold.list.serial, 1)


class Details(HandoverBase):
    def test_the_status_says_when_the_held_list_no_longer_names_this_nodes_key(self):
        self.me.take_list(*self.pair(1))
        self.assertIsNone(self.me.status()["list_problem"])
        _key, renewed = self.node_cert()
        moved = self.make(renewed)
        moved.list = self.me.list
        self.assertIn("does not name this node with the key it uses now", moved.status()["list_problem"])

    def test_a_wipe_takes_every_leftover_whatever_its_ending(self):
        self.me.take_list(*self.pair(1))
        for name in ("fleet.json.prev", "fleet.sig.new", "fleet.json.new.tmp", "fleet.sig.prev.tmp", "deny.json.tmp", "pairings.json.new"):
            with open(os.path.join(self.state, name), "w") as f:
                f.write("x")
        with open(os.path.join(self.state, "node-id"), "w") as f:
            f.write("kept")
        self.assertEqual(self.me.wipe(), [])
        self.assertEqual([n for n in self.files() if n != "settings.json"], ["node-id"])

    def test_the_acceptance_file_is_read_with_the_same_rules_it_is_written_with(self):
        good = {"id": THIRD["id"], "name": "third", "key": THIRD["key"], "carry": "live"}
        rows = [good, dict(good, name="again"),                              # a second row for the same id
                dict(good, id="short"), dict(good, id="d" * 32, key="x"), dict(good, id="e" * 32, carry="owner"),
                dict(good, id="f" * 32, name="a b"), {"id": 5}, "text"]
        with open(os.path.join(self.state, "pairings.json"), "w") as f:
            json.dump({"v": 1, "nodes": rows}, f)
        cold = self.make(self.cert)
        cold.load()
        self.assertEqual(cold.status()["accepted"], [{"id": THIRD["id"], "name": "third", "carry": "live"}])
        self.assertEqual(cold.approved(THIRD["id"], THIRD["key"])["via"], "pairing")
        self.assertIsNone(cold.approved("d" * 32, "x"))

    def test_the_fingerprint_may_be_separated_by_spaces_colons_and_dashes_only(self):
        key = "3f9a" * 16
        grouped = " ".join(key[i:i + 4] for i in range(0, 64, 4))
        self.assertTrue(hv.same_fingerprint(grouped, key))
        self.assertTrue(hv.same_fingerprint("\t" + grouped.upper() + "\n", key))
        for typed in (key + "zz", key[:-1] + "g", key.replace("3", ".", 1), key.replace("3", "_", 1), key + "3f", "0x" + key, key[:60], "", None, 5, [key]):
            self.assertFalse(hv.same_fingerprint(typed, key), typed)
        self.assertFalse(hv.same_fingerprint(key, None))

    def test_accepting_a_node_again_is_logged_as_a_change(self):
        self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "live", THIRD["key"], "Anthony iPad")
        self.me.accept_node(THIRD["id"], THIRD["name"], THIRD["key"], "full", THIRD["key"], "Anthony iPad")
        self.assertEqual([t for _k, t, _w in self.events.lines],
                         ["third was accepted by its key fingerprint (carry live).", "The acceptance of third was changed (carry live to full)."])

    def test_the_card_is_not_given_while_discovery_is_off(self):
        self.assertEqual(self.me.card()["id"], ME)
        self.settings.data["fleet"] = {"discovery": False}
        self.assertIsNone(self.me.card())
        self.settings.data["fleet"] = {"discovery": True, "accept": True}
        self.assertEqual(self.me.card()["key"], self.my_key)


if __name__ == "__main__":
    unittest.main()
