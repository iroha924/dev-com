"""パッケージの形を検査する。skill を足したり書き換えたりしたら流す。"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
AGENTS = os.path.join(ROOT, "agents")

# ado skill の「書く」の手順で ADO へ書き込む skill。名前で呼んだときだけ動くようにする。
WRITE_SKILLS = {"pr-evidence-pack", "work-item-ready", "pr-review-assist", "incident-report"}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return m.group(1) if m else None


def skill_dirs():
    return sorted(d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)))


class SkillLayoutTest(unittest.TestCase):
    def test_frontmatter(self):
        for name in skill_dirs():
            with self.subTest(skill=name):
                fm = frontmatter(read(os.path.join(SKILLS, name, "SKILL.md")))
                self.assertIsNotNone(fm, "frontmatter が無い")
                self.assertRegex(fm, r"(?m)^name: %s$" % re.escape(name))
                desc = re.search(r"(?m)^description: (.+)$", fm)
                self.assertIsNotNone(desc)
                self.assertLessEqual(len(desc.group(1)), 1024)
                self.assertLessEqual(len(name), 64)

    def test_write_skills_are_explicit_only(self):
        for name in skill_dirs():
            text = read(os.path.join(SKILLS, name, "SKILL.md"))
            yaml_path = os.path.join(SKILLS, name, "agents", "openai.yaml")
            writes = name != "ado" and "「書く」" in text
            with self.subTest(skill=name):
                self.assertEqual(writes, name in WRITE_SKILLS, "WRITE_SKILLS と SKILL.md の書き込み手順の参照が食い違う")
                if name in WRITE_SKILLS:
                    self.assertTrue(os.path.isfile(yaml_path))
                    self.assertIn("allow_implicit_invocation: false", read(yaml_path))

    def test_referenced_scripts_exist(self):
        pattern = re.compile(r"\$HOME\\\.agents\\skills\\([\w-]+)\\scripts\\([\w.]+)")
        for name in skill_dirs():
            for m in pattern.finditer(read(os.path.join(SKILLS, name, "SKILL.md"))):
                with self.subTest(skill=name, ref=m.group(0)):
                    self.assertTrue(os.path.isfile(os.path.join(SKILLS, m.group(1), "scripts", m.group(2))))

    def test_relative_links_exist(self):
        for name in skill_dirs():
            base = os.path.join(SKILLS, name)
            for m in re.finditer(r"\]\(([^)#:]+\.md)\)", read(os.path.join(base, "SKILL.md"))):
                with self.subTest(skill=name, link=m.group(1)):
                    self.assertTrue(os.path.isfile(os.path.normpath(os.path.join(base, m.group(1)))))

    def test_agents_mentioned_by_skills_exist(self):
        agents = {os.path.splitext(f)[0] for f in os.listdir(AGENTS)}
        for name in skill_dirs():
            for m in re.finditer(r"custom agent の `([\w-]+)`", read(os.path.join(SKILLS, name, "SKILL.md"))):
                with self.subTest(skill=name, agent=m.group(1)):
                    self.assertIn(m.group(1), agents)


class AgentLayoutTest(unittest.TestCase):
    def test_required_keys(self):
        for f in sorted(os.listdir(AGENTS)):
            text = read(os.path.join(AGENTS, f))
            with self.subTest(agent=f):
                self.assertRegex(text, r'(?m)^name = "%s"$' % re.escape(os.path.splitext(f)[0]))
                self.assertRegex(text, r'(?m)^description = ".+"$')
                self.assertRegex(text, r'(?m)^developer_instructions = """$')
                try:
                    import tomllib
                except ImportError:
                    continue
                data = tomllib.loads(text)
                self.assertEqual(set(data), {"name", "description", "developer_instructions"})


class PowerShellFilesTest(unittest.TestCase):
    def test_ps1_are_ascii(self):
        # Windows PowerShell 5.1 は BOM の無い非 ASCII を cp932 として読む。
        for root, _, files in os.walk(ROOT):
            for f in files:
                if f.endswith(".ps1"):
                    path = os.path.join(root, f)
                    with self.subTest(path=os.path.relpath(path, ROOT)):
                        with open(path, "rb") as fh:
                            fh.read().decode("ascii")


if __name__ == "__main__":
    unittest.main()
