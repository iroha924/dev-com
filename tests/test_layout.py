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

    def test_uninstall_lists_every_skill(self):
        # 削除の手順の skill のフォルダの一覧が skills/ と同じ（足した skill を消し忘れて、Codex の候補に残さない）
        notes = read(os.path.join(ROOT, "docs", "notes.md"))
        line = next(l for l in notes.split("## 更新と削除", 1)[1].splitlines() if "$HOME\\.agents\\skills\\` の下" in l)
        self.assertEqual(sorted(re.findall(r"`([\w-]+)`", line.split("の下", 1)[1])), skill_dirs())
        self.assertEqual(int(re.search(r"の下の (\d+) 個", line).group(1)), len(skill_dirs()))

    def test_skill_counts_match(self):
        # 本文に書いた skill の個数が skills/ の数と同じ
        for path in (os.path.join(ROOT, "README.md"), os.path.join(ROOT, "docs", "notes.md")):
            for n in re.findall(r"(?:skill |`/skills`[^。|\n]*?)(\d+) 個", read(path)):
                with self.subTest(path=os.path.basename(path), n=n):
                    self.assertEqual(int(n), len(skill_dirs()))

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


WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
# YAML のライブラリは使わず（標準ライブラリだけで動かす）、許す書き方を 1 行ずつの文法で狭く決め、それ以外は失敗にする。
# 引用符やエスケープのキー、アンカー・エイリアス・フローの書き方は、行で見る検査をすり抜けるので許さない
KEY_LINE = re.compile(r"^( *)(- )?([A-Za-z0-9_-]+):(?: (.+))?$")
ITEM_LINE = re.compile(r"^( *)- ([A-Za-z0-9_./-]+)$")
PLAIN_VALUE = re.compile(r"""^(?:"[^"\\]*"|[^\s&*!%@`'"|>\[{\\#][^\\]*)$""")
BLOCK_SCALAR = {"|", "|-", ">", ">-"}
# owner/repo だけ。サブパスや . で始まる区切りは、actions/checkout/@ のように同じ action を別の綴りで指せるので許さない
USES = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*@[0-9a-f]{40}  # \S+$")
FORBIDDEN = [(re.compile(r"pull_request_target", re.I), "pull_request_target を使わない（fork の PR に書き込みの権限と secret が渡る）"),
             (re.compile(r"\bsecrets\b", re.I), "secret を使わない"),
             (re.compile(r"continue-on-error", re.I), "continue-on-error で落ちたテストを通さない"),
             (re.compile(r"\t"), "タブを使わない")]


def workflow_keys(text):
    """workflow をキーの行の一覧 (行番号, キーの列, キー, 値) にする。2 つ目は違反の一覧。run: | の中身は行の一覧に入れない。"""
    keys, errors, block = [], [], None
    for n, line in enumerate(text.splitlines(), 1):
        for pattern, why in FORBIDDEN:
            if pattern.search(line):
                errors.append("%d: %s" % (n, why))
        indent = len(line) - len(line.lstrip(" "))
        if block is not None:
            if not line.strip() or indent > block:
                continue
            block = None
        if not line.strip() or line.lstrip(" ").startswith("#"):
            continue
        if "<<" in line:  # run: | の中のシェルのヒアドキュメントは除いて、YAML の構造の行でだけ見る
            errors.append("%d: マージキーを使わない" % n)
        m = KEY_LINE.match(line)
        if m:
            col = len(m.group(1)) + (2 if m.group(2) else 0)
            value = m.group(4)
            if value in BLOCK_SCALAR:
                block = col
            elif value is not None and not PLAIN_VALUE.match(value):
                errors.append("%d: 値は引用符なしか、エスケープの無い \"...\" にする（アンカー・エイリアス・フローの書き方も使わない）" % n)
            keys.append((n, col, m.group(3), value, bool(m.group(2))))
        elif ITEM_LINE.match(line):
            keys.append((n, len(ITEM_LINE.match(line).group(1)) + 2, None, ITEM_LINE.match(line).group(2), True))
        else:
            errors.append("%d: 許していない書き方（キー: 値、- キー: 値、- 値 のどれか）" % n)
    for k, nxt in zip(keys, keys[1:]):
        if k[2] is not None and k[3] is not None and k[3] not in BLOCK_SCALAR and nxt[1] > k[1]:
            errors.append("%d: 値を書いたキーの下に、さらに深い行を書かない" % nxt[0])
    return keys, errors


def children(keys, i):
    """keys[i] の直下のキー（keys[i] より深い列のうち、最初に出た列のもの）。keys[i] と同じか浅い列が出たら終わる。"""
    out = []
    for k in keys[i + 1:]:
        if k[1] <= keys[i][1]:
            break
        if not out or k[1] == out[0][1]:
            out.append(k)
    return out


def step_siblings(keys, i):
    """keys[i] と同じ手順（- で始まる 1 つの項目）の、同じ列のキー。手順の中でなければ空。"""
    col, start = keys[i][1], i
    while not (keys[start][1] == col and keys[start][4]):
        start -= 1
        if start < 0 or keys[start][1] < col:
            return []
    out = [keys[start]]
    for k in keys[start + 1:]:
        if k[1] < col or (k[1] == col and k[4]):
            break
        if k[1] == col:
            out.append(k)
    return out


def workflow_errors(text):
    """workflow の action の固定と権限の崩れ。違反の説明の一覧（空なら通る）。"""
    keys, errors = workflow_keys(text)
    for i, (n, col, key, value, _) in enumerate(keys):
        if key == "uses" and not USES.match(value or ""):
            errors.append("%d: uses は owner/repo@<40 桁の SHA>  # <版> の形で固定する" % n)
        if key == "permissions" and (col != 0 or value is not None):
            errors.append("%d: permissions はトップレベルの 1 か所だけにする（ジョブで上書きしない）" % n)
        if key == "uses" and (value or "").lower().split("@")[0] == "actions/checkout":
            with_i = next((keys.index(k) for k in step_siblings(keys, i) if k[2] == "with"), None)
            if with_i is None or ("persist-credentials", "false") not in [(k[2], k[3]) for k in children(keys, with_i)]:
                errors.append("%d: actions/checkout の with: に persist-credentials: false を付ける（トークンを残さない）" % n)
    tops = [i for i, k in enumerate(keys) if k[2] == "permissions" and k[1] == 0]
    if len(tops) != 1 or [(k[2], k[3]) for k in children(keys, tops[0])] != [("contents", "read")]:
        errors.append("permissions: トップレベルに 1 か所、中身は contents: read だけにする")
    return errors


GOOD_WORKFLOW = """name: tests
on:
  pull_request:
permissions:
  contents: read
jobs:
  t:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          persist-credentials: false
      - name: setup
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97  # v7.0.0
        with:
          python-version: "3.11"
      - run: python -m unittest discover -s tests
"""


class WorkflowTest(unittest.TestCase):
    def test_workflows_pin_actions_and_keep_permissions_read_only(self):
        files = sorted(f for f in os.listdir(WORKFLOWS) if f.endswith((".yml", ".yaml"))) if os.path.isdir(WORKFLOWS) else []
        for f in files:
            with self.subTest(workflow=f):
                self.assertEqual(workflow_errors(read(os.path.join(WORKFLOWS, f))), [])

    def test_the_check_catches_each_break(self):
        self.assertEqual(workflow_errors(GOOD_WORKFLOW), [])
        sha = "3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1"
        breaks = [
            ("uses の固定", lambda w: w.replace("checkout@" + sha, "checkout@v7")),
            ("SHA の後の版", lambda w: w.replace("  # v7.0.1", "")),
            ("ジョブの permissions", lambda w: w.replace("    runs-on:", "    permissions: write-all\n    runs-on:")),
            ("トップの permissions", lambda w: w.replace("  contents: read", "  contents: write")),
            ("permissions の追加", lambda w: w.replace("  contents: read\n", "  contents: read\n  pull-requests: write\n")),
            ("persist-credentials", lambda w: w.replace("          persist-credentials: false\n", "")),
            ("secret", lambda w: w.replace("python -m", "TOKEN=${{ secrets.X }} python -m")),
            ("pull_request_target", lambda w: w.replace("  pull_request:", "  pull_request_target:")),
            ("continue-on-error", lambda w: w.replace("      - run:", "      - continue-on-error: true\n        run:")),
            ("アンカー", lambda w: w.replace("    steps:", "    steps: &s")),
            ("フロー", lambda w: w.replace("      - name: setup\n        uses:", "      - {name: setup, uses:")),
            ("引用符の uses", lambda w: w.replace("        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97  # v7.0.0",
                                                 '        "uses": actions/setup-python@v7')),
            ("引用符の permissions", lambda w: w.replace("    runs-on:", '    "permissions": write-all\n    runs-on:')),
            ("エスケープした禁止語", lambda w: w.replace("  pull_request:", '  "pull_request_targe\\u0074":')),
            ("エスケープした secret", lambda w: w.replace("      - run: python", '      - env:\n          T: "${{ secrets\\u002eX }}"\n        run: python')),
            ("空行の後の権限", lambda w: w.replace("  contents: read\n", "  contents: read\n\n  actions: write\n")),
            ("コメントの後の権限", lambda w: w.replace("  contents: read\n", "  contents: read\n# x\n  actions: write\n")),
            ("文字列の中の persist-credentials", lambda w: w.replace(
                "          persist-credentials: false\n",
                "          persist-credentials: true\n        name: |\n          persist-credentials: false\n")),
            ("末尾の / の checkout", lambda w: w.replace("actions/checkout@", "actions/checkout/@").replace("          persist-credentials: false\n", "")),
            ("値の下の深い行", lambda w: w.replace("  contents: read\n", "  contents: read\n    actions: write\n")),
            ("大文字の checkout", lambda w: w.replace("actions/checkout@", "Actions/Checkout@").replace("          persist-credentials: false\n", "")),
        ]
        for name, mutate in breaks:
            with self.subTest(name=name):
                broken = mutate(GOOD_WORKFLOW)
                self.assertNotEqual(broken, GOOD_WORKFLOW)
                self.assertNotEqual(workflow_errors(broken), [])

    def test_the_check_accepts_plain_shell_and_comments(self):
        # run の中のシェルの * や、権限の中のコメントを、YAML の書き方の違反と取り違えない
        for name, w in (("シェルの *", GOOD_WORKFLOW.replace("      - run: python", "      - run: echo *test.py && python")),
                        ("権限の中のコメント", GOOD_WORKFLOW.replace("  contents: read\n", "  contents: read\n  # 読むだけ\n")),
                        ("run の中のヒアドキュメント", GOOD_WORKFLOW.replace("      - run: python -m unittest discover -s tests\n",
                                                                     "      - run: |\n          cat <<EOF\n          hello\n          EOF\n"))):
            with self.subTest(name=name):
                self.assertEqual(workflow_errors(w), [])


if __name__ == "__main__":
    unittest.main()
