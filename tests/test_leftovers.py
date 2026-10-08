# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What a power cut leaves behind on the box is removed the next time, and nothing else is (D70).

Five places wrote a temp file or folder beside the thing it was to become and removed it in a `finally`, which a
power cut never reaches: the updater's work folder in the install, a save of the settings, an upload of a shader or
an effect, a theme being added. And two ways the updater could end up outside the place it was meant to work in:
a lock file of its own, and a work folder in the system temp folder.
"""
import io
import os
import shutil
import stat
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from pvj import paths, themes, update
from pvj.settings import Settings, TEMP_NAME, TEMP_STALE
from pvj.update import UpdateError, Updater
from tests.test_server import ServerBase

ROOT = os.geteuid() == 0


class Folder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def put(self, *parts, text="x", age=0.0, folder=None):
        path = os.path.join(folder or self.dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    def names(self, folder=None):
        return sorted(os.listdir(folder or self.dir))

    @staticmethod
    def read(path):
        with open(path) as f:
            return f.read()


class RemoveLeftovers(Folder):
    """pvj.paths.remove_leftovers, which the three services use."""

    def test_only_a_plain_file_with_exactly_that_name_and_that_age(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        precious = self.put("precious", folder=outside)
        self.put(".settings-abcd1234", age=7200)
        self.put(".settings-fresh123", age=10)                  # a save that is happening now
        self.put(".settings-abcd1234.bak", age=7200)            # a longer name
        self.put("x.settings-abcd1234", age=7200)
        self.put("settings.json", age=7200)
        self.put("settings.json.bak", age=7200)
        os.mkdir(os.path.join(self.dir, ".settings-folder00"))
        os.symlink(precious, os.path.join(self.dir, ".settings-link0000"))
        os.symlink(os.path.join(self.dir, "nowhere"), os.path.join(self.dir, ".settings-dangling"))
        os.mkfifo(os.path.join(self.dir, ".settings-pipe0000"))
        before = self.names()
        self.assertEqual(paths.remove_leftovers(self.dir, TEMP_NAME, older_than=3600), [".settings-abcd1234"])
        self.assertEqual(self.names(), [n for n in before if n != ".settings-abcd1234"])
        # and with no age asked for: the fresh file goes; the links, the pipe and the folder stay whatever their age
        self.assertEqual(paths.remove_leftovers(self.dir, TEMP_NAME), [".settings-fresh123"])
        self.assertEqual(self.names(), [n for n in before if n not in (".settings-abcd1234", ".settings-fresh123")])
        self.assertTrue(os.path.exists(precious))

    def test_a_file_from_the_future_is_a_leftover_too(self):
        # Review of #108, finding 4: a Pi has no clock of its own. Started without a time source it believes an
        # earlier time than the one its files were written at, and "older than an hour" was then never true.
        self.put(".settings-tomorrow", age=-86400)
        self.put(".settings-skewed00", age=-1800)                # the clock was set back a little: may be a save now
        self.put(".settings-fresh123", age=10)
        self.assertEqual(paths.remove_leftovers(self.dir, TEMP_NAME, older_than=3600), [".settings-tomorrow"])
        self.assertEqual(self.names(), [".settings-fresh123", ".settings-skewed00"])
        # the clock the files were written by, an hour and more behind and ahead of the file's time
        self.put(".upload-1-2")
        at = os.stat(os.path.join(self.dir, ".upload-1-2")).st_mtime
        for now in (at - 3599, at, at + 3599):
            self.assertEqual(paths.remove_leftovers(self.dir, r"\.upload-\d+-\d+", 3600, now=now), [], now - at)
        self.assertEqual(paths.remove_leftovers(self.dir, r"\.upload-\d+-\d+", 3600, now=at - 3601), [".upload-1-2"])

    def test_a_folder_that_is_a_link_is_left_alone(self):
        real = os.path.join(self.dir, "real")
        self.put("real", ".upload-1-2", age=7200)
        os.symlink(real, os.path.join(self.dir, "link"))
        self.assertEqual(paths.remove_leftovers(os.path.join(self.dir, "link"), r"\.upload-\d+-\d+"), [])
        self.assertEqual(self.names(real), [".upload-1-2"])
        self.assertEqual(paths.remove_leftovers(real, r"\.upload-\d+-\d+"), [".upload-1-2"])

    def test_it_never_raises(self):
        self.assertEqual(paths.remove_leftovers(os.path.join(self.dir, "missing"), r".*"), [])
        self.assertEqual(paths.remove_leftovers(self.put("a-file"), r".*"), [])
        self.assertEqual(paths.remove_leftovers(None, r".*"), [])
        self.put(".upload-1-2")
        with mock.patch("os.unlink", side_effect=PermissionError(13, "no")):
            self.assertEqual(paths.remove_leftovers(self.dir, r"\.upload-\d+-\d+"), [])
        with mock.patch("os.listdir", side_effect=OSError(5, "io")):
            self.assertEqual(paths.remove_leftovers(self.dir, r"\.upload-\d+-\d+"), [])


class SettingsLeftovers(Folder):
    def test_loading_removes_the_temp_file_of_a_save_that_a_power_cut_ended(self):
        path = os.path.join(self.dir, "settings.json")
        s = Settings(path)
        s.load()
        s.save()                                                 # so that there is a .bak too
        old = self.put(".settings-abcd1234", text='{"half": ', age=TEMP_STALE + 60)
        ahead = self.put(".settings-ahead000", text='{"half": ', age=-TEMP_STALE - 60)   # a box that lost its clock
        fresh = self.put(".settings-efgh5678", text="{}", age=TEMP_STALE - 60)
        before = {n: self.read(os.path.join(self.dir, n)) for n in ("settings.json", "settings.json.bak")}
        again = Settings(path)
        again.load()
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(ahead))
        self.assertTrue(os.path.exists(fresh), "a second panel started by hand may be saving at this moment")
        self.assertEqual({n: self.read(os.path.join(self.dir, n)) for n in before}, before)
        self.assertEqual(again.data, s.data)

    def test_the_name_is_the_one_a_save_really_uses(self):
        import re
        seen = []
        real = os.replace

        def replace(src, dst):
            seen.append(os.path.basename(src))
            return real(src, dst)

        s = Settings(os.path.join(self.dir, "settings.json"))
        with mock.patch("os.replace", replace):
            s.load()
        self.assertTrue(seen)
        for name in seen:
            self.assertTrue(re.fullmatch(TEMP_NAME, name), name)

    def test_a_first_start_with_a_leftover_and_no_settings_still_starts(self):
        self.put(".settings-abcd1234", text="", age=TEMP_STALE + 60)
        s = Settings(os.path.join(self.dir, "settings.json"))
        s.load()
        self.assertEqual(self.names(), ["settings.json"])


class LibraryLeftovers(ServerBase):
    """The shaders', the effects' and the themes' folders, when the panel starts."""

    def test_a_cut_off_upload_is_removed_when_the_library_is_built_and_never_counted(self):
        from pvj import effects as effects_mod, shaderlive
        state = os.path.dirname(self.settings.path)
        for sub, build in (("shaders", lambda: shaderlive.LiveEngine(self.api)),
                           ("effects", lambda: effects_mod.Effects(self.api, thread=False))):
            folder = os.path.join(state, sub)
            os.makedirs(folder, exist_ok=True)
            for name in (".upload-4242-140735", "mine.fs", ".upload-notes", ".upload-1-2.fs"):
                with open(os.path.join(folder, name), "w") as f:
                    f.write("x")
            os.symlink(os.path.join(self.tmp, "secret.mp4"), os.path.join(folder, ".upload-7-7"))
            engine = build()
            self.assertEqual(sorted(os.listdir(folder)), [".upload-1-2.fs", ".upload-7-7", ".upload-notes", "mine.fs"], sub)
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "secret.mp4")))
            # Counted toward "at most 64 uploads"? No: a name the library lists begins with a letter or a digit.
            with open(os.path.join(folder, ".upload-4242-140735"), "w") as f:
                f.write("x")
            self.assertEqual(engine._names(folder), ["mine.fs"], sub)

    def test_a_cut_off_theme_is_removed_when_the_store_is_built_and_never_listed(self):
        addons = os.path.join(self.tmp, "addons")
        folder = os.path.join(addons, "themes")
        os.makedirs(folder)
        for name in (".adding-4242-140735", "mine.json", ".adding-notes"):
            with open(os.path.join(folder, name), "w") as f:
                f.write("{}")
        store = themes.Store(addons)
        self.assertEqual(sorted(os.listdir(folder)), [".adding-notes", "mine.json"])
        with open(os.path.join(folder, ".adding-4242-140735"), "w") as f:
            f.write("{}")
        self.assertEqual(store._names(), ["mine.json"])

    def test_a_themes_folder_that_is_a_link_is_not_tidied(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        open(os.path.join(elsewhere, ".adding-1-2"), "w").close()
        addons = os.path.join(self.tmp, "addons")
        os.makedirs(addons)
        os.symlink(elsewhere, os.path.join(addons, "themes"))
        themes.Store(addons)
        themes.Store(None)                                       # a panel without an add-ons folder
        self.assertEqual(os.listdir(elsewhere), [".adding-1-2"])
        # the add-ons folder itself a link, with a real themes folder behind it
        behind = os.path.join(self.tmp, "behind")
        os.makedirs(os.path.join(behind, "themes"))
        open(os.path.join(behind, "themes", ".adding-1-2"), "w").close()
        os.symlink(behind, os.path.join(self.tmp, "addons-link"))
        themes.Store(os.path.join(self.tmp, "addons-link"))
        self.assertEqual(os.listdir(os.path.join(behind, "themes")), [".adding-1-2"])


class UpdaterBase(Folder):
    def setUp(self):
        super().setUp()
        self.root = self.dir
        self.u = Updater(root=self.root, install=lambda *a: None, restart=lambda: None, health=lambda: True)
        self.prefix = os.path.join(self.root, "opt/pvj")
        self.me = os.getuid()


class ScratchSweep(UpdaterBase):
    """P1: `.update-XXXXXXXX` in the install after a power cut: the bundle and its unpacked tree."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.umask, os.umask(0o022))               # the install is 755, as install.sh makes it

    def plant(self, name=".update-abcd1234"):
        self.put("opt/pvj", name, "bundle.tar.gz", text="x" * 1000)
        self.put("opt/pvj", name, "tree", "pvj", "__init__.py")
        return name

    def test_a_planted_work_folder_is_removed_and_nothing_else_in_the_install(self):
        self.plant()
        self.put("opt/pvj", "releases", "1.0.0", "pvj", "__init__.py")
        self.put("opt/pvj", "previous")
        self.put("opt/pvj", ".update-abcd1234x", "keep")         # nine characters: not a name check() makes
        self.put("opt/pvj", ".updates", "keep")
        self.put("opt/pvj", ".update-file0000")                  # a file, not a folder
        os.symlink("releases/1.0.0", os.path.join(self.prefix, "current"))
        self.assertEqual(self.u.sweep_scratch(owner=self.me), [".update-abcd1234"])
        self.assertEqual(self.names(self.prefix), [".update-abcd1234x", ".update-file0000", ".updates", "current", "previous",
                                                   "releases"])
        self.assertTrue(os.path.exists(os.path.join(self.prefix, "releases/1.0.0/pvj/__init__.py")))

    def test_the_name_is_the_one_check_really_uses(self):
        made = []
        real = tempfile.mkdtemp

        def mkdtemp(*a, **k):
            made.append(real(*a, **k))
            return made[-1]

        os.makedirs(self.prefix)
        with mock.patch("tempfile.mkdtemp", mkdtemp), self.assertRaises(UpdateError):
            self.u.check(os.path.join(self.dir, "no-such-bundle.tar.gz"))
        with mock.patch("tempfile.mkdtemp", mkdtemp), self.assertRaises(UpdateError):
            self.u.check(self.put("pvj-1.0.0.tar.gz", text="not a bundle"), allow_unsigned=True)
        self.assertEqual(len(made), 1)
        self.assertEqual(os.path.dirname(made[0]), self.prefix)
        self.assertTrue(update.SCRATCH_NAME.fullmatch(os.path.basename(made[0])), made[0])

    def test_a_link_named_so_is_not_followed_and_not_removed(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        self.put("precious", folder=outside)
        os.makedirs(self.prefix)
        os.symlink(outside, os.path.join(self.prefix, ".update-link0000"))
        inside = self.plant()
        os.symlink(outside, os.path.join(self.prefix, inside, "tree", "way-out"))
        self.assertEqual(self.u.sweep_scratch(owner=self.me), [inside])
        self.assertEqual(self.names(self.prefix), [".update-link0000"])
        self.assertEqual(self.names(outside), ["precious"])

    # Review of #108, finding 5: a linked install was swept by nobody, while check() made its work folder through
    # the link all the same. The sweep now goes where the link goes, and looks at the folder it arrives in: a real
    # folder of the owner's (root's on a box) that neither its group nor anybody else can write. A folder that is
    # really somebody else's needs root to make; here that case is the default owner (root) seen by somebody who
    # is not root, in test_a_folder_that_is_not_roots_is_left.

    def test_an_install_folder_that_is_a_link_is_swept_where_it_really_is(self):
        elsewhere = os.path.join(self.dir, "elsewhere")
        self.put("elsewhere", ".update-abcd1234", "tree", "x")
        self.put("elsewhere", ".update-abcd1234x", "keep")
        self.put("elsewhere", "releases", "1.0.0", "keep")
        os.makedirs(os.path.join(self.root, "opt"))
        os.symlink(elsewhere, os.path.join(self.root, "opt", "by-way-of"))
        os.symlink("by-way-of", self.prefix)                     # a link to a link to the folder
        self.assertEqual(self.u.sweep_scratch(owner=self.me), [".update-abcd1234"])
        self.assertEqual(self.names(elsewhere), [".update-abcd1234x", "releases"])
        self.assertTrue(os.path.islink(self.prefix))

    def test_an_install_folder_that_others_can_write_is_not_swept_linked_or_not(self):
        elsewhere = os.path.join(self.dir, "elsewhere")
        self.put("elsewhere", ".update-abcd1234", "x")
        self.plant()
        link = Updater(root=self.root, prefix="/opt/linked")
        os.symlink(elsewhere, os.path.join(self.root, "opt", "linked"))
        for folder, u in ((self.prefix, self.u), (elsewhere, link)):
            for mode in (0o775, 0o757, 0o1777):
                os.chmod(folder, mode)
                self.assertEqual(u.sweep_scratch(owner=self.me), [], "%s %o" % (folder, mode))
                self.assertEqual(self.names(folder), [".update-abcd1234"])
            os.chmod(folder, 0o755)
            self.assertEqual(u.sweep_scratch(owner=self.me), [".update-abcd1234"])

    def test_an_install_folder_that_is_a_link_to_nothing_or_to_a_file_is_no_error(self):
        os.makedirs(os.path.join(self.root, "opt"))
        os.symlink(os.path.join(self.dir, "nowhere"), self.prefix)
        self.assertEqual(self.u.sweep_scratch(owner=self.me), [])
        os.unlink(self.prefix)
        os.symlink(self.put("a-file"), self.prefix)
        self.assertEqual(self.u.sweep_scratch(owner=self.me), [])

    @unittest.skipIf(ROOT, "as root every folder here is root's")
    def test_a_folder_that_is_not_roots_is_left(self):
        self.plant()
        self.assertEqual(self.u.sweep_scratch(), [])            # the default: root's folders only
        self.assertEqual(self.names(self.prefix), [".update-abcd1234"])

    def test_no_install_and_a_folder_that_cannot_be_removed_are_no_error(self):
        self.assertEqual(self.u.sweep_scratch(owner=self.me), [])
        self.plant()
        with mock.patch("shutil.rmtree", side_effect=PermissionError(13, "no")):
            self.assertEqual(self.u.sweep_scratch(owner=self.me), [])


class ScratchPlace(UpdaterBase):
    """P5: the work folder is in the install or there is no update; never in the system temp folder."""

    def test_where_nothing_is_installed_the_install_folder_is_made_and_used(self):
        made = []
        real = tempfile.mkdtemp

        def mkdtemp(*a, **k):
            made.append((real(*a, **k), k.get("dir")))
            return made[-1][0]

        self.assertFalse(os.path.exists(self.prefix))
        with mock.patch("tempfile.mkdtemp", mkdtemp), self.assertRaises(UpdateError):
            self.u.check(self.put("pvj-1.0.0.tar.gz", text="not a bundle"), allow_unsigned=True)
        self.assertEqual([d for _, d in made], [self.prefix])
        self.assertEqual(stat.S_IMODE(os.stat(self.prefix).st_mode) & 0o022, 0, "others may not write in the install")
        self.assertEqual(self.names(self.prefix), [], "the work folder of a refused bundle is gone again")

    def test_where_the_install_folder_cannot_be_made_the_update_is_refused(self):
        self.put("opt")                                          # a file where the folder would go
        calls = []
        bundle = self.put("pvj-1.0.0.tar.gz", text="not a bundle")
        with mock.patch("tempfile.mkdtemp", lambda *a, **k: calls.append(k) or self.fail("made a folder")):
            with self.assertRaises(UpdateError) as e:
                self.u.check(bundle, allow_unsigned=True)
        self.assertIn("work folder", str(e.exception))
        self.assertEqual(calls, [])

    def test_where_the_work_folder_cannot_be_made_nothing_goes_to_the_temp_folder(self):
        os.makedirs(self.prefix)
        seen = []

        def mkdtemp(*a, **k):
            seen.append(k.get("dir"))
            raise OSError(30, "Read-only file system")

        with mock.patch("tempfile.mkdtemp", mkdtemp), self.assertRaises(UpdateError) as e:
            self.u.check(self.put("pvj-1.0.0.tar.gz", text="not a bundle"), allow_unsigned=True)
        self.assertEqual(seen, [self.prefix])
        self.assertIn("Read-only file system", str(e.exception))


class Lock(Folder):
    """P4: there is one lock file. Whoever cannot use it does not update."""

    def test_a_lock_that_cannot_be_opened_is_an_error_not_another_file(self):
        opened = []
        real = os.open

        def spy(path, *a, **k):
            opened.append(path)
            return real(path, *a, **k)

        target = self.put("precious")
        cases = {"a folder": self.dir, "a link": os.path.join(self.dir, "link"),
                 "in a file": os.path.join(target, "lock")}
        os.symlink(target, cases["a link"])
        for what, path in cases.items():
            del opened[:]
            with mock.patch("os.open", spy), self.assertRaises(UpdateError, msg=what) as e:
                update.take_lock(path)
            self.assertIn(path, str(e.exception), what)
            self.assertLessEqual(set(opened), {path}, "%s: only the lock itself was tried" % what)
        self.assertEqual(self.read(target), "x")

    def test_a_missing_lock_folder_is_made_and_the_lock_still_excludes(self):
        path = os.path.join(self.dir, "lock", "pvj-update.lock")
        first = update.take_lock(path)
        self.addCleanup(first.close)
        self.assertTrue(os.path.isfile(path))
        self.assertIsNone(update.take_lock(path))
        first.close()
        second = update.take_lock(path)
        self.assertIsNotNone(second)
        second.close()

    def test_the_default_is_one_fixed_file_and_the_environment_may_name_another(self):
        with mock.patch.dict(os.environ, {"PVJ_UPDATE_LOCK": os.path.join(self.dir, "mine.lock")}):
            f = update.take_lock()
            f.close()
        self.assertEqual(self.names(), ["mine.lock"])

    # Review of #108, findings 2 and 6. The lock was `/run/lock/pvj-update.lock`, in a folder where every account
    # may make a name, and it was used whatever was found there. Now it is in the update units' own folder, which
    # only root can write, and what is opened is looked at. The old name is still taken, best effort, so that an
    # updater from before the move and one from after never run together.

    def test_the_lock_is_in_the_update_units_folder_which_they_make_for_root_alone(self):
        self.assertEqual(os.path.dirname(update.DEFAULT_LOCK), paths.UPDATE_DIR)
        self.assertEqual(update.OLD_LOCK, "/run/lock/pvj-update.lock")
        for unit in ("pvj-update-usb@.service", "pvj-update-inbox@.service"):
            text = self.read(os.path.join(os.path.dirname(__file__), "..", "install", unit))
            for line in ("User=root", "RuntimeDirectory=" + os.path.basename(paths.UPDATE_DIR),
                         "RuntimeDirectoryMode=0755", "RuntimeDirectoryPreserve=yes"):
                self.assertIn(line + "\n", text, unit)

    def test_only_a_plain_file_with_one_name_is_a_lock(self):
        pipe = os.path.join(self.dir, "pipe")
        os.mkfifo(pipe)
        twice = self.put("twice")
        os.link(twice, os.path.join(self.dir, "its-other-name"))
        for what, path in (("a pipe", pipe), ("a file with two names", twice)):
            with self.assertRaises(UpdateError, msg=what) as e:
                update.take_lock(path)
            self.assertIn(path, str(e.exception), what)

    def test_a_lock_in_a_folder_that_others_may_write_is_refused(self):
        folder = os.path.join(self.dir, "shared")
        os.mkdir(folder)
        for mode in (0o1777, 0o775, 0o757):
            os.chmod(folder, mode)
            with self.assertRaises(UpdateError, msg=oct(mode)) as e:
                update.take_lock(os.path.join(folder, "lock"))
            self.assertIn("others can write", str(e.exception))
            self.assertEqual(self.names(folder), [], "and nothing was made there")
        os.chmod(folder, 0o755)
        update.take_lock(os.path.join(folder, "lock")).close()

    def test_a_missing_lock_folder_is_made_as_the_units_make_it_whatever_the_umask(self):
        for umask in (0o077, 0o002):
            path = os.path.join(self.dir, "made-%o" % umask, "lock")
            before = os.umask(umask)
            try:
                update.take_lock(path).close()
            finally:
                os.umask(before)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o755)

    def test_an_updater_from_before_the_move_and_one_from_after_exclude_each_other(self):
        new, old = os.path.join(self.dir, "new.lock"), os.path.join(self.dir, "old.lock")
        earlier = update.take_lock(old)                          # what an updater from before the move holds
        self.assertIsNone(update.take_lock(new, old=old))
        earlier.close()
        mine = update.take_lock(new, old=old)                    # and it did not keep the new lock when it gave up
        self.assertIsNotNone(mine)
        self.addCleanup(mine.close)
        self.assertIsNone(update.take_lock(old), "the old name is held too")
        self.assertIsNone(update.take_lock(new, old=old))
        mine.close()                                             # one close lets go of both
        update.take_lock(old).close()
        update.take_lock(new, old=old).close()
        self.assertEqual(stat.S_IMODE(os.stat(old).st_mode), 0o600)

    def test_an_old_name_that_cannot_be_trusted_is_passed_over_and_never_stops_an_update(self):
        new = os.path.join(self.dir, "new.lock")
        target = self.put("precious")
        pipe = os.path.join(self.dir, "pipe")
        os.mkfifo(pipe)
        twice = self.put("twice")
        os.link(twice, os.path.join(self.dir, "its-other-name"))
        os.symlink(target, os.path.join(self.dir, "link"))
        before = self.names()
        for what, old in (("a folder", self.dir), ("a link", os.path.join(self.dir, "link")), ("a pipe", pipe),
                          ("a file with two names", twice), ("in a file", os.path.join(target, "lock")),
                          ("no such folder", os.path.join(self.dir, "missing", "pvj-update.lock"))):
            f = update.take_lock(new, old=old)
            self.assertIsNotNone(f, what)
            f.close()
        self.assertEqual(self.names(), sorted(before + ["new.lock"]), "the old name's folder is never made")
        self.assertEqual(self.read(target), "x")

    def test_an_old_name_that_is_somebody_elses_file_is_passed_over_even_when_it_is_held(self):
        # With fs.protected_regular off, any account can put its own file at the old name and hold it for ever.
        # Root is not available here, so "somebody else's" is made by saying that we are somebody else.
        new, old = os.path.join(self.dir, "new.lock"), os.path.join(self.dir, "old.lock")
        theirs = update.take_lock(old)
        self.addCleanup(theirs.close)
        with mock.patch("os.geteuid", return_value=os.geteuid() + 1):
            f = update.take_lock(new, old=old)
        self.assertIsNotNone(f)
        f.close()

    def test_the_default_lock_takes_the_old_name_without_being_asked(self):
        new, old = os.path.join(self.dir, "run", "update.lock"), os.path.join(self.dir, "old.lock")
        env = {k: v for k, v in os.environ.items() if k != "PVJ_UPDATE_LOCK"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(update, "DEFAULT_LOCK", new), \
                mock.patch.object(update, "OLD_LOCK", old):
            mine = update.take_lock()                            # as main() calls it on a box
            self.addCleanup(mine.close)
            self.assertEqual(stat.S_IMODE(os.stat(old).st_mode), 0o600)
            self.assertIsNone(update.take_lock(old), "an updater from before the move is kept out")
            mine.close()
            earlier = update.take_lock(old)
            self.addCleanup(earlier.close)
            self.assertIsNone(update.take_lock(), "and one that runs keeps this one out")

    def test_the_old_name_is_only_touched_where_the_lock_is_the_default_one(self):
        opened = []
        real = os.open

        def spy(path, *a, **k):
            opened.append(path)
            return real(path, *a, **k)

        mine = os.path.join(self.dir, "mine.lock")
        with mock.patch("os.open", spy):
            update.take_lock(mine).close()
            with mock.patch.dict(os.environ, {"PVJ_UPDATE_LOCK": mine}):
                update.take_lock().close()
        self.assertEqual(set(opened), {mine})
        del opened[:]

        def nowhere(path, *a, **k):                              # the default, without touching this machine's /run
            opened.append(path)
            raise PermissionError(13, "Permission denied")

        env = {k: v for k, v in os.environ.items() if k != "PVJ_UPDATE_LOCK"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("os.open", nowhere), \
                mock.patch("os.stat", return_value=os.stat(self.dir)), self.assertRaises(UpdateError):
            update.take_lock()
        self.assertEqual(opened, [update.DEFAULT_LOCK])


class CommandLine(Folder):
    """main(): the sweep happens under the lock and only there; no lock, no update."""

    def setUp(self):
        super().setUp()
        self.lock = os.path.join(self.dir, "lock")
        p = mock.patch.dict(os.environ, {"PVJ_UPDATE_LOCK": self.lock,
                                         "PVJ_UPDATE_RESULT": os.path.join(self.dir, "result.json")})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("os.geteuid", return_value=0)
        p.start()
        self.addCleanup(p.stop)
        self.calls = calls = []

        class Fake:
            def sweep_scratch(self):
                calls.append("sweep")
                return [".update-abcd1234"]

            def rollback(self):
                calls.append("rollback")
                return "1.0.0"

            def check(self, *a):
                calls.append("check")
                folder = tempfile.mkdtemp()
                return {"version": "1.0.0", "schema": 1}, folder, folder
        p = mock.patch.object(update, "Updater", Fake)
        p.start()
        self.addCleanup(p.stop)

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = update.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_old_work_folders_go_before_the_update_and_under_the_lock(self):
        code, out, err = self.main("rollback")
        self.assertEqual((code, self.calls), (0, ["sweep", "rollback"]), err)
        self.assertIn("removed .update-abcd1234", out)

    def test_with_the_lock_held_elsewhere_nothing_is_removed(self):
        held = update.take_lock()
        self.addCleanup(held.close)
        code, out, err = self.main("rollback")
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn("another update is running", err)

    def test_a_check_takes_the_lock_too_because_it_makes_a_work_folder(self):
        # Review of #108, finding 1: `check` made its `.update-XXXXXXXX` without the lock, so an update started
        # meanwhile swept it, and "holding the lock proves that no such folder is in use" was not true.
        code, out, err = self.main("check", "pvj-1.0.0.tar.gz")
        self.assertEqual((code, self.calls), (0, ["sweep", "check"]), err)
        del self.calls[:]
        held = update.take_lock()
        self.addCleanup(held.close)
        code, out, err = self.main("check", "pvj-1.0.0.tar.gz")
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn("another update is running", err)

    def test_commands_that_take_no_lock_do_not_sweep(self):
        with mock.patch.object(update.Updater, "status", lambda self: {}, create=True):
            code, out, err = self.main("status")
        self.assertEqual((code, self.calls), (0, []), err)

    def test_a_sweep_that_fails_does_not_stop_the_update(self):
        with mock.patch.object(update.Updater, "sweep_scratch", side_effect=RuntimeError("boom")):
            code, out, err = self.main("rollback")
        self.assertEqual((code, self.calls), (0, ["rollback"]), err)
        self.assertIn("could not look for old work folders", err)

    def test_without_a_usable_lock_there_is_no_update_and_the_panel_is_told_why(self):
        # Review of #108, finding 3: the panel had already answered "started", and the result file never said why
        # nothing happened. (Where another update holds the lock the file is that update's, and is left alone.)
        import json
        os.mkdir(self.lock)                                      # the lock's name is taken by a folder
        result = self.put("result.json", text='{"state": "done", "message": "updated to 1.0.0"}')
        code, out, err = self.main("rollback")
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn("cannot use the update lock", err)
        said = json.loads(self.read(result))
        self.assertEqual(said["state"], "failed")
        self.assertIn("cannot use the update lock", said["message"])
        self.assertEqual(self.names(), ["lock", "result.json"])

    def test_with_the_lock_held_elsewhere_the_result_is_the_running_updates(self):
        held = update.take_lock()
        self.addCleanup(held.close)
        result = self.put("result.json", text='{"state": "running", "message": "other"}')
        code, out, err = self.main("rollback")
        self.assertEqual(code, 1)
        self.assertEqual(self.read(result), '{"state": "running", "message": "other"}')

class EndToEnd(UpdaterBase):
    """The real main() with the real Updater, in a folder of its own: the sweep exactly as a box calls it, with
    the owner it has there (root), which no other test here passes (review of #108, finding 7).

    Without root, a folder that is root's cannot be made. So one test shows that what is not root's is left by the
    real command, and the other is told by lstat and fstat that everything is root's, which is what they say on a
    box; as root it needs no telling. Only a run as root removes a folder that really is root's."""

    def setUp(self):
        super().setUp()
        import functools
        self.addCleanup(os.umask, os.umask(0o022))
        self.put("opt/pvj", "releases", "1.0.0", "pvj", "__init__.py")
        self.put("opt/pvj", "releases", "2.0.0", "pvj", "__init__.py")
        self.put("opt/pvj", "previous", text="/opt/pvj/releases/1.0.0\n")
        os.symlink("/opt/pvj/releases/2.0.0", os.path.join(self.prefix, "current"))
        self.left = os.path.join(self.prefix, ".update-abcd1234")
        self.put("opt/pvj", ".update-abcd1234", "tree", "pvj", "__init__.py")
        self.result = os.path.join(self.dir, "result.json")
        real = functools.partial(Updater, root=self.root, install=lambda *a: None, restart=lambda: None,
                                 health=lambda: True)
        for p in (mock.patch.dict(os.environ, {"PVJ_UPDATE_LOCK": os.path.join(self.dir, "lock"),
                                               "PVJ_UPDATE_RESULT": self.result}),
                  mock.patch("os.geteuid", return_value=0),      # main() asks for root before anything else
                  mock.patch.object(update, "Updater", real)):
            p.start()
            self.addCleanup(p.stop)

    def rollback(self):
        import json
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = update.main(["rollback"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(os.readlink(os.path.join(self.prefix, "current")), "/opt/pvj/releases/1.0.0")
        self.assertEqual(json.loads(self.read(self.result))["state"], "done")
        return out.getvalue()

    @unittest.skipIf(ROOT, "as root every folder here is root's")
    def test_the_command_leaves_a_work_folder_that_is_not_roots(self):
        self.assertNotIn("removed", self.rollback())
        self.assertTrue(os.path.isfile(os.path.join(self.left, "tree", "pvj", "__init__.py")))

    def test_the_command_removes_roots_work_folder_and_then_does_what_it_was_asked(self):
        def as_root(real):
            def look(*a, **k):
                st = list(real(*a, **k))
                st[stat.ST_UID] = 0
                return os.stat_result(st)
            return look

        with mock.patch("os.lstat", as_root(os.lstat)), mock.patch("os.fstat", as_root(os.fstat)):
            out = self.rollback()
        self.assertIn("removed .update-abcd1234, left by an update that was cut off", out)
        self.assertEqual(self.names(self.prefix), ["current", "previous", "releases"])
        self.assertTrue(os.path.isfile(os.path.join(self.prefix, "releases", "2.0.0", "pvj", "__init__.py")))


if __name__ == "__main__":
    unittest.main()
