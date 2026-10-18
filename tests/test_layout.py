"""パッケージの形を検査する。skill を足したり書き換えたりしたら流す。"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
AGENTS = os.path.join(ROOT, "agents")

# ado skill の「書く」の手順で ADO へ書き込む skill。名前で呼んだときだけ動くようにする。
WRITE_SKILLS = {"pr-evidence-pack", "work-item-ready", "pr-review-assist", "incident-report"}
# 書き込まないが、子を起動して何往復もする重い skill。
MANUAL_SKILLS = WRITE_SKILLS | {"grill", "deep-latest"}
# 書き込みは本題ではなく、ユーザーが送るよう頼んだときだけ ado skill の「書く」へ渡す skill。自動で選ばれてよい。
# 書き込みの手順を持つ ado skill 自体が自動で選ばれるので、この 2 つを手動にしても書き込みの経路は減らない。
# 送る前に人が承認することを機械で止めるのは、ado.ps1 の write を常に許可にしていないときの Codex の承認だけ。
AUTO_SKILLS_THAT_MAY_SEND = {"review-triage", "writing-quality"}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return m.group(1) if m else None


def markdown_files():
    return sorted(
        os.path.join(root, f) for root, _, files in os.walk(SKILLS) for f in files if f.endswith(".md")
    )


def skill_dirs():
    return sorted(d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)))


class SkillLayoutTest(unittest.TestCase):
    def test_cleanup_approval_rule_is_present(self):
        text = read(os.path.join(ROOT, "AGENTS.md"))
        self.assertEqual(text.count("<!-- invariant: temp-cleanup-needs-approval -->"), 1)

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
            writes = name != "ado" and name not in AUTO_SKILLS_THAT_MAY_SEND and "「書く」" in text
            with self.subTest(skill=name):
                self.assertEqual(writes, name in WRITE_SKILLS, "WRITE_SKILLS と SKILL.md の書き込み手順の参照が食い違う")
                if name in WRITE_SKILLS:
                    self.assertTrue(os.path.isfile(yaml_path))
                    self.assertIn("allow_implicit_invocation: false", read(yaml_path))

    def test_description_is_a_safe_plain_scalar(self):
        # 引用符の無い YAML の値では、" #" から後ろがコメントとして消え、": " は読み込みの失敗になる。
        for name in skill_dirs():
            fm = frontmatter(read(os.path.join(SKILLS, name, "SKILL.md")))
            desc = re.search(r"(?m)^description: (.+)$", fm).group(1)
            with self.subTest(skill=name):
                self.assertNotIn(" #", desc)
                self.assertNotIn(": ", desc)
                self.assertNotRegex(desc, r"^[\[\]{}>|*&!%@`'\"#,?:-]")

    def test_descriptions_fit_the_initial_list(self):
        # Codex は起動時に読む skill の一覧を文脈の 2%（分からなければ 8,000 文字）に収め、超えると description を短く切る
        # （https://learn.chatgpt.com/docs/build-skills）。手元の他の skill の分を残すため、このパッケージは 6,000 文字まで。
        # 一覧の 1 行は「- 名前: 説明 (file: パス)」の形で、パスも数に入る。パスは長めの Windows のホームで見積もる。
        total = 0
        for name in skill_dirs():
            fm = frontmatter(read(os.path.join(SKILLS, name, "SKILL.md")))
            desc = re.search(r"(?m)^description: (.+)$", fm).group(1)
            path = "C:\\Users\\%s\\.agents\\skills\\%s\\SKILL.md" % ("x" * 20, name)
            total += len("- %s: %s (file: %s)\n" % (name, desc, path))
        self.assertLessEqual(total, 6000)

    def test_manual_skills_match_openai_yaml(self):
        for name in skill_dirs():
            yaml_path = os.path.join(SKILLS, name, "agents", "openai.yaml")
            manual = os.path.isfile(yaml_path) and "allow_implicit_invocation: false" in read(yaml_path)
            with self.subTest(skill=name):
                self.assertEqual(manual, name in MANUAL_SKILLS)

    def test_spawned_agents_do_not_fork_history(self):
        # spawn_agent の fork_turns は既定が "all" で、親の会話を全部引き継ぐ。子を起動する段落ごとに "none" を書く。
        # 起動する段落は「custom agent の `名前`」か「agent_type: "名前"」で役を名指しする。
        spawn = re.compile(r"custom agent の `[\w-]+`|agent_type: \"[\w-]+\"")
        checked = 0
        for path in markdown_files():
            for para in re.split(r"\n\s*\n", read(path)):
                if spawn.search(para):
                    checked += 1
                    with self.subTest(path=os.path.relpath(path, ROOT), para=para[:60]):
                        self.assertIn('fork_turns: "none"', para, '子を起動する段落に fork_turns: "none" が無い')
        # 検査の対象が 0 件になって黙って通るのを防ぐ（grill、writing-quality と review.md、pr-review-assist、incident-report）。
        self.assertGreaterEqual(checked, 5)

    def test_code_fences_are_well_formed(self):
        # 閉じのつもりのフェンスの後ろに文字が続くと閉じにならず、後ろの見出しや表がコードとして表示される
        # （CommonMark 4.5: 閉じは開きと同じ文字で同じ長さ以上、後ろは空白だけ）。入れ子のフェンスはこのリポジトリでは使わない。
        fence_re = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
        for path in markdown_files() + [os.path.join(ROOT, n) for n in ("README.md", "AGENTS.md", os.path.join("docs", "notes.md"))]:
            fence = None
            for n, line in enumerate(read(path).splitlines(), 1):
                m = fence_re.match(line)
                if fence is None:
                    if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                        fence = m.group(1)
                elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                    with self.subTest(path=os.path.relpath(path, ROOT), line=n):
                        self.assertEqual(m.group(2).strip(), "", "フェンスの中に、後ろに文字が続くフェンスの行がある")
                    fence = None
            with self.subTest(path=os.path.relpath(path, ROOT)):
                self.assertIsNone(fence, "閉じていないコードフェンスがある")

    def test_each_skill_recommends_a_setting(self):
        # AGENTS.md の「モデルと effort の案内」は、skill を使い始めるときにこの行と今の設定を比べる。
        for name in skill_dirs():
            text = read(os.path.join(SKILLS, name, "SKILL.md"))
            with self.subTest(skill=name):
                m = re.search(r"(?m)^推奨の設定: (.+)$", text)
                self.assertIsNotNone(m, "推奨の設定の行が無い")
                pairs = re.findall(r"(gpt-[\w.-]+)・(\w+)", m.group(1))
                self.assertTrue(pairs, "モデル・effort の組が書かれていない")
                for model, effort in pairs:
                    self.assertIn(model, {"gpt-6-luna", "gpt-6.1-sol"})
                    self.assertIn(effort, {"low", "medium", "high"})

    def test_readme_lists_every_skill(self):
        # README の「一覧」の表に、skills/ の全部の skill の行がある（足した skill の書き忘れを止める）
        table = read(os.path.join(ROOT, "README.md")).split("## 一覧", 1)[1].split("\n## ", 1)[0]
        for name in skill_dirs():
            with self.subTest(skill=name):
                self.assertRegex(table, r"\| \[`%s`\]\([^)]+\) \| skill \|" % re.escape(name))

    def test_referenced_scripts_exist(self):
        pattern = re.compile(r"\$HOME\\\.agents\\skills\\([\w-]+)\\scripts\\([\w.]+)")
        for path in markdown_files():
            for m in pattern.finditer(read(path)):
                with self.subTest(path=os.path.relpath(path, ROOT), ref=m.group(0)):
                    self.assertTrue(os.path.isfile(os.path.join(SKILLS, m.group(1), "scripts", m.group(2))))

    def test_relative_links_exist(self):
        for name in skill_dirs():
            base = os.path.join(SKILLS, name)
            for m in re.finditer(r"\]\(([^)#:]+\.md)\)", read(os.path.join(base, "SKILL.md"))):
                with self.subTest(skill=name, link=m.group(1)):
                    self.assertTrue(os.path.isfile(os.path.normpath(os.path.join(base, m.group(1)))))

    def test_agents_mentioned_by_skills_exist(self):
        agents = {os.path.splitext(f)[0] for f in os.listdir(AGENTS)}
        pattern = re.compile(r"custom agent の `([\w-]+)`|agent_type: \"([\w-]+)\"|`agent_type` \| `\"([\w-]+)\"`")
        for path in markdown_files():
            for m in pattern.finditer(read(path)):
                name = next(g for g in m.groups() if g)
                with self.subTest(path=os.path.relpath(path, ROOT), agent=name):
                    self.assertIn(name, agents)


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
                # 子に効くキーのうち、このパッケージが使うものだけ（sandbox と承認は親を引き継ぎ、書いても効かない）。
                self.assertLessEqual(set(data), {"name", "description", "developer_instructions", "model", "model_reasoning_effort"})
                self.assertIn(data.get("model", "gpt-6-luna"), {"gpt-6-luna", "gpt-6.1-sol"})
                self.assertIn(data.get("model_reasoning_effort", "medium"), {"low", "medium", "high"})


    def test_reading_agents_use_the_light_model(self):
        # 大量に読む役は luna に固定する。消すと、何もしなくても減っていた分が黙って戻る。
        for name in ("researcher", "incident-evidence-analyst", "prose-reviewer"):
            with self.subTest(agent=name):
                self.assertRegex(read(os.path.join(AGENTS, name + ".toml")), r'(?m)^model = "gpt-6-luna"$')


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
