"""モックの会話（corpus.json）を、Codex の rollout と同じ形式のファイルに書き出す。

    python make_rollouts.py corpus.json <出力の CODEX_HOME>

書き出す形は paginated（session_meta、turn_context、event_msg の item_completed の UserMessage / AgentMessage、
task_complete）。session_search.py の取り込みを本番と同じ経路で通すため。
"""

import datetime
import json
import os
import sys
import uuid


def thread_uuid(tid):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hirata-codex-eval/" + tid))


def to_utc(ts):
    t = datetime.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    return t.astimezone(datetime.timezone.utc)


def write_thread(home, th):
    tid = thread_uuid(th["id"])
    started = to_utc(th["started"])
    day = started.strftime("%Y/%m/%d")
    folder = os.path.join(home, "sessions", *day.split("/"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "rollout-%s-%s.jsonl" % (started.strftime("%Y-%m-%dT%H-%M-%S"), tid))
    lines = []

    def line(ts, typ, payload):
        lines.append(json.dumps({"timestamp": ts.isoformat().replace("+00:00", "Z"), "type": typ, "payload": payload},
                                ensure_ascii=False))

    line(started, "session_meta", {"id": tid, "cwd": th["cwd"], "originator": "codex-tui", "source": "cli",
                                   "history_mode": "paginated", "cli_version": "0.162.1",
                                   "git": {"branch": th.get("branch", "main")}})
    for n, turn in enumerate(th["turns"], 1):
        at = to_utc(turn.get("at") or th["started"]) + datetime.timedelta(seconds=n)
        turn_id = "%s-turn-%d" % (tid, n)
        line(at, "turn_context", {"turn_id": turn_id, "cwd": th["cwd"], "model": "gpt-6.1-sol", "effort": "medium"})
        line(at, "event_msg", {"type": "item_completed", "thread_id": tid, "turn_id": turn_id,
                               "item": {"type": "UserMessage", "id": "u%d" % n,
                                        "content": [{"type": "text", "text": turn["user"], "text_elements": []}]}})
        if turn.get("commentary"):
            line(at, "event_msg", {"type": "item_completed", "thread_id": tid, "turn_id": turn_id,
                                   "item": {"type": "AgentMessage", "id": "c%d" % n, "phase": "commentary",
                                            "content": [{"type": "Text", "text": turn["commentary"]}]}})
        line(at, "event_msg", {"type": "item_completed", "thread_id": tid, "turn_id": turn_id,
                               "item": {"type": "AgentMessage", "id": "f%d" % n, "phase": "final_answer",
                                        "content": [{"type": "Text", "text": turn["final"]}]}})
        line(at, "event_msg", {"type": "task_complete", "turn_id": turn_id, "last_agent_message": turn["final"]})
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return tid


def main(corpus_path, home):
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)
    os.makedirs(home, exist_ok=True)
    index = []
    for th in corpus["threads"]:
        tid = write_thread(home, th)
        index.append(json.dumps({"id": tid, "thread_name": th["name"], "updated_at": th["started"]}, ensure_ascii=False))
    with open(os.path.join(home, "session_index.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(index) + "\n")
    print("書き出した: %d 本 -> %s" % (len(corpus["threads"]), home))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
