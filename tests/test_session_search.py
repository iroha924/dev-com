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


class _Base(unittest.TestCase):
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


class SessionSearchTest(_Base):
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


class ReviewFindingsTest(_Base):
    """レビューで見つかった欠陥の再現（直す前のコードで落ちることを確かめた）。"""

    def idx(self):
        return ss.Index(self.db)

    def test_quoted_and_json_secrets_are_redacted(self):
        for text, secret in [('password="correct horse battery staple"', "battery"),
                             ("password = ' spaced secret'", "spaced"),
                             ('{"Authorization": "Basic dXNlcjpwYXNz"}', "dXNlcjpwYXNz"),
                             ('{"token": "abcd efgh ijkl"}', "efgh")]:
            self.assertNotIn(secret, ss.redact(text), text)

    def test_hex_hashes_are_not_redacted(self):
        import hashlib
        for h in (hashlib.sha384(b"x").hexdigest(), hashlib.sha512(b"x").hexdigest()[:80]):
            self.assertEqual(ss.redact(h), h)

    def test_unknown_element_appended_to_ok_file_still_updates(self):
        path = self.write(T1, paginated(T1, [("最初の依頼", "最初の答え", None)]))
        self.assertTrue(self.search("最初の依頼")["results"])
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(paginated(T1, [("後から足した依頼 ふくろう", "後の答え", [{"type": "audio", "data": "x"}])]).split("\n", 1)[1])
        out = self.search("ふくろう")
        self.assertTrue(out["results"])
        self.assertTrue(any("形の分からない" in c for c in out["coverage"]), out["coverage"])

    def test_unreadable_zst_is_retried_when_reader_appears(self):
        path = self.write(T1, "x", suffix=".jsonl.zst")
        idx = self.idx()
        saved = ss.zstd
        try:
            ss.zstd = None
            self.assertEqual(idx.ingest(self.home)["zst_unreadable"], 1)

            class Fake:
                @staticmethod
                def decompress(_):
                    return paginated(T1, [("圧縮の中の依頼", "答え", None)]).encode()
            ss.zstd = Fake
            report = idx.ingest(self.home)
            self.assertEqual(report["zst_unreadable"], 0)
            self.assertEqual(report["updated"], 1)
        finally:
            ss.zstd = saved
            idx.close()

    def test_forgotten_memory_stays_forgotten_after_update(self):
        os.makedirs(os.path.join(self.home, "memories"))
        mem = os.path.join(self.home, "memories", "notes.md")
        with open(mem, "w", encoding="utf-8") as f:
            f.write("# 決めたこと\n\nかもめの方針\n")
        self.assertTrue(self.search("かもめ")["results"])
        self.assertEqual(self.run_cli("forget", "memory:notes.md")[0], 0)
        with open(mem, "a", encoding="utf-8") as f:
            f.write("追記\n")
        os.utime(mem, (1, 1))
        self.search("かもめ")
        idx = self.idx()
        self.assertEqual(idx.db.execute("SELECT COUNT(*) FROM messages WHERE thread_id = 'memory:notes.md'").fetchone()[0], 0)
        idx.close()

    def _two_homes(self):
        other = os.path.join(self.tmp.name, "codex2")
        for h in (self.home, other):
            os.makedirs(os.path.join(h, "memories"), exist_ok=True)
            with open(os.path.join(h, "memories", "notes.md"), "w", encoding="utf-8") as f:
                f.write("# メモ\n\nうみねこ %s\n" % os.path.basename(h))
        self.write(T1, paginated(T1, [("ひみつの依頼 うみねこ", "答え", None)]))
        self.search("うみねこ")
        env = dict(self.env, CODEX_HOME=other)
        subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "うみねこ"], capture_output=True, env=env)
        return other, env

    def test_forget_is_scoped_to_codex_home(self):
        other, env = self._two_homes()
        subprocess.run([sys.executable, SCRIPT, "forget", "memory:notes.md"], capture_output=True, env=env)
        kinds = [r["kind"] for r in self.search("うみねこ")["results"]]
        self.assertIn("memory", kinds)

    def test_show_is_scoped_to_codex_home(self):
        other, env = self._two_homes()
        r = subprocess.run([sys.executable, SCRIPT, "show", "%s/u1" % T1], capture_output=True, encoding="utf-8", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("ひみつの依頼", r.stdout)

    def test_cwd_filter_respects_folder_boundary(self):
        self.write(T1, paginated(T1, [("やまびこ 本体", "答え", None)], cwd="C:\\work\\app"))
        self.write(T2, paginated(T2, [("やまびこ 子", "答え", None)], cwd="C:\\work\\app\\child"))
        self.write(T3, paginated(T3, [("やまびこ 古い", "答え", None)], cwd="C:\\work\\app-old"))
        refs = {r["ref"].split("/")[0] for r in self.search("--cwd", "C:\\work\\app", "やまびこ")["results"]}
        self.assertEqual(refs, {T1, T2})

    def test_show_memory_in_subfolder(self):
        os.makedirs(os.path.join(self.home, "memories", "rollout_summaries"))
        with open(os.path.join(self.home, "memories", "rollout_summaries", "demo.md"), "w", encoding="utf-8") as f:
            f.write("# 要点\n\nつばめの話\n")
        ref = self.search("つばめ")["results"][0]["ref"]
        code, out = self.run_cli("show", ref)
        self.assertEqual(code, 0, out)
        self.assertIn("つばめ", out)

    def test_date_filter_with_fractional_seconds(self):
        bound = ss._date_arg("2026-10-01")
        frac = bound.replace("Z", ".123Z")
        self.write(T1, paginated(T1, [("ひばり", "答え", None)], ts=frac))
        self.assertTrue(self.search("--since", "2026-10-01", "ひばり")["results"])
        self.assertFalse(self.search("--until", "2026-09-30", "ひばり")["results"])

    def test_python_option_accepts_path_with_spaces(self):
        d = os.path.join(self.tmp.name, "First Last")
        os.makedirs(d)
        exe = os.path.join(d, "python.exe")
        open(exe, "w").close()
        self.assertEqual(ss.python_command(exe), [exe])
        self.assertEqual(ss.python_command("py -3.14"), ["py", "-3.14"])

    def test_model_dir_needs_the_files(self):
        os.makedirs(os.path.join(self.tmp.name, "data", "models", ss.EMBED_MODEL_DIR))
        os.environ["HIRATA_CODEX_DATA"] = os.path.join(self.tmp.name, "data")
        try:
            self.assertFalse(ss.model_ready())
        finally:
            del os.environ["HIRATA_CODEX_DATA"]


    def test_subagents_by_agent_type_and_source_are_hidden(self):
        a = paginated(T1, [("子の依頼 とんび", "答え", None)]).replace('"agent_role": null', '"agent_type": "worker"')
        b = paginated(T2, [("子の依頼 とんび", "答え", None)]).replace(
            '"agent_role": null', '"source": {"subagent": {"thread_spawn": {"parent_thread_id": "%s"}}}' % T3)
        c = paginated(T3, [("要約の依頼 とんび", "答え", None)]).replace('"agent_role": null', '"source": {"subagent": "compact"}')
        for tid, text in ((T1, a), (T2, b), (T3, c)):
            self.write(tid, text)
        self.assertFalse(self.search("とんび")["results"])
        self.assertEqual(len(self.search("--include-agents", "とんび")["results"]), 3)

    def test_review_sessions_are_searched_by_default(self):
        # /review は持ち主が始めた会話。前に何を指摘されたかを探したいので、既定の検索に出す
        self.write(T1, paginated(T1, [("レビューして はやぶさ", "指摘は 2 件", None)]).replace(
            '"agent_role": null', '"source": {"subagent": "review"}'))
        self.assertTrue(self.search("はやぶさ")["results"])

    def test_legacy_without_turn_id_marks_every_final(self):
        rows = [line("2026-09-01T01:00:00Z", "session_meta", {"id": T1})]
        for n in (1, 2, 3):
            rows += [line("2026-09-01T01:00:00Z", "event_msg", {"type": "user_message", "message": "依頼%d" % n}),
                     line("2026-09-01T01:00:00Z", "event_msg", {"type": "agent_message", "message": "答え%d" % n}),
                     line("2026-09-01T01:00:00Z", "event_msg", {"type": "task_complete", "last_agent_message": "答え%d" % n})]
        _, msgs, _ = ss.parse_rollout("x", ("\n".join(rows) + "\n").encode())
        self.assertEqual([m["kind"] for m in msgs if m["text"].startswith("答え")], ["final"] * 3)

    def test_two_files_of_one_thread_are_not_doubled(self):
        text = paginated(T1, [("ひよどりの依頼", "答え", None)])
        self.write(T1, text)
        self.write(T1, text, sub=("archived_sessions",))
        out = self.search("ひよどり")
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(len(self.search("--all-kinds", "ひよどり", "答え")["results"]), 2)
        code, shown = self.run_cli("show", "%s/u1" % T1)
        self.assertEqual(shown.count("ひよどりの依頼"), 2, shown)  # 見出しと本文に 1 回ずつ

    def test_rebuild_clears_words_of_forgotten_threads(self):
        self.write(T1, paginated(T1, [("qzxjvwkp の話", "答え", None)]))
        self.search("qzxjvwkp")
        self.run_cli("forget", T1)
        self.run_cli("rebuild")
        self.search("別の言葉")
        con = __import__("sqlite3").connect(self.db)
        for table in ("msg_fts_data", "words_fts_data"):
            try:
                blobs = b"".join(r[0] or b"" for r in con.execute("SELECT block FROM %s" % table))
            except Exception:
                continue
            self.assertNotIn(b"qzxjvwkp", blobs, table)
            self.assertNotIn(b"xjv", blobs, table)
        con.close()

    def test_no_embedding_notice_without_the_environment(self):
        self.basic_corpus()
        out = self.search("再実行")
        self.assertFalse(any("埋め込み" in c for c in out["coverage"]), out["coverage"])

    def test_broken_venv_falls_back(self):
        venv = ss.venv_python().replace(ss.data_dir(), os.path.join(self.tmp.name, "data"))
        os.makedirs(os.path.dirname(venv))
        with open(venv, "w") as f:
            f.write("#!/bin/sh\nexit 103\n")
        os.chmod(venv, 0o755)
        self.basic_corpus()
        env = dict(self.env)
        env.pop("HIRATA_CODEX_SEARCH_REEXEC")
        r = subprocess.run([sys.executable, SCRIPT, "search", "--json", "--budget", "0", "再実行"],
                           capture_output=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["results"])

    def test_one_thread_takes_at_most_three_slots(self):
        self.write(T1, paginated(T1, [("かささぎ %d" % n, "かささぎの答え %d" % n, None) for n in range(5)]))
        self.write(T2, paginated(T2, [("かささぎ 別", "答え", None)]))
        refs = [r["ref"].split("/")[0] for r in self.search("--limit", "10", "かささぎ")["results"]]
        self.assertEqual(refs.count(T1), ss.MAX_HITS_PER_THREAD)
        self.assertIn(T2, refs)

    def test_since_until_use_local_dates(self):
        self.write(T1, paginated(T1, [("むくどり", "答え", None)], ts=ss._date_arg("2026-09-10").replace("Z", ".5Z")))
        self.assertTrue(self.search("--since", "2026-09-10", "--until", "2026-09-10", "むくどり")["results"])
        self.assertFalse(self.search("--until", "2026-09-09", "むくどり")["results"])
        self.assertFalse(self.search("--since", "2026-09-11", "むくどり")["results"])

    def test_forget_message_does_not_promise_codex_delete_removes_it(self):
        self.basic_corpus()
        code, out = self.run_cli("forget", T1)
        self.assertNotIn("消すなら codex delete", out)

    def test_revert_keeps_the_prefix_file_searchable(self):
        # thread/revert は、thread ID を変えずに別の rollout ID のファイルを作り、前半は元のファイルに残す
        R = "44444444-4444-4444-8444-444444444444"
        self.write(T1, paginated(T1, [("ロメオの依頼", "答え1", None), ("シエラの依頼", "答え2", None)]))
        self.search("ロメオ")
        reverted = paginated(T1, [("タンゴの依頼", "答え3", None)]).replace('"history_mode": "paginated"',
                                                                         '"history_mode": "paginated", "history_base": {"thread_id": "%s"}' % T1)
        self.write(R, reverted)
        for word in ("ロメオ", "タンゴ"):
            self.assertTrue(self.search(word)["results"], word)

    def test_newer_copy_wins_regardless_of_name_order(self):
        self.write(T1, paginated(T1, [("パパの依頼", "答え", None), ("ケベックの依頼", "答え", None)]))
        self.write(T1, paginated(T1, [("パパの依頼", "答え", None)]), sub=("archived_sessions",))
        self.assertTrue(self.search("ケベック")["results"])
        refs = [r["ref"] for r in self.search("--all-kinds", "パパの依頼")["results"]]
        self.assertEqual(len(refs), len(set(refs)), refs)

    def test_removing_one_copy_keeps_the_other(self):
        text = paginated(T1, [("ロビンの依頼", "答え", None)])
        a = self.write(T1, text)
        self.write(T1, text, sub=("archived_sessions",))
        self.search("ロビン")
        os.remove(a)
        out = self.search("ロビン")
        self.assertEqual(len(out["results"]), 1)
        self.assertFalse(any("原本が見つからない" in c for c in out["coverage"]), out["coverage"])

    def test_redaction_keeps_quotes_and_handles_escapes(self):
        self.assertEqual(ss.redact('{"password": "hunter22"}'), '{"password": "[REDACTED]"}')
        self.assertEqual(ss.redact("$pwd = 'hunter22'"), "$pwd = '[REDACTED]'")
        self.assertNotIn("tail", ss.redact('"password": "a\\"b-tail-secret"'))
        self.assertNotIn("Hunter2", ss.redact("ConvertTo-SecureString 'Hunter2!pw' -AsPlainText -Force"))
        self.assertNotIn("ghp_abcdefgh", ss.redact("Authorization: token ghp_abcdefghijklmnop"))

    def test_memory_show_has_no_resume_command(self):
        os.makedirs(os.path.join(self.home, "memories"))
        with open(os.path.join(self.home, "memories", "n.md"), "w", encoding="utf-8") as f:
            f.write("# 要点\n\nうぐいす\n")
        code, out = self.run_cli("show", self.search("うぐいす")["results"][0]["ref"])
        self.assertNotIn("codex resume", out)

    def test_broken_venv_is_reported(self):
        venv = os.path.join(self.tmp.name, "data", os.path.relpath(ss.venv_python(), ss.data_dir()))
        os.makedirs(os.path.dirname(venv))
        with open(venv, "w") as f:
            f.write("#!/bin/sh\nexit 103\n")
        os.chmod(venv, 0o755)
        self.basic_corpus()
        env = dict(self.env)
        env.pop("HIRATA_CODEX_SEARCH_REEXEC")
        r = subprocess.run([sys.executable, SCRIPT, "search", "--json", "--budget", "0", "再実行"],
                           capture_output=True, encoding="utf-8", env=env)
        self.assertTrue(any("専用の環境" in c and "setup" in c for c in json.loads(r.stdout)["coverage"]), r.stdout)

    def test_embedder_needs_complete_model_folder(self):
        os.makedirs(os.path.join(self.tmp.name, "data", "models", ss.EMBED_MODEL_DIR))
        os.environ["HIRATA_CODEX_DATA"] = os.path.join(self.tmp.name, "data")
        try:
            sem = ss.Semantic()
            self.assertIsNone(sem.embedder())
            self.assertIn("モデルが無い", sem._emb_error)
        finally:
            del os.environ["HIRATA_CODEX_DATA"]

    def test_default_limit_is_five(self):
        for n in range(8):
            tid = "5555555%d-5555-4555-8555-555555555555" % n
            self.write(tid, paginated(tid, [("せきれい %d" % n, "答え", None)]))
        self.assertEqual(len(self.search("せきれい")["results"]), 5)

    def test_setup_prefers_314_and_uses_only_the_given_python(self):
        order = ss.setup_candidates(None)
        first_other = next(i for i, c in enumerate(order) if "3.14" not in " ".join(c))
        self.assertTrue(all("3.14" in " ".join(c) for c in order[:first_other]))
        self.assertGreater(first_other, 0)
        self.assertEqual(ss.setup_candidates("py -3.13"), [["py", "-3.13"]])

    def test_legacy_same_text_finals_in_two_turns(self):
        rows = [line("2026-09-01T01:00:00Z", "session_meta", {"id": T1})]
        for n in (1, 2):
            rows += [line("2026-09-01T01:00:00Z", "event_msg", {"type": "user_message", "message": "依頼%d" % n}),
                     line("2026-09-01T01:00:00Z", "event_msg", {"type": "task_complete", "last_agent_message": "同じ答え"})]
        _, msgs, _ = ss.parse_rollout("x", ("\n".join(rows) + "\n").encode())
        self.assertEqual([m["kind"] for m in sorted(msgs, key=lambda m: m["line"])], ["user", "final", "user", "final"])

    def test_old_index_is_migrated_and_reextracted(self):
        self.write(T1, paginated(T1, [("移行の依頼 かいつぶり", "答え", None)], agent="worker"))
        self.search("かいつぶり")
        import sqlite3
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE threads")
        con.execute("CREATE TABLE threads (thread_id TEXT PRIMARY KEY, codex_home TEXT, name TEXT, cwd TEXT, branch TEXT, "
                    "originator TEXT, agent TEXT, started TEXT)")
        con.execute("INSERT INTO threads VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, NULL)", (T1, self.home))
        con.execute("UPDATE meta SET value = '1'")
        con.commit()
        con.close()
        self.assertFalse(self.search("かいつぶり")["results"])  # 取り込み直して、子の会話の印が戻る


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
