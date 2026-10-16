"""skills/session-search/scripts/session_search.py の、標準ライブラリだけで動く部分を確かめる。

一時的な CODEX_HOME に rollout を書き、取り込み・検索・show・forget・archive への移動・伏せ字を見る。
SudachiPy と埋め込みの層は、入っている環境でだけ確かめる（evals/session-search の評価が精度を見る）。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "session-search", "scripts", "session_search.py")
spec = importlib.util.spec_from_file_location("session_search", SCRIPT)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

T1 = "11111111-1111-4111-8111-111111111111"
T2 = "22222222-2222-4222-8222-222222222222"
T3 = "33333333-3333-4333-8333-333333333333"


def line(ts, typ, payload):
    return json.dumps({"timestamp": ts, "type": typ, "payload": payload}, ensure_ascii=False)


def paginated(tid, turns, cwd="C:\\work\\ci", originator="codex-tui", agent=None, ts="2026-09-01T01:00:00Z"):
    out = [line(ts, "session_meta", {"id": tid, "cwd": cwd, "originator": originator, "history_mode": "paginated",
                                     "cli_version": "0.162.1", "git": {"branch": "main"}, "agent_role": agent})]
    for n, (user, final, extra) in enumerate(turns, 1):
        turn = "%s-t%d" % (tid, n)
        out.append(line(ts, "turn_context", {"turn_id": turn, "cwd": cwd}))
        content = [{"type": "text", "text": user}] + (extra or [])
        out.append(line(ts, "event_msg", {"type": "item_completed", "turn_id": turn,
                                          "item": {"type": "UserMessage", "id": "u%d" % n, "content": content}}))
        if final is not None:
            out.append(line(ts, "event_msg", {"type": "item_completed", "turn_id": turn,
                                              "item": {"type": "AgentMessage", "id": "f%d" % n, "phase": "final_answer",
                                                       "content": [{"type": "Text", "text": final}]}}))
            out.append(line(ts, "event_msg", {"type": "task_complete", "turn_id": turn, "last_agent_message": final}))
    return "\n".join(out) + "\n"


class SessionSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "codex")
        os.makedirs(os.path.join(self.home, "sessions", "2026", "09", "01"))
        self.db = os.path.join(self.tmp.name, "data", "s.sqlite")
        self.env = dict(os.environ, CODEX_HOME=self.home, HIRATA_CODEX_SEARCH_DB=self.db,
                        HIRATA_CODEX_DATA=os.path.join(self.tmp.name, "data"), HIRATA_CODEX_SEARCH_REEXEC="test")

    def write(self, tid, text, sub=("sessions", "2026", "09", "01"), suffix=".jsonl"):
        path = os.path.join(self.home, *sub, "rollout-2026-09-01T01-00-00-%s%s" % (tid, suffix))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, encoding="utf-8", env=self.env)
        return r.returncode, r.stdout + r.stderr

    def search(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "search", "--json", "--budget", "0"] + list(args),
                           capture_output=True, encoding="utf-8", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def basic_corpus(self):
        self.write(T1, paginated(T1, [("CI の再実行はどうする？", "自動の再実行はしないと決めた。flaky は個別に直す。", None)]))
        self.write(T2, paginated(T2, [("ＥＣＯＮＮＲＥＳＥＴ が出る", "原因はロードバランサーの idle timeout 60 秒だった。", None),
                                      ("障害の報告を書いて", "障害報告: 9/26 に証明書が切れた。", None)]))

    # 取り込み

    def test_reads_users_finals_and_skips_images(self):
        extra = [{"type": "image", "image_url": "data:..."}, {"type": "local_image", "path": "C:\\a.png"}]
        meta, msgs, counters = ss.parse_rollout("x", paginated(T1, [("見て", "見た", extra)]).encode())
        self.assertEqual([m["kind"] for m in msgs], ["user", "final"])
        self.assertEqual((counters["skipped_elements"], counters["shape_errors"]), (2, 0))
        self.assertEqual(meta["thread_id"], T1)

    def test_unknown_elements_and_broken_lines_are_counted(self):
        text = paginated(T1, [("a", "b", [{"type": "audio", "data": "x"}])]) + "{broken\n"
        _, _, counters = ss.parse_rollout("x", text.encode())
        self.assertEqual((counters["shape_errors"], counters["bad_json"]), (1, 1))

    def test_task_complete_is_the_final_when_no_phase(self):
        text = "\n".join([
            line("2026-09-01T01:00:00Z", "session_meta", {"id": T1}),
            line("2026-09-01T01:00:00Z", "turn_context", {"turn_id": "t1"}),
            line("2026-09-01T01:00:00Z", "event_msg", {"type": "user_message", "message": "legacy の依頼"}),
            line("2026-09-01T01:00:00Z", "event_msg", {"type": "agent_message", "message": "途中"}),
            line("2026-09-01T01:00:00Z", "event_msg", {"type": "agent_message", "message": "最後の答え"}),
            line("2026-09-01T01:00:00Z", "event_msg", {"type": "task_complete", "turn_id": "t1", "last_agent_message": "最後の答え"}),
        ]) + "\n"
        _, msgs, _ = ss.parse_rollout("x", text.encode())
        self.assertEqual([(m["kind"], m["text"]) for m in msgs],
                         [("user", "legacy の依頼"), ("commentary", "途中"), ("final", "最後の答え")])

    def test_incomplete_last_line_is_left_for_later(self):
        text = paginated(T1, [("a", "b", None)])
        _, msgs, counters = ss.parse_rollout("x", (text + '{"timestamp": "x", "type": "ev').encode())
        self.assertEqual(counters["incomplete_tail"], 1)
        self.assertEqual(len(msgs), 2)

    # 検索

    def test_any_term_matches_and_more_terms_rank_higher(self):
        self.basic_corpus()
        out = self.search("再実行", "ロードバランサー")
        refs = [r["ref"].split("/")[0] for r in out["results"]]
        self.assertIn(T1, refs)
        self.assertIn(T2, refs)
        self.assertTrue("trigram" in out["mode"] or "SudachiPy" in out["mode"], out["mode"])

    def test_two_character_words_and_fullwidth(self):
        self.basic_corpus()
        self.assertEqual(self.search("障害")["results"][0]["ref"].split("/")[0], T2)
        self.assertEqual(self.search("econnreset")["results"][0]["ref"].split("/")[0], T2)

    def test_hyphen_terms_and_phrases(self):
        self.write(T1, paginated(T1, [("--no-verify を使わない理由", "フックを飛ばすので使わない。", None)]))
        self.assertTrue(self.search("--", "--no-verify")["results"])
        self.assertTrue(self.search('"フックを飛ばす"')["results"])
        code, out = self.run_cli("search", '"閉じていない')
        self.assertEqual(code, 2)

    def test_agents_are_hidden_unless_asked(self):
        self.write(T3, paginated(T3, [("子の依頼", "子の答え 再実行", None)], agent="researcher"))
        self.assertFalse(self.search("再実行")["results"])
        self.assertTrue(self.search("--include-agents", "再実行")["results"])

    def test_show_context_and_continuation(self):
        long = "前置き" * 600 + " 目的の語 " + "後ろ" * 600
        self.write(T1, paginated(T1, [("最初", "一つ目の答え", None), ("二つ目", long, None)]))
        code, out = self.run_cli("show", "%s/f2" % T1, "--around", "目的の語", "--length", "200")
        self.assertEqual(code, 0, out)
        self.assertIn("目的の語", out)
        self.assertIn("続きは --offset", out)
        self.assertIn("一つ目の答え", out)  # 前のターンも出る

    def test_forget_hides_and_survives_rebuild(self):
        self.basic_corpus()
        self.assertEqual(self.run_cli("forget", T1)[0], 0)
        self.assertFalse(self.search("再実行")["results"])
        self.assertEqual(self.run_cli("rebuild")[0], 0)
        self.assertFalse(self.search("再実行")["results"])
        self.assertTrue(self.search("障害")["results"])

    def test_archive_move_keeps_one_copy_and_missing_original_is_kept(self):
        path = self.write(T1, paginated(T1, [("再実行の方針", "しない", None)]))
        self.assertEqual(len(self.search("再実行")["results"]), 1)
        dest = os.path.join(self.home, "archived_sessions", os.path.basename(path))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest)
        self.assertEqual(len(self.search("再実行")["results"]), 1)
        os.remove(dest)
        out = self.search("再実行")
        self.assertEqual(len(out["results"]), 1)
        self.assertTrue(any("原本が見つからない" in c for c in out["coverage"]))

    def test_compressed_files_are_reported_when_unreadable(self):
        if ss.zstd is not None:
            self.skipTest("この Python は zstd を読める")
        self.write(T1, "not really zstd", suffix=".jsonl.zst")
        out = self.search("何か")
        self.assertTrue(any("圧縮された記録 1 本" in c for c in out["coverage"]))

    def test_memories_are_searched(self):
        os.makedirs(os.path.join(self.home, "memories"))
        with open(os.path.join(self.home, "memories", "notes.md"), "w", encoding="utf-8") as f:
            f.write("# 決めたこと\n\nCI の自動の再実行はしない。\n")
        out = self.search("再実行")
        self.assertEqual(out["results"][0]["kind"], "memory")
        self.assertIsNone(out["results"][0]["resume"])

    # 伏せ字

    def test_redaction(self):
        pat = "a" * 30 + "b2c3d4e5f6g7h2i3j4k5l6"  # 52 文字の小文字と数字
        self.assertNotIn(pat, ss.redact("PAT は %s です" % pat))
        self.assertNotIn("s3cret!", ss.redact('$password = "s3cret!"'))
        self.assertNotIn("abcdefghijklmnop", ss.redact("Authorization: Bearer abcdefghijklmnopqrstu"))
        self.assertNotIn("hunter2", ss.redact("https://me:hunter2@tfs.example.local/tfs"))
        keep = "commit 3577dd9f0a1b2c3d4e5f60718293a4b5c6d7e8f9 と id 01a14340-8384-7012-8936-16e067cdbdd1"
        self.assertEqual(ss.redact(keep), keep)

    def test_secrets_are_redacted_before_storing(self):
        self.write(T1, paginated(T1, [("token=abcd1234efgh の件", "password: hunter22 は消した", None)]))
        out = self.search("件")
        text = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("abcd1234efgh", text)
        self.assertNotIn("hunter22", text)


class SemanticLayerTest(unittest.TestCase):
    """SudachiPy が入っている環境でだけ、単語の BM25 の経路を確かめる。"""

    def setUp(self):
        try:
            import sudachipy  # noqa: F401
        except ImportError:
            self.skipTest("SudachiPy が無い")

    def test_word_bm25_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "codex")
            path = os.path.join(home, "sessions", "rollout-2026-09-01T01-00-00-%s.jsonl" % T1)
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as f:
                f.write(paginated(T1, [("パイプラインのキャッシュを外した理由", "復元が遅かったので外した。", None)]))
            os.environ["HIRATA_CODEX_DATA"] = os.path.join(tmp, "data")  # モデルは無い
            idx = ss.Index(os.path.join(tmp, "s.sqlite"))
            idx.ingest(home)
            sem = ss.Semantic()
            idx.fill_words(sem.tokenizer(), home)
            results, _, mode = idx.search(home, ss.parse_query(["キャッシュ"]), ss.DEFAULT_KINDS, sem=sem)
            self.assertIn("SudachiPy", mode)
            self.assertNotIn("埋め込み", mode)
            self.assertTrue(results)
            idx.close()


if __name__ == "__main__":
    unittest.main()
