import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import keys
from omaorchestra.__main__ import main
from omaorchestra.mcp import inventory, registry


class FakeCli:
    """Stands in for `claude mcp ...` and `codex mcp ...`; records the calls."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def __call__(self, argv, cwd=None, **kw):
        self.calls.append((argv, cwd))
        code = 1 if self.fail and self.fail in argv else 0
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="refused" if code else "")


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".config" / "opencode").mkdir(parents=True)
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        self.env = mock.patch.dict(os.environ, {
            "OMAORCHESTRA_AGENT_HOME": str(self.home), "OMAORCHESTRA_MCP": str(Path(self.tmp.name) / "mcp.json"),
            "OMAORCHESTRA_STATE_DIR": str(Path(self.tmp.name) / "state"),
            "OMAORCHESTRA_BIN": "/opt/omaorchestra/bin/omaorchestra"})
        self.env.start()
        self.vault = {}
        self.keyring = [mock.patch.object(keys, "store", lambda pid, v: self.vault.__setitem__(pid, v.strip())),
                        mock.patch.object(keys, "lookup", lambda pid: self.vault.get(pid)),
                        mock.patch.object(keys, "clear", lambda pid: self.vault.pop(pid, None))]
        for k in self.keyring:
            k.start()

    def tearDown(self):
        for k in self.keyring:
            k.stop()
        self.env.stop()
        self.tmp.cleanup()

    # ---------------------------------------------------------------- 6.1

    def test_inventory_reads_every_source_without_values(self):
        (self.home / ".claude.json").write_text(json.dumps({
            "mcpServers": {"github": {"type": "http", "url": "https://gh/mcp", "headers": {"Authorization": "Bearer SECRET"}}},
            "projects": {str(self.project): {"mcpServers": {"db": {"command": "db-mcp", "env": {"DB_PASS": "SECRET"}}},
                                             "disabledMcpjsonServers": ["shared-off"]}}}))
        (self.project / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "shared": {"command": "npx", "args": ["shared-mcp"]}, "shared-off": {"type": "sse", "url": "https://x/sse"}}}))
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.docs]\ncommand = "docs-mcp"\nargs = ["--x"]\n')
        (self.home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"mcp": {
            "fs": {"type": "local", "command": ["fs-mcp", "/tmp"], "environment": {"TOKEN": "SECRET"}},
            "remote": {"type": "remote", "url": "https://r/mcp", "enabled": False}}}))
        servers = inventory.everything()
        found = {(s["agent"], s["scope"], s["name"]): s for s in servers}
        self.assertEqual(set(found), {("claude", "user", "github"), ("claude", "local", "db"),
                                      ("claude", "project", "shared"), ("claude", "project", "shared-off"),
                                      ("codex", "user", "docs"), ("opencode", "user", "fs"), ("opencode", "user", "remote")})
        self.assertEqual(found["claude", "user", "github"]["header_keys"], ["Authorization"])
        self.assertFalse(found["claude", "project", "shared-off"]["enabled"])
        self.assertEqual((found["opencode", "user", "fs"]["command"], found["opencode", "user", "fs"]["args"]),
                         ("fs-mcp", ["/tmp"]))
        self.assertFalse(found["opencode", "user", "remote"]["enabled"])
        self.assertNotIn("SECRET", json.dumps(servers), "no secret values in the inventory")

    # ---------------------------------------------------------------- 6.2

    def stdio_spec(self):
        return {"transport": "stdio", "command": "db-mcp", "args": ["--ro"], "env": {"DB_HOST": "localhost"},
                "secret_env": ["DB_PASS"]}

    def test_add_stdio_everywhere_with_secrets_in_the_keyring(self):
        cli = FakeCli()
        targets = [registry.parse_target("claude"), registry.parse_target(f"claude:local:{self.project}"),
                   registry.parse_target("codex"), registry.parse_target("opencode")]
        registry.add("db", self.stdio_spec(), targets, {"DB_PASS": "hunter2"}, run=cli)
        self.assertEqual(self.vault, {"mcp:db:DB_PASS": "hunter2"})
        adds = [(argv, cwd) for argv, cwd in cli.calls if "add-json" in argv or "add" in argv[:3]]
        claude_user, claude_local, codex = adds
        entry = json.loads(claude_user[0][-1])
        self.assertEqual(entry, {"type": "stdio", "command": "/opt/omaorchestra/bin/omaorchestra",
                                 "args": ["mcp", "exec", "db"], "env": {}})
        self.assertEqual((claude_local[0][3:5], claude_local[1]), (["-s", "local"], str(self.project)))
        self.assertEqual(codex[0], ["codex", "mcp", "add", "db", "--", "/opt/omaorchestra/bin/omaorchestra",
                                    "mcp", "exec", "db"])
        oc = json.loads((self.home / ".config" / "opencode" / "opencode.json").read_text())
        self.assertEqual(oc["mcp"]["db"]["command"], ["/opt/omaorchestra/bin/omaorchestra", "mcp", "exec", "db"])
        written = json.dumps([c[0] for c in cli.calls]) + json.dumps(oc) + registry.path().read_text()
        self.assertNotIn("hunter2", written, "the secret is only in the keyring")
        self.assertTrue(inventory.opencode()[0]["managed"])

    def test_http_secret_headers_use_claudes_headers_helper(self):
        cli = FakeCli()
        spec = {"transport": "http", "url": "https://gh/mcp", "headers": {"X-Org": "me"}, "secret_headers": ["Authorization"]}
        registry.add("gh", spec, [registry.parse_target("claude")], {"Authorization": "Bearer tok"}, run=cli)
        entry = json.loads(cli.calls[-1][0][-1])
        self.assertEqual(entry, {"type": "http", "url": "https://gh/mcp",
                                 "headersHelper": "/opt/omaorchestra/bin/omaorchestra mcp headers gh"})
        self.assertEqual(json.loads(registry.headers_json("gh")), {"X-Org": "me", "Authorization": "Bearer tok"})
        for agent in ("codex", "opencode"):
            with self.assertRaisesRegex(registry.McpError, "secret headers"):
                registry.add("gh2", spec, [registry.parse_target(agent)], {"Authorization": "x"}, run=cli)
        self.assertNotIn("gh2", registry.load(), "a refused add stores nothing")

    def test_refusals(self):
        for bad in ("vscode", "claude:project", "claude:local", "codex:local:/x"):
            with self.assertRaises(registry.McpError):
                registry.parse_target(bad)
        with self.assertRaisesRegex(registry.McpError, "no value given"):
            registry.add("db", self.stdio_spec(), [registry.parse_target("claude")], {}, run=FakeCli())
        with self.assertRaisesRegex(registry.McpError, "refused"):
            registry.add("db", self.stdio_spec(), [registry.parse_target("claude")], {"DB_PASS": "x"},
                         run=FakeCli(fail="add-json"))

    def test_disable_enable_remove_with_backups(self):
        (self.home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"theme": "x"}))
        cli = FakeCli()
        registry.add("db", self.stdio_spec(), [registry.parse_target("opencode")], {"DB_PASS": "p"}, run=cli)
        oc = self.home / ".config" / "opencode" / "opencode.json"
        registry.set_enabled("db", False, run=cli)
        self.assertEqual(json.loads(oc.read_text()), {"theme": "x"}, "disabled: taken out, the rest kept")
        self.assertFalse(registry.get("db")["enabled"])
        registry.set_enabled("db", True, run=cli)
        self.assertIn("db", json.loads(oc.read_text())["mcp"])
        registry.remove("db", run=cli)
        self.assertEqual((registry.load(), self.vault), ({}, {}))
        backups = list((Path(self.tmp.name) / "state" / "backups").glob("opencode-*"))
        self.assertTrue(backups, "the agent's file was backed up")

    def test_exec_puts_secrets_in_the_servers_environment(self):
        registry.add("db", self.stdio_spec(), [registry.parse_target("opencode")], {"DB_PASS": "hunter2"}, run=FakeCli())
        with mock.patch("os.execvpe") as execvpe:
            registry.exec_server("db")
        command, argv, env = execvpe.call_args[0]
        self.assertEqual((command, argv), ("db-mcp", ["db-mcp", "--ro"]))
        self.assertEqual((env["DB_PASS"], env["DB_HOST"]), ("hunter2", "localhost"))

    def test_cli_add_list_and_managed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO("s3cret\n")), \
                mock.patch.object(registry, "install"):
            code = main(["mcp", "add", "db", "--agent", "codex", "--env", "DB_HOST=localhost",
                         "--secret-env", "DB_PASS", "--secrets-stdin", "--", "db-mcp", "--ro"])
            main(["mcp", "managed"])
        self.assertEqual(code, 0)
        self.assertEqual(self.vault, {"mcp:db:DB_PASS": "s3cret"})
        self.assertEqual(registry.get("db")["args"], ["--ro"])
        self.assertIn("secrets in keyring: DB_PASS", out.getvalue())
        self.assertNotIn("s3cret", out.getvalue())


if __name__ == "__main__":
    unittest.main()
