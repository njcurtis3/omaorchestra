import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omaorchestra import daemon, service


class FakeSystemctl:
    """Records systemctl calls; `active` says what is-active reports."""

    def __init__(self, active=False, fail=()):
        self.calls = []
        self.active = active
        self.fail = fail

    def __call__(self, argv, capture_output, text):
        args = argv[2:]  # drop "systemctl --user"
        self.calls.append(args)
        code = 0
        if args[0] == "is-active":
            code = 0 if self.active else 3
        if args[0] in self.fail:
            code = 1
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="boom" if code == 1 else "")

    def verbs(self):
        return [c[0] for c in self.calls]


class UnitTest(unittest.TestCase):
    def test_packaged_unit_matches_generated(self):
        packaged = (ROOT / "packaging" / service.UNIT_NAME).read_text()
        self.assertEqual(packaged, service.render_unit(service.PACKAGED_BINARY, marker=False))

    def test_exit_code_constants_agree(self):
        self.assertEqual(daemon.ALREADY_RUNNING_EXIT, service.ALREADY_RUNNING_EXIT)
        self.assertIn(f"RestartPreventExitStatus={service.ALREADY_RUNNING_EXIT}", service.render_unit("/x"))

    def test_quotes_paths_with_spaces(self):
        self.assertIn('ExecStart="/home/u/my dir/omaorchestra" daemon', service.render_unit("/home/u/my dir/omaorchestra"))
        self.assertIn("ExecStart=/usr/bin/omaorchestra daemon", service.render_unit("/usr/bin/omaorchestra"))

    def test_systemd_accepts_the_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / service.UNIT_NAME
            unit.write_text(service.render_unit(ROOT / "bin" / "omaorchestra"))
            result = subprocess.run(["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.unit = self.dir / service.UNIT_NAME
        self.no_package = self.dir / "no-packaged-unit"

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, binary, fake):
        return service.install(binary, unit_dir=self.dir, packaged_unit=self.no_package, run=fake)

    def test_fresh_install_writes_reloads_enables(self):
        fake = FakeSystemctl()
        self.install("/src/bin/omaorchestra", fake)
        self.assertTrue(service.written_by_us(self.unit))
        self.assertEqual(fake.verbs(), ["daemon-reload", "enable", "is-active"])
        self.assertEqual(fake.calls[1], ["enable", "--now", service.UNIT_NAME])

    def test_unchanged_reinstall_only_enables(self):
        self.install("/src/bin/omaorchestra", FakeSystemctl())
        fake = FakeSystemctl(active=True)
        self.install("/src/bin/omaorchestra", fake)
        self.assertEqual(fake.verbs(), ["enable"])

    def test_changed_unit_restarts_a_running_service(self):
        self.install("/old/omaorchestra", FakeSystemctl())
        fake = FakeSystemctl(active=True)
        done = self.install("/new/omaorchestra", fake)
        self.assertEqual(fake.verbs(), ["daemon-reload", "enable", "is-active", "restart"])
        self.assertIn("/new/omaorchestra", self.unit.read_text())
        self.assertTrue(any("restarted" in d for d in done))

    def test_refuses_to_overwrite_a_foreign_unit(self):
        self.unit.write_text("[Service]\nExecStart=/something/else\n")
        with self.assertRaises(service.ServiceError):
            self.install("/src/bin/omaorchestra", FakeSystemctl())
        self.assertIn("/something/else", self.unit.read_text())

    def test_packaged_install_only_enables(self):
        packaged = self.dir / "packaged.service"
        packaged.write_text("")
        fake = FakeSystemctl()
        service.install(service.PACKAGED_BINARY, unit_dir=self.dir, packaged_unit=packaged, run=fake)
        self.assertEqual(fake.calls, [["enable", "--now", service.UNIT_NAME]])
        self.assertFalse(self.unit.exists())

    def test_systemctl_failure_is_reported(self):
        with self.assertRaises(service.ServiceError) as caught:
            self.install("/src/bin/omaorchestra", FakeSystemctl(fail=("enable",)))
        self.assertIn("boom", str(caught.exception))


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.unit = self.dir / service.UNIT_NAME

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_our_unit(self):
        self.unit.write_text(service.render_unit("/src/omaorchestra"))
        fake = FakeSystemctl()
        service.uninstall(unit_dir=self.dir, run=fake)
        self.assertFalse(self.unit.exists())
        self.assertEqual(fake.verbs(), ["disable", "daemon-reload"])

    def test_leaves_a_foreign_unit(self):
        self.unit.write_text("[Service]\nExecStart=/something/else\n")
        done = service.uninstall(unit_dir=self.dir, run=FakeSystemctl())
        self.assertTrue(self.unit.exists())
        self.assertTrue(any("left" in d for d in done))


if __name__ == "__main__":
    unittest.main()
