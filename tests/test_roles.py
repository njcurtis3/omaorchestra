import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import daemon, history, launch, recipes, roles
from omaorchestra.__main__ import main
from omaorchestra.registry import Registry

ROLE = """---
name: {name}
description: {description}
{extra}
---

You are the {name}.
"""


def role_file(folder, name, description="A role", extra="", filename=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (filename or f"{name}.md")
    path.write_text(ROLE.format(name=name, description=description, extra=extra))
    return path


class FrontmatterTest(unittest.TestCase):
    def test_the_subset_subagent_files_use(self):
        fields, body = roles.frontmatter("""---
name: scout
description: "Finds facts: with a colon"
tools: Read, Glob, Bash(git *)
disallowedTools: [Write, Edit]
skills:
  - one
  - "two"
hooks:
  PreToolUse:
    - matcher: Bash
note: |
  first line
  second line
folded: >
  one
  two
maxTurns: 12
background: false
color: blue # a comment
# a comment line
---

The prompt.
""")
        self.assertEqual(fields["name"], "scout")
        self.assertEqual(fields["description"], "Finds facts: with a colon")
        self.assertEqual(fields["tools"], "Read, Glob, Bash(git *)")
        self.assertEqual(fields["disallowedTools"], ["Write", "Edit"])
        self.assertEqual(fields["skills"], ["one", "two"])
        self.assertIsNone(fields["hooks"])  # a nested table: not used by roles, never an error
        self.assertEqual(fields["note"], "first line\nsecond line")
        self.assertEqual(fields["folded"], "one two")
        self.assertEqual((fields["maxTurns"], fields["background"], fields["color"]), (12, False, "blue"))
        self.assertEqual(body, "The prompt.\n")
        self.assertEqual(roles._tools(fields["tools"], "tools"), ("Read", "Glob", "Bash(git *)"))

    def test_broken_frontmatter(self):
        for text, error in (("no frontmatter\n", "no frontmatter"),
                            ("---\nname: x\n", "no closing"),
                            ("---\njust words\n---\nbody\n", "key: value"),
                            ("---\n  name: x\n---\nbody\n", "indented")):
            with self.assertRaises(roles.RoleError) as caught:
                roles.frontmatter(text)
            self.assertIn(error, str(caught.exception))


class ParseTest(unittest.TestCase):
    def parse(self, extra="", name="r", body="Do it.", fallback=None):
        return roles.parse(f"---\nname: {name}\n{extra}\n---\n{body}\n", fallback_name=fallback)

    def test_fields(self):
        role = self.parse("agent: codex\nmodel: gpt-5\npermissionMode: acceptEdits\neffort: high\n"
                          "tools: Read, Grep\ndisallowedTools: Bash")
        self.assertEqual((role.agent, role.model, role.permission_mode, role.effort), ("codex", "gpt-5", "acceptEdits",
                                                                                       "high"))
        self.assertEqual((role.tools, role.disallowed_tools), (("Read", "Grep"), ("Bash",)))
        self.assertTrue(role.read_only)
        self.assertEqual(role.prompt, "Do it.\n")
        self.assertEqual(self.parse().agent, "claude")

    def test_refusals(self):
        for extra, name, body, error in (("", "-bad", "x", "name"), ("", "a:b", "x", "name"), ("", "r", "  ", "prompt"),
                                         ("agent: gemini", "r", "x", "agent"),
                                         ("permissionMode: yolo", "r", "x", "permissionMode"),
                                         ("effort: extreme", "r", "x", "effort"), ("model: two words", "r", "x", "model")):
            with self.assertRaises(roles.RoleError) as caught:
                self.parse(extra, name, body)
            self.assertIn(error, str(caught.exception))

    def test_the_file_name_when_there_is_no_name(self):
        role = roles.parse("---\ndescription: d\n---\nbody\n", fallback_name="from-file")
        self.assertEqual(role.name, "from-file")

    def test_read_only_and_tools(self):
        self.assertFalse(self.parse().read_only)  # every tool
        self.assertFalse(self.parse("tools: Read, Edit").read_only)
        self.assertTrue(self.parse("tools: Read, Bash(git *)").read_only)
        self.assertTrue(self.parse("disallowedTools: Write, Edit").read_only)
        role = self.parse("tools: Read, Bash(git *)")
        self.assertTrue(role.allows("Bash"))
        self.assertFalse(role.allows("WebFetch"))
        self.assertFalse(self.parse("disallowedTools: WebFetch").allows("WebFetch"))
        self.assertTrue(self.parse().allows("WebFetch"))

    def test_model_for_each_agent(self):
        self.assertEqual(self.parse("model: opus").model_for("claude"), "opus")
        self.assertIsNone(self.parse("model: opus").model_for("codex"))  # Claude's name means nothing there
        self.assertIsNone(self.parse("model: claude-opus-5-5").model_for("opencode"))
        self.assertEqual(self.parse("model: gpt-5").model_for("codex"), "gpt-5")
        self.assertIsNone(self.parse("model: inherit").model_for("claude"))


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.user = self.root / "config" / "roles"
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": str(self.root / "config" / "config.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_built_in_roles(self):
        found, problems = roles.load(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(set(found), set(roles.FLEET_ROLES))
        self.assertEqual({n for n, r in found.items() if r.read_only}, {"scout", "architect", "reviewer"})
        self.assertEqual({r.source for r in found.values()}, {"built-in"})
        for role in found.values():
            self.assertIn("If your task ends with a reply format", " ".join(role.prompt.split()))

    def test_project_then_yours_then_built_in(self):
        repo = self.root / "repo"
        sub = repo / "pkg" / "app"
        sub.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        role_file(self.user, "reviewer", "mine")
        role_file(self.user, "helper", "mine too")
        role_file(repo / ".claude" / "agents", "reviewer", "the repo's")
        role_file(repo / ".claude" / "agents" / "nested", "tester", "nested in the repo")
        role_file(sub / ".claude" / "agents", "tester", "closest")
        role_file(self.root / ".claude" / "agents", "outside", "above the repository")
        found, problems = roles.load(sub)
        self.assertEqual(problems, [])
        self.assertEqual(found["reviewer"].description, "the repo's")
        self.assertEqual((found["reviewer"].source, found["reviewer"].shadows), ("project", ["yours", "built-in"]))
        self.assertEqual(found["tester"].description, "closest")
        self.assertEqual(found["helper"].source, "yours")
        self.assertEqual(found["builder"].source, "built-in")
        self.assertNotIn("outside", found)  # stops at the repository's root
        # Without a folder, or outside any project: yours and the built-ins.
        self.assertEqual(roles.load()[0]["reviewer"].description, "mine")

    def test_a_folder_outside_a_repository_looks_only_at_itself(self):
        folder = self.root / "plain" / "inner"
        role_file(folder / ".claude" / "agents", "here")
        role_file(self.root / "plain" / ".claude" / "agents", "parent")
        found, _ = roles.load(folder)
        self.assertIn("here", found)
        self.assertNotIn("parent", found)

    def test_a_bad_file_is_a_problem_not_a_failure(self):
        self.user.mkdir(parents=True)
        (self.user / "broken.md").write_text("no frontmatter here\n")
        role_file(self.user, "fine")
        found, problems = roles.load()
        self.assertIn("fine", found)
        self.assertEqual(len(problems), 1)
        self.assertIn("broken.md", problems[0])

    def test_get(self):
        self.assertEqual(roles.get("scout").name, "scout")
        with self.assertRaises(roles.RoleError) as caught:
            roles.get("nobody")
        self.assertIn("builder", str(caught.exception))


class LaunchArgsTest(unittest.TestCase):
    def role(self, extra=""):
        return roles.parse(f"---\nname: checker\ndescription: Checks\n{extra}\n---\nCheck it. \"Quoted\" and ünïcode.\n")

    def test_claude(self):
        spec = roles.launch_args(self.role("tools: Read, Grep\ndisallowedTools: Bash\nmodel: opus\n"
                                           "permissionMode: acceptEdits\neffort: high"), "claude")
        self.assertEqual(spec["extra"][0], "--agents")
        definition = json.loads(spec["extra"][1])["checker"]
        self.assertEqual(definition, {"description": "Checks", "prompt": "Check it. \"Quoted\" and ünïcode.\n",
                                      "tools": ["Read", "Grep"], "disallowedTools": ["Bash"]})
        self.assertEqual(spec["extra"][2:], ["--agent", "checker", "--effort", "high"])
        self.assertEqual((spec["model"], spec["permission_mode"], spec["env"]), ("opus", "acceptEdits", {}))
        self.assertNotIn("tools", json.loads(roles.launch_args(self.role(), "claude")["extra"][1])["checker"])

    def test_codex(self):
        spec = roles.launch_args(self.role("tools: Read\nmodel: opus\npermissionMode: acceptEdits"), "codex")
        self.assertEqual(spec["extra"][0], "-c")
        key, _, value = spec["extra"][1].partition("=")
        self.assertEqual(key, "developer_instructions")
        # Codex parses the value as TOML.
        self.assertEqual(tomllib.loads(f"x = {value}")["x"], "Check it. \"Quoted\" and ünïcode.\n")
        self.assertEqual(spec["extra"][2:], ["-s", "read-only"])
        self.assertEqual((spec["model"], spec["permission_mode"]), (None, None))  # Claude's, not Codex's
        self.assertNotIn("-s", roles.launch_args(self.role(), "codex")["extra"])

    def test_opencode(self):
        existing = json.dumps({"agent": {"mine": {"prompt": "keep"}}, "theme": "x"})
        spec = roles.launch_args(self.role("tools: Read, Grep, WebFetch"), "opencode",
                                 {"OPENCODE_CONFIG_CONTENT": existing})
        self.assertEqual(spec["extra"], ["--agent", "omaorchestra-checker"])
        content = json.loads(spec["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(content["theme"], "x")
        self.assertEqual(content["agent"]["mine"], {"prompt": "keep"})
        definition = content["agent"]["omaorchestra-checker"]
        self.assertEqual((definition["mode"], definition["prompt"]), ("primary", "Check it. \"Quoted\" and ünïcode.\n"))
        self.assertEqual(definition["permission"], {"edit": "deny", "bash": "deny", "websearch": "deny"})
        everything = json.loads(roles.launch_args(self.role(), "opencode")["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("permission", everything["agent"]["omaorchestra-checker"])
        # In auto mode (a fleet's default) what opencode would ask is answered for it.
        auto = roles.launch_args(self.role("tools: Read, Grep"), "opencode", permission_mode="auto")
        self.assertEqual(json.loads(auto["env"]["OPENCODE_CONFIG_CONTENT"])["agent"]["omaorchestra-checker"]
                         ["permission"], {**roles.OPENCODE_AUTO, "edit": "deny", "bash": "deny", "websearch": "deny",
                                          "webfetch": "deny"})
        broken = roles.launch_args(self.role(), "opencode", {"OPENCODE_CONFIG_CONTENT": "not json"})
        self.assertIn("agent", json.loads(broken["env"]["OPENCODE_CONFIG_CONTENT"]))


class RunWithRoleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_CLAUDE": "true", "OMAORCHESTRA_OPENCODE": "true",
                                                "OMAORCHESTRA_CONFIG": str(self.root / "config" / "config.toml"),
                                                "OMAORCHESTRA_STATE_DIR": str(self.root / "state")})
        self.env.start()
        self.sent, self.spawned = [], []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_task(self, **kw):
        return launch.run("review it", self.tmp.name, spawn=lambda cmd, **k: self.spawned.append((cmd, k["env"])),
                          request=self.sent.append, worktree=False, **kw)

    def test_claude_runs_as_the_role(self):
        self.run_task(role="reviewer")
        command, env = self.spawned[0]
        self.assertEqual(command[command.index("--agent") + 1], "reviewer")
        self.assertEqual(command[command.index("--model") + 1], "opus")
        self.assertIn("--agents", command)
        self.assertEqual(command[-1], "review it")
        self.assertEqual((self.sent[0]["role"], self.sent[0]["model"]), ("reviewer", "opus"))

    def test_what_you_give_wins_over_the_role(self):
        self.run_task(role="reviewer", model="haiku", permission_mode="plan", extra=["--verbose"])
        command, _ = self.spawned[0]
        self.assertEqual(command[command.index("--model") + 1], "haiku")
        self.assertEqual(command[command.index("--permission-mode") + 1], "plan")
        self.assertLess(command.index("--agent"), command.index("--verbose"))

    def test_opencode_gets_the_role_in_its_environment(self):
        self.run_task(role="scout", agent="opencode")
        command, env = self.spawned[0]
        self.assertEqual(command[command.index("--agent") + 1], "omaorchestra-scout")
        self.assertNotIn("-m", command)  # the scout's model is Claude's
        self.assertIn("omaorchestra-scout", json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"])

    def test_unknown_role(self):
        with self.assertRaises(launch.LaunchError) as caught:
            self.run_task(role="nobody")
        self.assertIn("no role nobody", str(caught.exception))
        self.assertEqual(self.spawned, [])

    def test_cli_takes_the_roles_agent(self):
        role_file(self.root / "config" / "roles", "coder", extra="agent: codex")
        captured = {}

        def fake_run(task, cwd, **kw):
            captured.update(kw)
            return {"id": "abcdef1234", "tracked": True, "worktree": None, "note": ""}
        with mock.patch.object(launch, "run", fake_run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "x", "--in", self.tmp.name, "--role", "coder"]), 0)
            self.assertEqual((captured["agent"], captured["role"]), ("codex", "coder"))
            self.assertEqual(main(["run", "x", "--in", self.tmp.name, "--role", "coder", "--agent", "claude"]), 0)
            self.assertEqual(captured["agent"], "claude")
            self.assertEqual(main(["run", "x", "--in", self.tmp.name]), 0)
            self.assertEqual((captured["agent"], captured["role"]), ("claude", None))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["run", "x", "--in", self.tmp.name, "--role", "nobody"]), 1)
        self.assertIn("no role nobody", err.getvalue())


class RoleCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": str(self.root / "config" / "config.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_list_show_check(self):
        role_file(self.root / "config" / "roles", "reviewer", "My own reviewer", extra="model: sonnet")
        code, out, _ = self.cli("role", "list", "--in", self.tmp.name)
        self.assertEqual(code, 0)
        lines = [line.split()[0] for line in out.splitlines() if line and not line.startswith(" ")]
        self.assertEqual(lines[:5], list(roles.FLEET_ROLES))  # the fleet's order
        self.assertIn("(yours, over built-in)", out)
        code, out, _ = self.cli("role", "show", "scout", "--in", self.tmp.name)
        self.assertIn("read-only", out)
        self.assertIn("You are the **scout**", out)
        code, out, _ = self.cli("role", "show", "reviewer", "--json", "--in", self.tmp.name)
        shown = json.loads(out)
        self.assertEqual((shown["source"], shown["model"], shown["shadows"]), ("yours", "sonnet", ["built-in"]))
        self.assertEqual(self.cli("role", "check", "--in", self.tmp.name)[0], 0)
        (self.root / "config" / "roles" / "bad.md").write_text("---\nname: bad\n---\n")
        code, out, _ = self.cli("role", "check", "--in", self.tmp.name)
        self.assertEqual(code, 1)
        self.assertIn("no prompt", out)
        code, _, err = self.cli("role", "list", "--in", self.tmp.name)
        self.assertEqual(code, 0)
        self.assertIn("bad.md", err)
        self.assertEqual(self.cli("role", "show", "nobody")[0], 1)


class RecipeRoleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_CONFIG": str(self.root / "config.toml")})
        self.env.start()
        role_file(self.root / "roles", "coder", extra="agent: codex")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, text):
        path = self.root / "recipes.toml"
        path.write_text(text)
        return path

    def test_a_step_runs_as_a_role(self):
        self.write('[recipes.x]\n[[recipes.x.steps]]\nprompt = "{task}"\nrole = "coder"\n'
                   '[[recipes.x.steps]]\nprompt = "check {task}"\nrole = "reviewer"\nreview = true\n'
                   '[[recipes.x.steps]]\nprompt = "again {task}"\nrole = "reviewer"\nmodel = "haiku"\n')
        first, second, third = recipes.items("x", "do it", self.tmp.name, base={"model": "sonnet", "agent": "opencode"})
        self.assertEqual((first["role"], first["agent"], first["model"]), ("coder", "codex", "sonnet"))
        # The reviewer names its model, so `--model` (for steps that name none) leaves it alone.
        self.assertEqual((second["role"], second["agent"]), ("reviewer", "claude"))
        self.assertNotIn("model", second)
        self.assertEqual(third["model"], "haiku")

    def test_bad_roles(self):
        self.write('[recipes.x]\n[[recipes.x.steps]]\nprompt = "{task}"\nrole = "nobody"\n')
        with self.assertRaises(recipes.RecipeError) as caught:
            recipes.items("x", "t", self.tmp.name)
        self.assertIn("step 1: no role nobody", str(caught.exception))
        self.write('[recipes.x]\n[[recipes.x.steps]]\nprompt = "{task}"\nrole = 3\n')
        with self.assertRaises(recipes.RecipeError):
            recipes.load()


class DaemonRoleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "work"
        self.folder.mkdir()
        self.env = mock.patch.dict(os.environ, {"OMAORCHESTRA_STATE_DIR": os.path.join(self.tmp.name, "state"),
                                                "OMAORCHESTRA_CONFIG": os.path.join(self.tmp.name, "c.toml")})
        self.env.start()
        self.d = daemon.Daemon(Registry(Path(self.tmp.name) / "state" / "sessions.json"), is_alive=lambda p, s: False)
        self.spawned = []
        self.d.spawn = lambda cmd, **kw: self.spawned.append(cmd)
        self.d.usage_check = lambda agent, threshold: None
        self.d.usage_refresh = lambda agent: None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_a_queued_role_starts_as_it_and_is_remembered(self):
        item = {"task": "look around", "cwd": str(self.folder), "worktree": False, "agent_bin": "true",
                "path": "/usr/bin:/bin", "role": "scout"}
        self.assertTrue(self.d.handle({"cmd": "queue-add", "item": item})["ok"])
        command = self.spawned[0]
        self.assertEqual(command[command.index("--agent") + 1], "scout")
        sid, session = next(iter(self.d.registry.sessions.items()))
        self.assertEqual(session["role"], "scout")
        record = history.build({**session, "id": sid}, "gone", ended=session["updated"], cost_fn=lambda s: None)
        self.assertEqual(record["role"], "scout")


if __name__ == "__main__":
    unittest.main()
