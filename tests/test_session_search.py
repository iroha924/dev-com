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
        first_other = next((i for i, c in enumerate(order) if "3.14" not in " ".join(c)), len(order))
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

    def test_compressing_with_an_old_python_keeps_the_content(self):
        path = self.write(T1, paginated(T1, [("ユニフォームの依頼", "答え", None)]))
        self.assertTrue(self.search("ユニフォーム")["results"])
        os.remove(path)
        with open(path + ".zst", "wb") as f:
            f.write(b"compressed")
        idx = self.idx()
        saved = ss.zstd
        try:
            ss.zstd = None
            idx.ingest(self.home)
        finally:
            ss.zstd = saved
        n = idx.db.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (T1,)).fetchone()[0]
        idx.close()
        self.assertEqual(n, 2)

    def test_interrupted_migration_recovers(self):
        self.write(T1, paginated(T1, [("移行 いわつばめ", "答え", None)]))
        self.search("いわつばめ")
        import sqlite3
        con = sqlite3.connect(self.db)
        con.execute("ALTER TABLE threads RENAME TO threads_v1")  # 名前を変えて新しい表を作った後、消す前に止まった
        con.execute("CREATE TABLE threads (thread_id TEXT, codex_home TEXT, name TEXT, cwd TEXT, branch TEXT, "
                    "originator TEXT, agent TEXT, started TEXT, PRIMARY KEY (codex_home, thread_id))")
        con.execute("UPDATE meta SET value = '1' WHERE key = 'schema'")
        con.commit()
        con.close()
        out = self.search("いわつばめ")
        self.assertTrue(out["results"])
        self.assertFalse(any("更新できなかった" in c for c in out["coverage"]), out["coverage"])

    def test_old_duplicate_rows_are_read_again(self):
        self.write(T1, paginated(T1, [("みさごの依頼", "答え", None)]))
        self.search("みさご")
        idx = self.idx()  # 1b66129 が 2 本目に行っていたこと: 中身を消して duplicate にする
        with idx.db:
            for (p,) in idx.db.execute("SELECT path FROM files").fetchall():
                idx._delete_path(p)
            idx.db.execute("UPDATE files SET status = 'duplicate'")
            idx.db.execute("UPDATE meta SET value = '1' WHERE key = 'extract'")
        idx.close()
        self.assertTrue(self.search("みさご")["results"])

    def test_powershell_quoting_is_redacted_well(self):
        self.assertNotIn("secret", ss.redact("password: 'it''s secret'"))
        self.assertNotIn("Hunter2", ss.redact("ConvertTo-SecureString -AsPlainText -Force -String 'Hunter2'"))
        self.assertIn("$x = 'second'", ss.redact("$pwd = 'C:\\Users\\me\\'; $x = 'second'"))

    def test_same_text_in_two_turns_is_kept(self):
        rows = [line("2026-09-01T01:00:00Z", "session_meta", {"id": T1})]
        for n, ts in ((1, "2026-09-01T01:00:00Z"), (2, "2026-09-02T01:00:00Z")):
            rows += [line(ts, "event_msg", {"type": "user_message", "message": "同じ依頼 しめ"}),
                     line(ts, "event_msg", {"type": "task_complete", "last_agent_message": "答え%d" % n})]
        self.write(T1, "\n".join(rows) + "\n")
        self.write(T1, "\n".join(rows) + "\n", sub=("archived_sessions", "x"))
        self.assertEqual(len(self.search("しめ")["results"]), 2)

    def test_thread_metadata_is_per_home(self):
        other = os.path.join(self.tmp.name, "codex2")
        os.makedirs(os.path.join(other, "sessions"))
        self.write(T1, paginated(T1, [("ほおじろ", "答え", None)]))
        with open(os.path.join(self.home, "session_index.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": T1, "thread_name": "Aの名前"}, ensure_ascii=False) + "\n")
        self.search("ほおじろ")
        p2 = os.path.join(other, "sessions", "rollout-2026-09-01T01-00-00-%s.jsonl" % T1)
        with open(p2, "w", encoding="utf-8") as f:
            f.write(paginated(T1, [("ほおじろ", "答え", None)], agent="worker"))
        with open(os.path.join(other, "session_index.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": T1, "thread_name": "Bの名前"}, ensure_ascii=False) + "\n")
        env = dict(self.env, CODEX_HOME=other)
        subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "ほおじろ"], capture_output=True, env=env)
        out = self.search("ほおじろ")
        self.assertTrue(out["results"])  # B の子の印が A に付かない
        self.assertIn("Aの名前", json.dumps(out, ensure_ascii=False))

    def test_model_ready_needs_tokenizer_and_pooling(self):
        d = os.path.join(self.tmp.name, "data", "models", ss.EMBED_MODEL_DIR)
        os.makedirs(d)
        for f in ("config.json", "modules.json", "model.safetensors"):
            open(os.path.join(d, f), "w").close()
        os.environ["HIRATA_CODEX_DATA"] = os.path.join(self.tmp.name, "data")
        try:
            self.assertFalse(ss.model_ready())
        finally:
            del os.environ["HIRATA_CODEX_DATA"]

    def test_model_revision_is_pinned(self):
        self.assertRegex(ss.EMBED_REVISION, r"^[0-9a-f]{40}$")

    def test_single_quote_backslash_is_not_an_escape(self):
        self.assertNotIn("privateSuffix", ss.redact("$password = 'abc privateSuffix\\'"))

    def test_backtick_escapes_in_powershell(self):
        self.assertNotIn("tail42", ss.redact('ConvertTo-SecureString "a`"tail42" -AsPlainText -Force'))
        self.assertNotIn("sSecret42", ss.redact("ConvertTo-SecureString 'John''sSecret42' -AsPlainText"))

    def test_stored_text_is_redacted_again_on_upgrade(self):
        path = self.write(T1, paginated(T1, [("認証 しぎ", "答え", None)]))
        self.search("しぎ")
        os.remove(path)
        self.search("しぎ")  # 原本なし（missing）として残る
        leaked = '{"Authorization": "Basic dXNlcjpwYXNzd29yZA=="} しぎ'
        idx = self.idx()
        with idx.db:
            idx.db.execute("UPDATE messages SET text = ?, norm = ? WHERE kind = 'user'", (leaked, ss.normalize(leaked)))
            idx.db.execute("UPDATE meta SET value = '1' WHERE key = 'extract'")
        idx.close()
        self.assertNotIn("dXNlcjpwYXNzd29yZA", json.dumps(self.search("しぎ"), ensure_ascii=False))

    def test_legacy_ids_differ_between_rollout_files(self):
        def legacy(text, ts):
            return "\n".join([line(ts, "session_meta", {"id": T1}),
                              line(ts, "event_msg", {"type": "user_message", "message": text}),
                              line(ts, "event_msg", {"type": "task_complete", "last_agent_message": text + " の答え"})]) + "\n"
        self.write(T1, legacy("同じ依頼 ちどり", "2026-09-01T01:00:00Z"))
        self.write("66666666-6666-4666-8666-666666666666", legacy("同じ依頼 ちどり", "2026-09-05T01:00:00Z"))
        self.assertEqual([r["kind"] for r in self.search("ちどり")["results"]].count("user"), 2)

    def test_migration_keeps_metadata_for_every_home(self):
        other = os.path.join(self.tmp.name, "codex2")
        os.makedirs(os.path.join(other, "sessions"))
        self.write(T1, paginated(T1, [("ごいさぎ", "答え", None)], agent="worker"))
        p2 = os.path.join(other, "sessions", "rollout-2026-09-01T01-00-00-%s.jsonl" % T1)
        with open(p2, "w", encoding="utf-8") as f:
            f.write(paginated(T1, [("ごいさぎ", "答え", None)], agent="worker"))
        env = dict(self.env, CODEX_HOME=other)
        self.search("ごいさぎ")
        subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "ごいさぎ"], capture_output=True, env=env)
        os.remove(p2)
        import sqlite3  # schema 1 の threads（thread_id だけの主キー、A の行だけ）に戻す
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE threads")
        con.execute("CREATE TABLE threads (thread_id TEXT PRIMARY KEY, codex_home TEXT, name TEXT, cwd TEXT, branch TEXT, "
                    "originator TEXT, agent TEXT, started TEXT)")
        con.execute("INSERT INTO threads VALUES (?, ?, NULL, NULL, NULL, NULL, 'worker', NULL)", (T1, self.home))
        con.execute("UPDATE meta SET value = '1'")
        con.commit()
        con.close()
        r = subprocess.run([sys.executable, SCRIPT, "search", "--json", "--budget", "0", "ごいさぎ"],
                           capture_output=True, encoding="utf-8", env=env)
        self.assertFalse(json.loads(r.stdout)["results"])  # B に残った本文も、子の会話のまま

    def test_restored_file_is_no_longer_missing(self):
        path = self.write(T1, paginated(T1, [("おおるり", "答え", None)]))
        self.search("おおるり")
        backup = path + ".bak"
        shutil.move(path, backup)
        self.assertTrue(any("原本が見つからない" in c for c in self.search("おおるり")["coverage"]))
        shutil.move(backup, path)
        self.assertFalse(any("原本が見つからない" in c for c in self.search("おおるり")["coverage"]))

    def test_deleted_memory_leaves_the_index(self):
        os.makedirs(os.path.join(self.home, "memories"))
        mem = os.path.join(self.home, "memories", "old.md")
        with open(mem, "w", encoding="utf-8") as f:
            f.write("# 古い要点\n\nこまどり\n")
        self.assertTrue(self.search("こまどり")["results"])
        os.remove(mem)
        out = self.search("こまどり")
        self.assertFalse(out["results"])
        self.assertFalse(any("原本が見つからない" in c for c in out["coverage"]), out["coverage"])

    def test_memory_removed_during_ingest_does_not_crash(self):
        os.makedirs(os.path.join(self.home, "memories"))
        idx = self.idx()
        saved = ss.memory_files
        try:
            ss.memory_files = lambda home, failed=None: [os.path.join(home, "memories", "gone.md")]
            idx.ingest(self.home)
        finally:
            ss.memory_files = saved
            idx.close()


class FourthReviewTest(_Base):
    """4 回目のレビュー（全体）で見つかった欠陥の再現。"""

    def idx(self):
        return ss.Index(self.db)

    # 伏せ字

    def test_more_secret_shapes_are_redacted(self):
        cases = [
            ('$password = "' + "word " * 42 + 'tailsecret42"', "tailsecret42"),
            ('$password = "first\nsecondline42"', "secondline42"),
            ('"syntheticPassword42" | ConvertTo-SecureString -AsPlainText -Force', "syntheticPassword42"),
            ("ConvertTo-SecureString -String syntheticPassword42 -AsPlainText -Force", "syntheticPassword42"),
            ("ConvertTo-SecureString plainPositional42 -AsPlainText", "plainPositional42"),
            ("git clone https://syntheticPat42@dev.azure.com/organization", "syntheticPat42"),
            ("https://user:p@ss@host/x", "ss@host"),
            ("Server=db;User Id=sa;Password=my secret pass;Encrypt=true", "secret pass"),
            ("password=abc", "abc"),
            ("Connect-Thing -Password 'paramSecret42'", "paramSecret42"),
            ("パスワードは Kanji2Secret42 です", "Kanji2Secret42"),
            ("DefaultEndpointsProtocol=https;AccountName=a;AccountKey=abc+def/ghi==;EndpointSuffix=x", "abc+def"),
            ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAtruncated", "MIIEowIBAAKCAQEA"),
            ("-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----", "MIIEvQ"),
            ("Bearer abcdefghijklmnopqrstuvwx", "abcdefghijklmnop"),
            ("PAT " + "A" * 40 + "AZDO" + "b" * 40, "AZDO"),
        ]
        for text, secret in cases:
            self.assertNotIn(secret, ss.redact(text), text)

    # 取り込み

    def test_long_japanese_message_does_not_break_word_index(self):
        try:
            import sudachipy  # noqa: F401
        except ImportError:
            self.skipTest("SudachiPy が無い")
        idx = self.idx()
        long = "パイプラインのログ。" * 2700 + " 末尾にある TF401019 の話"
        self.write(T1, paginated(T1, [(long, "答え", None)]))
        idx.ingest(self.home)
        sem = ss.Semantic()
        idx.fill_words(sem.tokenizer(), self.home)
        results, _, mode = idx.search(self.home, ["TF401019"], ss.DEFAULT_KINDS, sem=sem)
        idx.close()
        self.assertIn("SudachiPy", mode)
        self.assertTrue(results)

    def test_embedding_uses_the_head_and_words_use_the_whole_text(self):
        self.assertEqual(ss.embed_chunks("あ" * (ss.EMBED_CHARS * 2 + 10)), ["あ" * ss.EMBED_CHARS])
        self.assertEqual(len(ss._byte_chunks("あ" * 50000, ss.SUDACHI_MAX_BYTES)), 4)  # 単語の区切りは全文

    def test_copied_fork_uses_its_own_session_meta(self):
        own = line("2026-09-01T01:00:00Z", "session_meta", {"id": T2, "agent_role": "worker", "forked_from_id": T1})
        parent = line("2026-09-01T01:00:00Z", "session_meta", {"id": T1})
        rest = paginated(T2, [("フォークの依頼 くいな", "答え", None)]).split("\n", 1)[1]
        meta, _, _ = ss.parse_rollout("rollout-x-%s.jsonl" % T2, (own + "\n" + parent + "\n" + rest).encode())
        self.assertEqual((meta["thread_id"], meta["agent"]), (T2, "worker"))

    def test_complete_last_line_without_newline_is_read(self):
        text = paginated(T1, [("最後の行 いかる", "答え", None)]).rstrip("\n")
        _, msgs, counters = ss.parse_rollout("x", text.encode())
        self.assertEqual(len(msgs), 2)
        self.assertEqual(counters["incomplete_tail"], 0)

    def test_append_during_read_still_indexes_what_was_read(self):
        path = self.write(T1, paginated(T1, [("読んでいる間 ひたき", "答え", None)]))
        idx = self.idx()
        real_stat, calls = os.stat, []

        def fake_stat(p, *a, **k):
            st = real_stat(p, *a, **k)
            if p == path:
                calls.append(1)
                if len(calls) == 2:  # 読んだ後の確認で、書き足されたように見せる
                    return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
                                           st.st_size + 10, st.st_atime, st.st_mtime, st.st_ctime))
            return st
        ss.os.stat = fake_stat
        try:
            idx.ingest(self.home)
        finally:
            ss.os.stat = real_stat
        n = idx.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        idx.close()
        self.assertEqual(n, 2)

    def test_file_vanishing_mid_ingest_does_not_crash(self):
        path = self.write(T1, paginated(T1, [("消える つぐみ", "答え", None)]))
        idx = self.idx()
        real_stat, calls = os.stat, []

        def fake_stat(p, *a, **k):
            if p == path:
                calls.append(1)
                if len(calls) == 2:
                    raise FileNotFoundError(p)
            return real_stat(p, *a, **k)
        ss.os.stat = fake_stat
        try:
            idx.ingest(self.home)
        finally:
            ss.os.stat = real_stat
            idx.close()

    def test_unreadable_memory_is_kept_and_reported(self):
        os.makedirs(os.path.join(self.home, "memories"))
        mem = os.path.join(self.home, "memories", "m.md")
        with open(mem, "w", encoding="utf-8") as f:
            f.write("# 要点\n\nあかげら\n")
        self.search("あかげら")
        idx = self.idx()
        real_stat = os.stat

        def fake_stat(p, *a, **k):
            if p == mem:
                raise PermissionError(p)
            return real_stat(p, *a, **k)
        ss.os.stat = fake_stat
        try:
            report = idx.ingest(self.home)
        finally:
            ss.os.stat = real_stat
        n = idx.db.execute("SELECT COUNT(*) FROM messages WHERE kind = 'memory'").fetchone()[0]
        idx.close()
        self.assertEqual(n, 1)
        self.assertTrue(report["errors"])

    def test_missing_count_clears_after_compressed_copy_is_read(self):
        path = self.write(T1, paginated(T1, [("圧縮して移す るり", "答え", None)]))
        self.search("るり")
        idx = self.idx()
        saved = ss.zstd
        inplace = path + ".zst"  # その場で圧縮（読めない Python で取り込む）してから、archive へ移す
        with open(inplace, "wb") as f:
            f.write(b"z")
        os.remove(path)
        dest = os.path.join(self.home, "archived_sessions", os.path.basename(inplace))
        try:
            ss.zstd = None
            idx.ingest(self.home)
            os.makedirs(os.path.dirname(dest))
            shutil.move(inplace, dest)
            idx.ingest(self.home)

            class Fake:
                @staticmethod
                def decompress(_):
                    return paginated(T1, [("圧縮して移す るり", "答え", None)]).encode()
            ss.zstd = Fake
            idx.ingest(self.home)
        finally:
            ss.zstd = saved
        cov = idx.coverage(self.home)
        idx.close()
        self.assertEqual(cov["missing"], 0)

    def test_extract_version_covers_the_latest_changes(self):
        self.assertGreaterEqual(int(ss.EXTRACT_VERSION), 3)

    # 検索と表示

    def test_display_name_is_per_home(self):
        other = os.path.join(self.tmp.name, "codex2")
        os.makedirs(os.path.join(other, "sessions"))
        p2 = os.path.join(other, "sessions", "rollout-2026-08-01T01-00-00-%s.jsonl" % T1)
        with open(p2, "w", encoding="utf-8") as f:
            f.write(paginated(T1, [("Bだけの依頼 けり", "答え", None)], ts="2026-08-01T01:00:00Z"))
        env = dict(self.env, CODEX_HOME=other)
        subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "けり"], capture_output=True, env=env)
        self.write(T1, paginated(T1, [("Aの依頼 けり", "答え", None)]))
        out = json.dumps(self.search("けり"), ensure_ascii=False)
        self.assertNotIn("Bだけの依頼", out)

    def test_search_never_mixes_homes(self):
        other = os.path.join(self.tmp.name, "codex2")
        os.makedirs(os.path.join(other, "sessions"))
        with open(os.path.join(other, "sessions", "rollout-2026-08-01T01-00-00-%s.jsonl" % T2), "w", encoding="utf-8") as f:
            f.write(paginated(T2, [("ほかのホーム やませみ", "答え", None)]))
        env = dict(self.env, CODEX_HOME=other)
        subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "やませみ"], capture_output=True, env=env)
        self.write(T1, paginated(T1, [("関係ない", "答え", None)]))
        self.assertFalse(self.search("やませみ")["results"])

    def test_forget_then_append_or_move_stays_hidden(self):
        path = self.write(T1, paginated(T1, [("わすれる しまえなが", "答え", None)]))
        self.search("しまえなが")
        self.run_cli("forget", T1)
        with open(path, "a", encoding="utf-8") as f:
            f.write(paginated(T1, [("追記 しまえなが", "答え", None)]).split("\n", 1)[1])
        self.assertFalse(self.search("しまえなが")["results"])
        dest = os.path.join(self.home, "archived_sessions", os.path.basename(path))
        os.makedirs(os.path.dirname(dest))
        shutil.move(path, dest)
        self.assertFalse(self.search("しまえなが")["results"])
        code, out = self.run_cli("show", "%s/u1" % T1)
        self.assertNotEqual(code, 0)

    def test_forgotten_list_is_per_home(self):
        idx = self.idx()
        idx.forget("/other/home", T1)
        self.assertNotIn(T1, idx.forgotten(self.home))
        idx.close()

    def test_quoted_phrase_from_the_shell_is_a_phrase(self):
        self.write(T1, paginated(T1, [("idle timeout の件", "答え", None)]))
        self.write(T2, paginated(T2, [("idle になって unrelated で timeout", "答え", None)]))
        refs = [r["ref"].split("/")[0] for r in self.search("idle timeout")["results"]]
        self.assertEqual(refs[0], T1)  # 語句をそのまま含むものが上。並べた言葉だけのものも、絞り落とさない
        self.assertIn(T2, refs)

    def test_more_terms_rank_higher(self):
        self.write(T1, paginated(T1, [("再実行 だけ", "答え", None)]))
        self.write(T2, paginated(T2, [("再実行 と ロードバランサー の両方", "答え", None)]))
        self.write(T3, paginated(T3, [("ロードバランサー だけ", "答え", None)]))
        first = self.search("再実行", "ロードバランサー")["results"][0]["ref"].split("/")[0]
        self.assertEqual(first, T2)

    def test_show_prints_the_notices(self):
        self.write(T1, paginated(T1, [("注意の行 かわせみ", "答え", None)]))
        self.write(T2, "not zstd", suffix=".jsonl.zst")
        code, out = self.run_cli("show", "%s/u1" % T1)
        if ss.zstd is None:
            self.assertIn("圧縮された記録", out)
        else:
            self.assertIn("読めなかったファイル", out)

    def test_bad_date_is_a_usage_error(self):
        code, out = self.run_cli("search", "--since", "2026/09/01", "x")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", out)

    def test_stats_shows_the_method(self):
        code, out = self.run_cli("stats")
        self.assertIn("方式:", out)

    def test_japanese_phrasing_keeps_ordinary_words(self):
        for text in ("シークレットは使わず、公開鍵だけ", "個人環境固有のパスは書かない", "トークンは期限切れ"):
            self.assertEqual(ss.redact(text), text)
        self.assertNotIn("Kanji2Secret42", ss.redact("パスワードは Kanji2Secret42 です"))
        self.assertNotIn("abc123def", ss.redact("トークン：abc123def"))

    def test_triple_quoted_secret_is_redacted(self):
        self.assertNotIn("triple42", ss.redact('password = """triple42"""'))
        self.assertNotIn("triple42", ss.redact("token = \'\'\'triple42\'\'\'"))

    @unittest.skipIf(os.name == "nt", "偽の python を sh で作る")
    def test_venv_rebuild_reason(self):
        def fake(name, version, zstd):
            path = os.path.join(self.tmp.name, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\ncase \"$2\" in\n  *compression.zstd*) exit %d;;\n  *version_info*) echo \"%s\";;\nesac\n"
                        % (0 if zstd else 1, version))
            os.chmod(path, 0o755)
            return path
        base = [fake("base", "(3, 14)", True)]
        self.assertEqual(ss.venv_rebuild_reason(base, os.path.join(self.tmp.name, "none")), "まだ無い")
        self.assertIsNone(ss.venv_rebuild_reason(base, fake("same", "(3, 14)", True)))
        self.assertIn("違う", ss.venv_rebuild_reason(base, fake("old", "(3, 11)", False)))
        self.assertIn("圧縮", ss.venv_rebuild_reason(base, fake("nozstd", "(3, 14)", False)))
        broken = os.path.join(self.tmp.name, "broken")
        with open(broken, "w") as f:
            f.write("#!/bin/sh\nexit 103\n")
        os.chmod(broken, 0o755)
        self.assertIn("起動できない", ss.venv_rebuild_reason(base, broken))

    def test_archive_move_keeps_one_copy_after_one_ingest(self):
        path = self.write(T1, paginated(T1, [("いっかい ひばり", "答え", None)]))
        self.search("ひばり")
        dest = os.path.join(self.home, "archived_sessions", os.path.basename(path))
        os.makedirs(os.path.dirname(dest))
        shutil.move(path, dest)
        idx = self.idx()
        idx.ingest(self.home)
        paths = [r[0] for r in idx.db.execute("SELECT DISTINCT path FROM messages")]
        files = [r[0] for r in idx.db.execute("SELECT path FROM files")]
        idx.close()
        self.assertEqual((paths, files), ([dest], [dest]))

    def test_rerank_reorders_the_head_and_can_be_turned_off(self):
        for tid, text in ((T1, "あおさぎ 一つ目"), (T2, "あおさぎ 二つ目"), (T3, "あおさぎ 三つ目")):
            self.write(tid, paginated(tid, [(text, "答え", None)]))
        idx = self.idx()
        idx.ingest(self.home)

        class FakeSem:
            def tokenizer(self):
                return None

            def embedder(self):
                return None

            def reranker(self):
                return lambda pairs: [1.0 if "三つ目" in b else 0.0 for _, b in pairs]
        res, _, mode = idx.search(self.home, ["あおさぎ"], ss.DEFAULT_KINDS, question="三つ目は？", sem=FakeSem())
        self.assertEqual(res[0][1]["thread_id"], T3)
        self.assertIn("並べ直し", mode)
        res, _, mode = idx.search(self.home, ["あおさぎ"], ss.DEFAULT_KINDS, sem=FakeSem(), rerank=False)
        self.assertNotIn("並べ直し", mode)
        idx.close()
        self.assertEqual(self.search("--no-rerank", "あおさぎ")["results"][0]["kind"], "user")

    def test_rerank_window_finds_the_terms(self):
        text = "あ" * 3000 + "目的の言葉" + "い" * 3000
        self.assertIn("目的の言葉", ss.rerank_window(text, ["目的の言葉"]))
        self.assertLessEqual(len(ss.rerank_window(text, ["目的の言葉"])), ss.RERANK_WINDOW)

    def test_phrase_list_lifts_the_exact_phrase_in_word_mode(self):
        # 単語の BM25 だけなら、言葉を逆の順で含む短い T1 が上に来る。語句の並びが、そのまま含む長い T2 を上げる
        self.write(T1, paginated(T1, [("timeout idle", "答え", None)]))
        self.write(T2, paginated(T2, [("ログを見ると idle timeout が 60 秒で切れていて その前後に 別の 処理 も 動いて いた", "答え", None)]))
        idx = self.idx()
        idx.ingest(self.home)

        class FakeSem:
            def tokenizer(self):
                return lambda text: ss.normalize(text).split()

            def embedder(self):
                return None

            def reranker(self):
                return None
        idx.fill_words(FakeSem().tokenizer(), self.home)
        res, _, _ = idx.search(self.home, ss.parse_query(["idle timeout"]), ss.DEFAULT_KINDS, sem=FakeSem())
        idx.close()
        self.assertEqual(res[0][1]["thread_id"], T2)

    def test_model_that_fails_to_load_is_reported(self):
        class Broken:
            _emb_error = "OSError: broken"
            _rerank_error = None
        saved = ss.model_ready
        ss.model_ready = lambda kind="embed": kind == "embed"
        try:
            idx = self.idx()
            lines = ss.coverage_lines(idx, self.home, None, None, Broken())
            idx.close()
        finally:
            ss.model_ready = saved
        self.assertTrue(any("埋め込みのモデルを読み込めなかった" in x for x in lines), lines)

    def test_compressed_copy_is_not_counted_as_missing(self):
        path = self.write(T1, paginated(T1, [("あとり", "答え", None)]))
        self.search("あとり")
        os.remove(path)
        with open(path + ".zst", "wb") as f:
            f.write(b"z")
        saved = ss.zstd
        idx = self.idx()
        try:
            ss.zstd = None
            idx.ingest(self.home)
        finally:
            ss.zstd = saved
        self.assertEqual(idx.coverage(self.home)["missing"], 0)
        idx.close()

    def test_legacy_refs_differ_between_rollout_files(self):
        def legacy(ts):
            return "\n".join([line(ts, "session_meta", {"id": T1}),
                              line(ts, "event_msg", {"type": "user_message", "message": "同じ依頼 ちどり"})]) + "\n"
        self.write(T1, legacy("2026-09-01T01:00:00Z"))
        self.write("66666666-6666-4666-8666-666666666666", legacy("2026-09-05T01:00:00Z"))
        refs = [r["ref"] for r in self.search("ちどり")["results"]]
        self.assertEqual(len(set(refs)), 2, refs)


class FifthReviewTest(_Base):
    """5 回目のレビュー（全体）で見つかった欠陥の再現。"""

    def idx(self):
        return ss.Index(self.db)

    def test_more_secret_shapes(self):
        cases = [
            ("postgresql://admin:Sup3rSECRET@db.example/app", "Sup3rSECRET"),
            ("mongodb+srv://user:pw1234@cluster0/x", "pw1234"),
            ("redis://:redisPw99@cache:6379", "redisPw99"),
            ("Endpoint=sb://x/;SharedAccessKeyName=k;SharedAccessSignature=sv=1&sig=SECRETsig123", "SECRETsig123"),
            ("https://acct.blob.core.windows.net/c?sv=2024&sig=SECRETsig456&se=x", "SECRETsig456"),
            ("ConvertTo-SecureString -AsPlainText -Force psSECRET5", "psSECRET5"),
            ("Invoke-Sqlcmd -Password paramSecret42", "paramSecret42"),
            ("tool --password dashSecret42", "dashSecret42"),
            ("az devops login --token azTok42xyz", "azTok42xyz"),
            ("tool --pat patValue42", "patValue42"),
            ("AWS_SECRET_ACCESS_KEY=awsSecret42", "awsSecret42"),
            ("ｐａｓｓｗｏｒｄ＝ｈｕｎｔｅｒ２２", "ｈｕｎｔｅｒ２２"),
            ('$password = @"\nhereSecret42\n"@', "hereSecret42"),
            ("Authorization: Basic dTpw", "dTpw"),
            ("secret: |\n  yamlSecret42\n  more\nnext: 1", "yamlSecret42"),
            ("パスワードを Kanji3Secret42 に変えた", "Kanji3Secret42"),
        ]
        for text, secret in cases:
            out = ss.redact(text)
            self.assertNotIn(secret, out, text)
            self.assertNotIn(ss.normalize(secret), ss.normalize(out), text)

    def test_display_redacts_old_rows_too(self):
        self.write(T1, paginated(T1, [("ひたき ReadonlySecret42", "答え", None)]))
        self.search("ひたき")
        idx = self.idx()
        leaked = "パスワードは ReadonlySecret42 です ひたき"
        with idx.db:
            idx.db.execute("UPDATE messages SET text = ?, norm = ? WHERE kind = 'user'", (leaked, ss.normalize(leaked)))
            idx.db.execute("UPDATE threads SET name = ?, cwd = ?", ("パスワードは UpgradeSecret42 です", "パスワードは UpgradeSecret42 です"))
        idx.close()
        out = json.dumps(self.search("ひたき"), ensure_ascii=False)
        self.assertNotIn("ReadonlySecret42", out)
        self.assertNotIn("UpgradeSecret42", out)
        code, shown = self.run_cli("show", "%s/u1" % T1)
        self.assertNotIn("ReadonlySecret42", shown)
        self.assertNotIn("UpgradeSecret42", shown)

    def test_forget_accepts_ref_case_and_spaces_and_rejects_unknown(self):
        self.write(T1, paginated(T1, [("しじゅうから", "答え", None)]))
        self.search("しじゅうから")
        self.assertEqual(self.run_cli("forget", "  %s/u1 " % T1.upper())[0], 0)
        self.assertFalse(self.search("しじゅうから")["results"])
        code, out = self.run_cli("forget", "99999999-9999-4999-8999-999999999999")
        self.assertNotEqual(code, 0)
        self.assertIn("索引に無い", out)

    def test_forget_hides_copies_in_a_copied_fork(self):
        parent = paginated(T1, [("親の依頼 よしきり", "親の答え", None)])
        own = line("2026-09-02T01:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        child = own + "\n" + parent + paginated(T2, [("子の依頼 よしきり", "子の答え", None)]).split("\n", 1)[1]
        self.write(T1, parent)
        self.write(T2, child)
        self.search("よしきり")
        self.run_cli("forget", T1)
        texts = [r["snippet"] if "snippet" in r else "" for r in self.search("--all-kinds", "よしきり")["results"]]
        joined = json.dumps(self.search("--all-kinds", "よしきり"), ensure_ascii=False)
        self.assertNotIn("親の依頼", joined)
        self.assertIn("子の依頼", joined)
        code, shown = self.run_cli("show", "%s/u1" % T2)
        self.assertNotIn("親の依頼", shown)

    def test_partial_answer_counts_as_an_answer(self):
        text = paginated(T1, [("依頼 つばくろ", None, None)])
        text += line("2026-09-01T01:00:00Z", "event_msg", {"type": "item_completed", "turn_id": "%s-t1" % T1, "item": {
            "type": "AgentMessage", "id": "p1", "phase": "partial_answer", "content": [{"type": "Text", "text": "部分の答え ほおあか"}]}}) + "\n"
        _, msgs, _ = ss.parse_rollout("x", text.encode())
        self.assertIn(("final", "部分の答え ほおあか"), [(m["kind"], m["text"]) for m in msgs])

    def test_file_without_messages_is_reported(self):
        text = line("2026-09-01T01:00:00Z", "session_meta", {"id": T1}) + "\n" + line(
            "2026-09-01T01:00:00Z", "response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "x"}]}) + "\n"
        self.write(T1, text)
        out = self.search("なにか")
        self.assertTrue(any("発言を取り出せなかった" in c for c in out["coverage"]), out["coverage"])

    def test_unreadable_memories_folder_keeps_content(self):
        os.makedirs(os.path.join(self.home, "memories"))
        with open(os.path.join(self.home, "memories", "m.md"), "w", encoding="utf-8") as f:
            f.write("# 要点\n\nかけす\n")
        self.search("かけす")
        idx = self.idx()
        real_walk = os.walk

        def broken_walk(top, *a, **k):
            if top.endswith("memories"):
                if k.get("onerror"):
                    k["onerror"](PermissionError(top))
                return iter(())
            return real_walk(top, *a, **k)
        ss.os.walk = broken_walk
        try:
            report = idx.ingest(self.home)
        finally:
            ss.os.walk = real_walk
        n = idx.db.execute("SELECT COUNT(*) FROM messages WHERE kind = 'memory'").fetchone()[0]
        idx.close()
        self.assertEqual(n, 1)
        self.assertTrue(report["errors"])

    def test_words_index_gaps_fall_back_to_trigram(self):
        self.write(T1, paginated(T1, [("みどり 二つ目", "答え", None)]))
        idx = self.idx()
        idx.ingest(self.home)

        class FakeSem:
            def tokenizer(self):
                return lambda text: ["緑" if w == "みどり" else w for w in ss.normalize(text).split()]

            def embedder(self):
                return None

            def reranker(self):
                return None
        res, _, _ = idx.search(self.home, ["みどり"], ss.DEFAULT_KINDS, sem=FakeSem())  # 単語の索引はまだ空
        self.assertTrue(res)
        idx.fill_words(lambda text: (_ for _ in ()).throw(RuntimeError("boom")), self.home)
        self.assertEqual(idx.db.execute("SELECT COUNT(*) FROM word_done").fetchone()[0], 0)  # 失敗は済みにしない
        idx.fill_words(FakeSem().tokenizer(), self.home)
        res, _, _ = idx.search(self.home, ["みどり"], ss.DEFAULT_KINDS, sem=FakeSem())  # 正規化で当たらない言葉も、文字どおりで当たる
        self.assertTrue(res)
        idx.close()

    def test_show_orders_by_time_not_string(self):
        rows = [line("2026-09-01T01:00:00Z", "session_meta", {"id": T1})]
        for turn, (uts, fts) in enumerate((("2026-09-01T01:00:00.900Z", "2026-09-01T01:00:01Z"), ("2026-09-01T01:00:01.100Z", "2026-09-01T01:00:02Z")), 1):
            tid = "t%d" % turn
            rows.append(line(uts, "event_msg", {"type": "item_completed", "turn_id": tid, "item": {"type": "UserMessage", "id": "u%d" % turn, "content": [{"type": "text", "text": "依頼%d まひわ" % turn}]}}))
            rows.append(line(fts, "event_msg", {"type": "item_completed", "turn_id": tid, "item": {"type": "AgentMessage", "id": "f%d" % turn, "phase": "final_answer", "content": [{"type": "Text", "text": "答え%d" % turn}]}}))
        self.write(T1, "\n".join(rows) + "\n")
        code, out = self.run_cli("show", "%s/f1" % T1, "--context", "0")
        body = out.split("\n", 1)[1]  # 1 行目の見出し（最初の依頼が名前の代わりに出る）を除く
        self.assertIn("依頼1", body)
        self.assertNotIn("依頼2", body)

    def test_unreadable_session_index_does_not_crash(self):
        self.write(T1, paginated(T1, [("もず", "答え", None)]))
        os.makedirs(os.path.join(self.home, "session_index.jsonl"))  # 開けない（フォルダ）
        out = self.search("もず")
        self.assertTrue(out["results"])

    def test_relative_db_path(self):
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            ss.Index("rel.sqlite").close()
        finally:
            os.chdir(cwd)

    def test_agent_eval_wrapper_quotes_paths(self):
        spec2 = importlib.util.spec_from_file_location("agent_eval", os.path.join(ROOT, "evals", "session-search", "agent_eval.py"))
        ae = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(ae)
        d = os.path.join(self.tmp.name, "with space")
        os.makedirs(d)
        w = ae.wrapper_script(d, os.path.join(d, "snap shot"), os.path.join(d, "db x.sqlite"), os.path.join(d, "da ta"))
        body = open(w, encoding="utf-8").read()
        self.assertIn("'%s'" % os.path.join(d, "snap shot"), body)

    def test_no_rerank_flag_reaches_the_search(self):
        seen = {}

        class FakeSem:
            def tokenizer(self):
                return None

            def embedder(self):
                return None

            def reranker(self):
                def score(pairs):
                    seen["query"] = pairs[0][0]
                    return [0.0] * len(pairs)
                return score
        for tid in (T1, T2):
            self.write(tid, paginated(tid, [("こがら %s" % tid[:1], "答え", None)]))
        import contextlib
        import io
        saved_sem, saved_env = ss.Semantic, dict(os.environ)
        os.environ.update(self.env)
        ss.Semantic = FakeSem
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ss.main(["search", "--budget", "0", "--question", "こがらの話", "こがら"])
            self.assertEqual(seen.get("query"), "こがらの話")  # 並べ直しに問いの文が渡る
            seen.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                ss.main(["search", "--budget", "0", "--no-rerank", "こがら"])
            self.assertEqual(seen, {})
        finally:
            ss.Semantic = saved_sem
            os.environ.clear()
            os.environ.update(saved_env)

    def test_forgotten_thread_is_not_ingested_again(self):
        path = self.write(T1, paginated(T1, [("わすれる ひよ", "答え", None)]))
        self.search("ひよ")
        self.run_cli("forget", T1)
        with open(path, "a", encoding="utf-8") as f:
            f.write(paginated(T1, [("追記 ひよ", "答え", None)]).split("\n", 1)[1])
        self.search("ひよ")
        idx = self.idx()
        self.assertEqual(idx.db.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (T1,)).fetchone()[0], 0)
        idx.close()

    def test_old_vectors_table_is_rebuilt(self):
        import sqlite3
        idx = self.idx()
        idx.close()
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE vectors")
        con.execute("CREATE TABLE vectors (message_id INTEGER PRIMARY KEY, model TEXT, vec BLOB)")
        con.execute("UPDATE meta SET value = '2' WHERE key = 'schema'")
        con.commit()
        con.close()
        idx = self.idx()
        cols = [r[1] for r in idx.db.execute("PRAGMA table_info(vectors)")]
        idx.close()
        self.assertIn("chunk", cols)

    def test_rebuild_leaves_no_trace_in_the_file(self):
        self.write(T1, paginated(T1, [("qzxjvwkpunique の話", "答え", None)]))
        self.search("qzxjvwkpunique")
        self.run_cli("forget", T1)
        self.run_cli("rebuild")
        with open(self.db, "rb") as f:
            self.assertNotIn("qzxjvwkpunique".encode(), f.read())

    def test_forgotten_bad_lines_do_not_stay_in_the_notice(self):
        self.write(T1, paginated(T1, [("わる つぐみ", "答え", None)]) + "{broken\n")
        self.search("つぐみ")
        self.run_cli("forget", T1)
        out = self.search("なにか")
        self.assertFalse(any("壊れた行" in c for c in out["coverage"]), out["coverage"])


class SixthReviewTest(_Base):
    """6 回目のレビューで見つかった欠陥の再現。"""

    def idx(self):
        return ss.Index(self.db)

    def _fork(self, parent_turns, child_turns):
        parent = paginated(T1, parent_turns)
        own = line("2026-09-02T01:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        return parent, own + "\n" + parent + paginated(T2, child_turns).split("\n", 1)[1]

    def test_forget_then_parent_appended_hides_the_new_copies_too(self):
        parent, _ = self._fork([("親の依頼 ほしがらす", "親の答え", None)], [])
        path = self.write(T1, parent)
        self.search("ほしがらす")
        self.run_cli("forget", T1)
        more = parent + paginated(T1, [("親の追記 ほしがらす", "答え", None)]).split("\n", 1)[1]
        with open(path, "w", encoding="utf-8") as f:
            f.write(more)
        self.search("ほしがらす")  # 原本を読み直す（除外のハッシュを足す）
        own = line("2026-09-02T01:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        self.write(T2, own + "\n" + more + paginated(T2, [("子の依頼 ほしがらす", "答え", None)]).split("\n", 1)[1])
        out = json.dumps(self.search("--all-kinds", "ほしがらす"), ensure_ascii=False)
        self.assertNotIn("親の追記", out)
        self.assertIn("子の依頼", out)

    def test_old_forget_without_keys_still_hides_copies(self):
        parent, child = self._fork([("親の依頼 いすか", "親の答え", None)], [("子の依頼 いすか", "答え", None)])
        self.write(T1, parent)
        self.write(T2, child)
        self.search("いすか")
        self.run_cli("forget", T1)
        idx = self.idx()
        with idx.db:  # forgotten_keys が無かったバージョンで forget した状態にする
            idx.db.execute("DELETE FROM forgotten_copies")
            idx.db.execute("DELETE FROM meta WHERE key = 'keyed'")
        idx.close()
        self.write(T2, child + paginated(T2, [("子の追記", "答え", None)]).split("\n", 1)[1])  # 子を読み直させる
        out = json.dumps(self.search("--all-kinds", "いすか"), ensure_ascii=False)
        self.assertNotIn("親の依頼", out)

    def test_forget_reexecs_like_search(self):
        calls = []
        saved = ss.maybe_reexec
        ss.maybe_reexec = lambda home, argv: calls.append(argv[0]) or None
        try:
            self.write(T1, paginated(T1, [("みそさざい", "答え", None)]))
            saved_env = dict(os.environ)
            os.environ.update(self.env)
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                ss.main(["forget", T1])
        finally:
            ss.maybe_reexec = saved
            os.environ.clear()
            os.environ.update(saved_env)
        self.assertEqual(calls, ["forget"])

    def test_more_secret_shapes_6(self):
        cases = [
            ('$password2 = "assignmentSecret42"', "assignmentSecret42"),
            ("passwordValue: valueSecret42", "valueSecret42"),
            ("Authorization: SharedKey account:sharedKey42", "sharedKey42"),
            ('("SecureStringSecret42") | ConvertTo-SecureString -AsPlainText -Force', "SecureStringSecret42"),
            ('password = "' + "a" * 4001 + ' tailSecret42"', "tailSecret42"),
        ]
        for text, secret in cases:
            self.assertNotIn(secret, ss.redact(text), text[:60])

    def test_secrets_are_redacted_in_the_stored_text(self):
        self.write(T1, paginated(T1, [("token=abcd1234efgh の件", "password: hunter22 は消した", None)]))
        self.search("件")
        idx = self.idx()
        stored = " ".join(r[0] for r in idx.db.execute("SELECT text || ' ' || norm FROM messages"))
        idx.close()
        self.assertNotIn("abcd1234efgh", stored)
        self.assertNotIn("hunter22", stored)

    def test_shorter_copy_does_not_swallow_the_longer_original(self):
        longer = paginated(T1, [("前半 こうなご", "答え", None), ("後半だけ ほっけ", "答え", None)])
        shorter = paginated(T1, [("前半 こうなご", "答え", None)])
        a = self.write(T1, longer)
        self.write(T1, shorter, sub=("archived_sessions",))
        self.assertTrue(self.search("ほっけ")["results"])
        os.remove(a)
        out = self.search("ほっけ")
        self.assertTrue(out["results"])  # 長いほうにしか無い発言は、原本なしとして残す
        self.assertTrue(any("原本が見つからない" in c for c in out["coverage"]), out["coverage"])

    def test_fork_copies_with_rewritten_timestamps_are_hidden(self):
        # Codex のコピー型の fork は、写した行の時刻を fork した時刻に書き換える
        parent = paginated(T1, [("親の依頼 ごじゅうから", "親の答え", None)], ts="2026-09-01T01:00:00Z")
        copied = paginated(T1, [("親の依頼 ごじゅうから", "親の答え", None)], ts="2026-09-02T05:00:00Z").split("\n", 1)[1]
        own = line("2026-09-02T05:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        self.write(T1, parent)
        self.write(T2, own + "\n" + line("2026-09-02T05:00:00Z", "session_meta", {"id": T1}) + "\n" + copied
                   + paginated(T2, [("子の依頼 ごじゅうから", "答え", None)], ts="2026-09-02T05:10:00Z").split("\n", 1)[1])
        self.search("ごじゅうから")
        self.run_cli("forget", T1)
        out = json.dumps(self.search("--all-kinds", "ごじゅうから"), ensure_ascii=False)
        self.assertNotIn("親の依頼", out)
        self.assertIn("子の依頼", out)

    def test_same_text_in_an_unrelated_thread_is_kept(self):
        self.write(T1, paginated(T1, [("よくある依頼 ひわ", "答え", None)]))
        self.write(T3, paginated(T3, [("よくある依頼 ひわ", "答え", None)]))  # fork ではない別の会話
        self.search("ひわ")
        self.run_cli("forget", T1)
        refs = {r["ref"].split("/")[0] for r in self.search("ひわ")["results"]}
        self.assertEqual(refs, {T3})

    def test_agent_eval_failed_run_is_not_correct(self):
        spec2 = importlib.util.spec_from_file_location("agent_eval", os.path.join(ROOT, "evals", "session-search", "agent_eval.py"))
        ae = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(ae)
        out = os.path.join(self.tmp.name, "ae")

        class Args:
            pass
        args = Args()
        args.out, args.plain, args.db, args.model, args.effort, args.timeout = out, out, os.path.join(out, "x.sqlite"), "m", "low", 10
        q = {"id": "q1", "question": "?", "gold": [T1], "type": "exact"}

        class Done:
            returncode = 1
        saved = ae.subprocess.run

        def fake_run(cmd, **kw):
            o = cmd[cmd.index("-o") + 1]
            with open(o, "w", encoding="utf-8") as f:
                json.dump({"found": True, "thread_id": T1, "answer": "a", "quote": "q"}, f)
            return Done()
        ae.subprocess.run = fake_run
        try:
            r = ae.run_one(args, "search", q, "wrapper", "schema.json")
        finally:
            ae.subprocess.run = saved
        self.assertFalse(r["correct"])


class SeventhReviewTest(_Base):
    """6 回目のレビュー（Claude 側）で見つかった欠陥と、検査の抜けの再現。"""

    def idx(self):
        return ss.Index(self.db)

    def test_changed_redaction_rules_are_applied_to_an_old_index(self):
        self.write(T1, paginated(T1, [("postgresql://admin:Sup3rSECRETzz@db/app つぐみ", "答え", None)]))
        self.search("つぐみ")
        idx = self.idx()
        leaked = "postgresql://admin:Sup3rSECRETzz@db/app つぐみ"
        with idx.db:  # 前の規則で取り込んだ状態（値がそのまま残っている）にする
            idx.db.execute("UPDATE messages SET text = ?, norm = ?, cwd = ? WHERE kind = 'user'",
                           (leaked, ss.normalize(leaked), "password=CwdSecret42"))
            idx.db.execute("UPDATE meta SET value = 'old' WHERE key = 'redact'")
        idx.close()
        self.search("つぐみ")
        idx = self.idx()
        stored = " ".join("%s %s %s" % tuple(r) for r in idx.db.execute("SELECT text, norm, cwd FROM messages"))
        idx.close()
        self.assertNotIn("Sup3rSECRETzz", stored)
        self.assertNotIn(ss.normalize("Sup3rSECRETzz"), stored)
        self.assertNotIn("CwdSecret42", stored)

    def test_text_output_redacts_before_cutting(self):
        # 抜粋の窓の左端が「admin:」の途中に来るようにする（窓は当たった位置の SNIPPET 文字前から）
        secret = "postgresql://admin:Sup3rSECRETzz@db.example/app "
        head = "x" * 50 + secret
        cut = head.index("admin") + 3
        long = head + "y" * (ss.SNIPPET - (len(head) - cut)) + "ひがら"
        self.assertEqual(long[long.index("ひがら") - ss.SNIPPET:][:4], "in:S")
        self.write(T1, paginated(T1, [(long, "答え", None)]))
        self.search("ひがら")
        idx = self.idx()
        with idx.db:
            idx.db.execute("UPDATE messages SET text = ?, norm = ? WHERE kind = 'user'", (long, ss.normalize(long)))
        idx.close()
        code, out = self.run_cli("search", "--budget", "0", "ひがら")
        self.assertNotIn("Sup3rSECRETzz", out)

    def test_forget_accepts_uppercase_hex_ids(self):
        tid = "abcdef01-2345-4678-89ab-cdef01234567"
        self.write(tid, paginated(tid, [("おおばん", "答え", None)]))
        self.search("おおばん")
        self.assertEqual(self.run_cli("forget", tid.upper())[0], 0)
        self.assertFalse(self.search("おおばん")["results"])

    def test_child_fork_ingested_after_forget_is_hidden(self):
        parent = paginated(T1, [("親の依頼 あまさぎ", "親の答え", None)])
        self.write(T1, parent)
        self.search("あまさぎ")
        self.run_cli("forget", T1)
        own = line("2026-09-02T01:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        self.write(T2, own + "\n" + parent + paginated(T2, [("子の依頼 あまさぎ", "答え", None)]).split("\n", 1)[1])
        out = json.dumps(self.search("--all-kinds", "あまさぎ"), ensure_ascii=False)
        self.assertNotIn("親の依頼", out)
        idx = self.idx()
        self.assertTrue(idx.db.execute("SELECT COUNT(*) FROM forgotten_copies").fetchone()[0])
        idx.close()

    def test_message_cwd_is_redacted_when_stored(self):
        self.write(T1, paginated(T1, [("ばん", "答え", None)], cwd="C:\\work\\password=CwdLeak42"))
        self.search("ばん")
        idx = self.idx()
        cwds = " ".join(r[0] or "" for r in idx.db.execute("SELECT cwd FROM messages"))
        idx.close()
        self.assertNotIn("CwdLeak42", cwds)

    def test_agent_threads_are_judged_per_home(self):
        other = os.path.join(self.tmp.name, "codex2")
        os.makedirs(os.path.join(other, "sessions"))
        with open(os.path.join(other, "sessions", "rollout-2026-09-01T01-00-00-%s.jsonl" % T1), "w", encoding="utf-8") as f:
            f.write(paginated(T1, [("くいな", "答え", None)], agent="worker"))
        env = dict(self.env, CODEX_HOME=other)
        subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "くいな"], capture_output=True, env=env)
        self.write(T1, paginated(T1, [("くいな", "答え", None)]))  # こちらの CODEX_HOME では子の会話ではない
        self.assertTrue(self.search("くいな")["results"])

    def test_incomplete_tail_is_not_counted_as_broken(self):
        text = paginated(T1, [("みみかいつぶり", "答え", None)])
        _, msgs, counters = ss.parse_rollout("x", (text + '{"timestamp": "x", "type": "ev').encode())
        self.assertEqual((counters["incomplete_tail"], counters["bad_json"]), (1, 0))

    def test_corrupt_index_can_be_discarded_and_rebuilt(self):
        self.write(T1, paginated(T1, [("こじゅけい", "答え", None)]))
        self.search("こじゅけい")
        with open(self.db, "r+b") as f:
            f.write(b"not a database at all" * 10)
        code, out = self.run_cli("search", "こじゅけい")
        self.assertNotEqual(code, 0)
        self.assertIn("壊れている", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.run_cli("rebuild", "--discard-broken")[0], 0)
        self.assertTrue(self.search("こじゅけい")["results"])

    def test_unreadable_zst_that_disappears_is_not_counted(self):
        path = self.write(T1, "z", suffix=".jsonl.zst")
        saved = ss.zstd
        idx = self.idx()
        try:
            ss.zstd = None
            idx.ingest(self.home)
            os.remove(path)
            idx.ingest(self.home)
        finally:
            ss.zstd = saved
        cov = idx.coverage(self.home)
        idx.close()
        self.assertEqual((cov["missing"], cov["zst_unreadable"]), (0, 0))

    def test_more_secret_shapes_7(self):
        cases = [
            ("Set-VSTeamAccount -PersonalAccessToken patValue4242", "patValue4242"),
            ("New-Thing -SecurePassword securePw4242", "securePw4242"),
            ("docker login -u me -p dockerPw4242 registry", "dockerPw4242"),
            ("sqlcmd -S db -U sa -P sqlPw4242", "sqlPw4242"),
            ("cmdkey /add:host /user:me /pass:cmdPw4242", "cmdPw4242"),
            ("net use \\\\host\\share netPw4242 /user:me", "netPw4242"),
            ('<add key="ClearTextPassword" value="nugetPw4242" />', "nugetPw4242"),
            ("<password>xmlPw4242</password>", "xmlPw4242"),
        ]
        for text, secret in cases:
            self.assertNotIn(secret, ss.redact(text), text)

    def test_signature_is_stable_and_reflects_rules(self):
        a = ss._redact_signature()
        self.assertEqual(a, ss._redact_signature())
        saved = list(ss._SECRET_PATTERNS)
        try:
            ss._SECRET_PATTERNS.append((ss.re.compile("zzz"), "x"))
            self.assertNotEqual(a, ss._redact_signature())
        finally:
            ss._SECRET_PATTERNS[:] = saved


class EighthReviewTest(_Base):
    """7 回目のレビューで見つかった欠陥の再現。"""

    def idx(self):
        return ss.Index(self.db)

    def _chain(self, ts_b="2026-09-02T01:00:00Z", ts_c="2026-09-03T01:00:00Z"):
        a = paginated(T1, [("祖父の依頼 うそ", "答え", None)])
        b_own = line(ts_b, "session_meta", {"id": T2, "forked_from_id": T1})
        b = b_own + "\n" + a + paginated(T2, [("親の依頼 うそ", "答え", None)], ts=ts_b).split("\n", 1)[1]
        c_own = line(ts_c, "session_meta", {"id": T3, "forked_from_id": T2})
        c = c_own + "\n" + b + paginated(T3, [("孫の依頼 うそ", "答え", None)], ts=ts_c).split("\n", 1)[1]
        return a, b, c

    def test_quoted_short_option_passwords(self):
        for text, secret in [('docker login -u me -p "dockerPw4242" registry', "dockerPw4242"),
                             ('sqlcmd -S db -P "sqlPw4242"', "sqlPw4242"),
                             ('cmdkey /add:h /user:me /pass:"cmdPw4242"', "cmdPw4242"),
                             ('net use \\\\host\\share "network secret42" /user:me', "secret42")]:
            self.assertNotIn(secret, ss.redact(text), text)

    def test_forgetting_parent_then_child_keeps_grandchild_clean(self):
        a, b, c = self._chain()
        self.write(T1, a)
        self.write(T2, b)
        path_c = self.write(T3, c)
        self.search("うそ")
        self.run_cli("forget", T1)
        self.search("うそ")
        self.run_cli("forget", T2)
        with open(path_c, "a", encoding="utf-8") as f:
            f.write(paginated(T3, [("孫の追記", "答え", None)]).split("\n", 1)[1])
        out = json.dumps(self.search("--all-kinds", "うそ"), ensure_ascii=False)
        self.assertNotIn("祖父の依頼", out)
        self.assertNotIn("親の依頼", out)
        self.assertIn("孫の依頼", out)

    def test_grandchild_ingested_before_child_is_cleaned(self):
        a, b, c = self._chain()
        self.write(T1, a)
        self.search("うそ")
        self.run_cli("forget", T1)
        # 孫が先に読まれるよう、孫を archived_sessions、子を sessions に置く（パスの並びで archived_sessions が先）
        self.write(T3, c, sub=("archived_sessions",))
        self.write(T2, b)
        out = json.dumps(self.search("--all-kinds", "うそ"), ensure_ascii=False)
        self.assertNotIn("祖父の依頼", out)

    def test_old_forget_is_rekeyed_in_every_home(self):
        other = os.path.join(self.tmp.name, "codex2")
        os.makedirs(os.path.join(other, "sessions"))
        a, b, _ = self._chain()
        envs = [self.env, dict(self.env, CODEX_HOME=other)]
        homes = [self.home, other]
        for home, env in zip(homes, envs):
            with open(os.path.join(home, "sessions", "rollout-2026-09-01T01-00-00-%s.jsonl" % T1), "w", encoding="utf-8") as f:
                f.write(a)
            subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "うそ"], capture_output=True, env=env)
            subprocess.run([sys.executable, SCRIPT, "forget", T1], capture_output=True, env=env)
        idx = self.idx()
        with idx.db:  # 鍵を持たない前のバージョンの forget の状態にする
            idx.db.execute("DELETE FROM forgotten_copies")
            idx.db.execute("DELETE FROM meta WHERE key LIKE 'keyed%'")
        idx.close()
        for env in envs:
            subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "うそ"], capture_output=True, env=env)
        with open(os.path.join(other, "sessions", "rollout-2026-09-02T01-00-00-%s.jsonl" % T2), "w", encoding="utf-8") as f:
            f.write(b)
        r = subprocess.run([sys.executable, SCRIPT, "search", "--json", "--budget", "0", "--all-kinds", "うそ"],
                           capture_output=True, encoding="utf-8", env=envs[1])
        self.assertNotIn("祖父の依頼", r.stdout)

    def test_previously_read_zst_that_becomes_unreadable_then_vanishes_is_kept(self):
        path = self.write(T1, "x", suffix=".jsonl.zst")
        idx = self.idx()
        saved = ss.zstd

        class Fake:
            @staticmethod
            def decompress(_):
                return paginated(T1, [("圧縮の中 つぐみ", "答え", None)]).encode()
        try:
            ss.zstd = Fake
            idx.ingest(self.home)
            ss.zstd = None
            with open(path, "a") as f:
                f.write("y")  # 変わったので読み直すが、読めない
            idx.ingest(self.home)
            os.remove(path)
            idx.ingest(self.home)
        finally:
            ss.zstd = saved
        n = idx.db.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ?", (T1,)).fetchone()[0]
        idx.close()
        self.assertEqual(n, 2)

    def test_eval_embedding_cache_follows_the_text(self):
        spec2 = importlib.util.spec_from_file_location("run_eval", os.path.join(ROOT, "evals", "session-search", "run_eval.py"))
        re_mod = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(re_mod)
        a = re_mod.cache_key("m", [{"id": 1, "text": "一"}, {"id": 2, "text": "二"}])
        b = re_mod.cache_key("m", [{"id": 1, "text": "一"}, {"id": 2, "text": "別"}])
        self.assertNotEqual(a, b)


class NinthReviewTest(_Base):
    """7 回目のレビュー（Claude 側）で見つかった欠陥と、検査の抜けの再現。"""

    def idx(self):
        return ss.Index(self.db)

    def test_ordinary_words_after_pat_and_token_are_kept(self):
        for text in ("path: $(Pipeline.Workspace)/.pip にして、patch: 振る舞いを変える",
                     "tokenizer: SudachiPy の言い回し", "secretary: 山田", "paths: [src]"):
            self.assertEqual(ss.redact(text), text)
        for text, secret in (("password2: Hunter2zz", "Hunter2zz"), ("passwordValue = 'Hunter3zz'", "Hunter3zz"),
                             ("DB_PASSWORD_HASH: Hunter4zz", "Hunter4zz")):
            self.assertNotIn(secret, ss.redact(text), text)

    def test_more_secret_shapes_8(self):
        cases = [
            ("az login --service-principal -u app -p azSecret4242 --tenant t", "azSecret4242"),
            ("az acr login -n reg -u me -p acrSecret4242", "acrSecret4242"),
            ("curl -u admin:curlSecret4242 https://x", "curlSecret4242"),
            ("curl --user admin:curlSecret5252 https://x", "curlSecret5252"),
            ("mysql -u root -pMysqlSecret42 db", "MysqlSecret42"),
            ("net use Z: \\\\srv\\share netSecret4242 /user:x", "netSecret4242"),
            ("Set-AzStorage -StorageAccountKey storKey4242", "storKey4242"),
            ("az storage blob list --account-key acctKey4242", "acctKey4242"),
            ("Ocp-Apim-Subscription-Key: apimKey4242", "apimKey4242"),
            ("x-functions-key: funcKey4242", "funcKey4242"),
            ("https://app.azurewebsites.net/api/x?code=AbCdEfGhIjKlMnOpQrStUv12==", "AbCdEfGhIjKlMnOpQrStUv12"),
            ("schtasks /create /ru me /rp schSecret4242 /tn x", "schSecret4242"),
            ("psexec \\\\host -u me -p psSecret4242 cmd", "psSecret4242"),
            ("sshpass -p sshSecret4242 ssh host", "sshSecret4242"),
            ("token ghp_" + "a1B2" * 9, "a1B2a1B2"),
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "dozjgNryP4J3jVmNHl0w5N"),
            ("パスワードは「Kanji5Secret42」です", "Kanji5Secret42"),
        ]
        for text, secret in cases:
            self.assertNotIn(secret, ss.redact(text), text)
        self.assertIn("\\\\srv\\share", ss.redact("net use Z: \\\\srv\\share netSecret4242 /user:x"))  # 共有のパスは残す

    def _fork_pair(self):
        parent = paginated(T1, [("親の依頼 くろじ", "親の答え", None)], ts="2026-09-01T01:00:00Z")
        copied = paginated(T1, [("親の依頼 くろじ", "親の答え", None)], ts="2026-09-02T05:00:00Z").split("\n", 1)[1]
        own = line("2026-09-02T05:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        child = (own + "\n" + line("2026-09-02T05:00:00Z", "session_meta", {"id": T1}) + "\n" + copied
                 + paginated(T2, [("子の依頼 くろじ", "答え", None)], ts="2026-09-02T05:10:00Z").split("\n", 1)[1])
        return parent, child

    def test_migration_fills_forked_from_and_hides_old_copies(self):
        parent, child = self._fork_pair()
        self.write(T1, parent)
        self.write(T2, child)
        self.search("くろじ")
        idx = self.idx()
        with idx.db:  # forked_from の列も鍵も無い前のバージョンの索引で、親を forget した状態にする
            idx.db.execute("INSERT OR REPLACE INTO forgotten VALUES (?, ?, 'x')", (self.home, T1))
            for (path,) in idx.db.execute("SELECT path FROM files WHERE thread_id = ?", (T1,)).fetchall():
                idx._delete_path(path)
                idx.db.execute("UPDATE files SET status = 'forgotten' WHERE path = ?", (path,))
            idx.db.execute("DELETE FROM forgotten_copies")
            idx.db.execute("DELETE FROM meta WHERE key LIKE 'keyed%'")
            idx.db.execute("CREATE TABLE threads_old (thread_id TEXT, codex_home TEXT, name TEXT, cwd TEXT, branch TEXT, "
                           "originator TEXT, agent TEXT, started TEXT, PRIMARY KEY (codex_home, thread_id))")
            idx.db.execute("INSERT INTO threads_old SELECT thread_id, codex_home, name, cwd, branch, originator, agent, started FROM threads")
            idx.db.execute("DROP TABLE threads")
            idx.db.execute("ALTER TABLE threads_old RENAME TO threads")
        idx.close()
        out = json.dumps(self.search("--all-kinds", "くろじ"), ensure_ascii=False)
        self.assertNotIn("親の依頼", out)
        self.assertIn("子の依頼", out)

    def test_changed_rules_rekey_forgotten_threads(self):
        parent = paginated(T1, [("親の依頼 password=ruleSecret42 しまあじ", "親の答え", None)])
        own = line("2026-09-02T01:00:00Z", "session_meta", {"id": T2, "forked_from_id": T1})
        child = own + "\n" + parent + paginated(T2, [("子の依頼 しまあじ", "答え", None)]).split("\n", 1)[1]
        self.write(T1, parent)
        self.write(T2, child)
        self.search("しまあじ")
        self.run_cli("forget", T1)
        idx = self.idx()
        with idx.db:  # 前の規則で作った鍵（伏せ字の結果が今と違う）だけを持つ状態にする
            idx.db.execute("UPDATE forgotten_copies SET key = 'old-' || key")
            idx.db.execute("UPDATE meta SET value = 'old' WHERE key = 'redact'")
            # 前のバージョンの forget の読み直し（keyed の印が無いとき）を通らないようにする
            idx.db.execute("INSERT OR REPLACE INTO meta VALUES (?, '1')", ("keyed:" + self.home,))
        idx.close()
        self.search("しまあじ")
        self.run_cli("rebuild")
        out = json.dumps(self.search("--all-kinds", "しまあじ"), ensure_ascii=False)
        self.assertNotIn("親の依頼", out)

    def test_corruption_in_the_middle_is_reported(self):
        for n in range(40):
            tid = "7777%04d-7777-4777-8777-777777777777" % n
            self.write(tid, paginated(tid, [("ページ %d " % n + "ほうぼう " * 200, "答え", None)]))
        self.search("ほうぼう")
        size = os.path.getsize(self.db)
        with open(self.db, "r+b") as f:
            for off in range(4096 * 3, size - 4096, 4096 * 2):
                f.seek(off + 100)
                f.write(b"\xff" * 64)
        r = subprocess.run([sys.executable, SCRIPT, "search", "--budget", "0", "ほうぼう"], capture_output=True, encoding="utf-8", env=self.env)
        if r.returncode == 0:
            self.skipTest("壊した場所が読まれなかった")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("壊れている", r.stderr)

    def test_reredact_leaves_no_trace_of_the_old_value(self):
        leaked = "postgresql://admin:zqxTraceSecret42@db/app みさご"
        self.write(T1, paginated(T1, [(leaked, "答え", None)]))
        self.search("みさご")
        idx = self.idx()
        with idx.db:  # 前の規則で取り込んで、値がそのまま全文検索の索引に入っている状態にする
            row = idx.db.execute("SELECT id FROM messages WHERE kind = 'user'").fetchone()[0]
            idx.db.execute("UPDATE messages SET text = ?, norm = ? WHERE id = ?", (leaked, ss.normalize(leaked), row))
            idx.db.execute("DELETE FROM msg_fts WHERE rowid = ?", (row,))
            idx.db.execute("INSERT INTO msg_fts (rowid, norm) VALUES (?, ?)", (row, ss.normalize(leaked)))
            idx.db.execute("UPDATE meta SET value = 'old' WHERE key = 'redact'")
        idx.close()
        self.search("みさご")
        idx = self.idx()
        blob = b"".join(r[0] or b"" for r in idx.db.execute("SELECT block FROM msg_fts_data"))
        text = " ".join(r[0] for r in idx.db.execute("SELECT text FROM messages"))
        idx.close()
        self.assertNotIn("zqxTraceSecret42", text)
        self.assertNotIn(b"zqx", blob)  # 3 文字ずつの断片も、全文検索の内部の区画に残らない

    def test_vanished_no_messages_file_is_not_counted(self):
        text = line("2026-09-01T01:00:00Z", "session_meta", {"id": T1}) + "\n" + line(
            "2026-09-01T01:00:00Z", "response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "x"}]}) + "\n"
        path = self.write(T1, text)
        self.search("なにか")
        os.remove(path)
        out = self.search("なにか")
        self.assertFalse(any("原本が見つからない" in c or "発言を取り出せなかった" in c for c in out["coverage"]), out["coverage"])

    def test_growing_conversation_keeps_earlier_embeddings(self):
        path = self.write(T1, paginated(T1, [("一つ目 やまがら", "答え", None)]))
        idx = self.idx()
        idx.ingest(self.home)
        calls = []

        def encode(texts):
            calls.append(len(texts))
            import array
            return [type("V", (), {"tobytes": lambda self: array.array("f", [1.0, 0.0]).tobytes()})() for _ in texts]
        idx.fill_vectors(encode, self.home)
        idx.fill_words(lambda text: ss.normalize(text).split(), self.home)
        with open(path, "a", encoding="utf-8") as f:
            f.write(paginated(T1, [("二つ目 やまがら", "答え", None)]).split("\n", 1)[1].replace("u1", "u2").replace("f1", "f2"))
        idx.ingest(self.home)
        calls.clear()
        left = idx.fill_vectors(encode, self.home)
        words_left = idx.db.execute("SELECT COUNT(*) FROM messages WHERE id NOT IN (SELECT message_id FROM word_done)").fetchone()[0]
        idx.close()
        self.assertEqual((left, sum(calls), words_left), (0, 2, 2))  # 増えた 2 件だけを作る

    def test_narrowed_rules_restore_over_redacted_text(self):
        self.write(T1, paginated(T1, [("path: $(Pipeline.Workspace)/.pip に変える おおよしきり", "答え", None)]))
        self.search("おおよしきり")
        idx = self.idx()
        over = "path: [REDACTED] に変える おおよしきり"
        with idx.db:  # 前の（広すぎた）規則で伏せた状態にする
            idx.db.execute("UPDATE messages SET text = ?, norm = ? WHERE kind = 'user'", (over, ss.normalize(over)))
            idx.db.execute("UPDATE meta SET value = 'old' WHERE key = 'redact'")
        idx.close()
        out = json.dumps(self.search("Pipeline.Workspace"), ensure_ascii=False)
        self.assertIn("Pipeline.Workspace", out)

    def test_forget_alone_removes_existing_copies(self):
        parent, child = self._fork_pair()
        self.write(T1, parent)
        self.write(T2, child)
        self.search("くろじ")
        idx = self.idx()
        with idx.db:
            idx.db.execute("INSERT OR REPLACE INTO meta VALUES (?, '1')", ("keyed:" + self.home,))
        idx.close()
        self.run_cli("forget", T1)  # 検索を挟まずに確かめる（読み直しに頼らない）
        idx = self.idx()
        n = idx.db.execute("SELECT COUNT(*) FROM messages WHERE thread_id = ? AND text LIKE '親の%'", (T2,)).fetchone()[0]
        idx.close()
        self.assertEqual(n, 0)


class SemanticLayerTest(unittest.TestCase):
    """SudachiPy が入っている環境でだけ、単語の BM25 の経路を確かめる。"""

    def setUp(self):
        try:
            import sudachipy  # noqa: F401
        except ImportError:
            self.skipTest("SudachiPy が無い")

    def test_tokens_keep_both_the_normalized_and_the_written_form(self):
        tok = ss.Semantic().tokenizer()
        words = tok("同じ処理を繰り返し流した")
        self.assertIn("繰り返し", words)  # 書かれたままの形
        self.assertIn("繰り返す", words)  # 正規化した形

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
