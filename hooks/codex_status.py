"""Codex の UserPromptSubmit のフック。いまのモデル・effort・利用枠の使用率を 1 行で文脈に足す。

Codex のモデルは、自分のモデル名・effort・利用枠の残りを見られない。AGENTS.md の「モデルと effort の案内」を
実際の値で判断できるよう、その会話の rollout（transcript_path）から読んで渡す。

読むもの（rollout は公開された仕様ではなく、Codex の内部の形式）:
  - turn_context の model / effort: そのターンの設定（Codex はフックを呼ぶ前に、そのターンの turn_context を書く）
  - event_msg の token_count の rate_limits: primary / secondary の used_percent・window_minutes・resets_at
  - 先頭の session_meta の originator: codex_exec なら、人が返事をできない非対話の実行

どんな失敗でも終了コード 0 で終わる。フックの失敗で依頼を止めないため。読めなかった値は「不明」と書く。
出力は hookSpecificOutput.additionalContext の JSON（公式は素のテキストも受け付けるが、Codex で動作を確かめた形に揃える）。
JSON は ASCII だけで書く（日本語は \\uXXXX）。Windows では powershell.exe を通して起動されるので、
PowerShell がネイティブのコマンドの出力を読み直すときに、日本語が文字コードの違いで化けないようにするため。
"""

import datetime
import json
import os
import sys

TAIL_BYTES = 8 * 1024 * 1024
WINDOW_NAMES = {300: "5 時間の枠", 10080: "週の枠"}
# 使用率がこれ以上なら、軽い設定を優先して案内するよう添える。
TIGHT_PERCENT = 80


def read_tail_lines(path, tail_bytes=TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - tail_bytes)
        f.seek(start)
        data = f.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # 途中から読んだ最初の行は切れている
    return lines


NON_INTERACTIVE_ORIGINATORS = {"codex_exec"}


def read_originator(path):
    """rollout の 1 行目（session_meta）の originator。読めなければ None。"""
    with open(path, "rb") as f:
        first = f.readline(1024 * 1024)
    try:
        item = json.loads(first.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(item, dict) and item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
        return item["payload"].get("originator")
    return None


def latest_status(lines):
    """後ろから見て、最後の turn_context と、最後の rate_limits を返す。"""
    turn = None
    limits = None
    for raw in reversed(lines):
        if turn is not None and limits is not None:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        if turn is None and item.get("type") == "turn_context":
            turn = payload
        elif (
            limits is None
            and item.get("type") == "event_msg"
            and payload.get("type") == "token_count"
            and isinstance(payload.get("rate_limits"), dict)
        ):
            limits = payload["rate_limits"]
    return turn, limits


def describe_window(window, now):
    """(表示, 使用率) を返す。リセット時刻を過ぎた値は今の使用率ではないので、使用率を None にする。"""
    if not isinstance(window, dict) or window.get("used_percent") is None:
        return None
    minutes = window.get("window_minutes")
    name = WINDOW_NAMES.get(minutes, "%s 分の枠" % minutes if minutes else "枠")
    resets = window.get("resets_at")
    if isinstance(resets, (int, float)) and resets <= now:
        return "%s 不明（記録の後にリセット済み）" % name, None
    text = "%s %d%% 使用" % (name, round(float(window["used_percent"])))
    if isinstance(resets, (int, float)):
        at = datetime.datetime.fromtimestamp(resets).strftime("%m/%d %H:%M")
        text += "（リセット %s）" % at
    return text, float(window["used_percent"])


def build_message(hook_input, turn, limits, now=None, originator=None):
    now = datetime.datetime.now().timestamp() if now is None else now
    model = hook_input.get("model") or (turn or {}).get("model") or "不明"
    effort = (turn or {}).get("effort") or "不明"
    parts = ["モデル %s" % model, "effort %s" % effort]
    tight = False
    windows = []
    if isinstance(limits, dict):
        for key in ("primary", "secondary"):
            described = describe_window(limits.get(key), now)
            if described:
                windows.append(described[0])
                tight = tight or (described[1] is not None and described[1] >= TIGHT_PERCENT)
    parts.append(" / ".join(windows) if windows else "利用枠 不明")
    line = "[利用状況] " + "、".join(parts) + "。"
    if originator in NON_INTERACTIVE_ORIGINATORS:
        # 人が返事をできないので、止めて案内すると処理がそこで終わってしまう。
        return line + "非対話の実行（%s）なので、設定の切り替えの案内はせずに進める。" % originator
    line += "この依頼に勧める設定と違うなら、AGENTS.md の「モデルと effort の案内」に従い、作業の前に案内する。"
    if tight:
        line += "枠が残り少ないので、gpt-6-luna で足りる依頼は必ずそれを案内する。"
    return line


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        hook_input = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        hook_input = {}
    if not isinstance(hook_input, dict):
        hook_input = {}
    # spawn された子でもフックは走る（入力に agent_id が入る）。子は案内しないので、何も足さない。
    if hook_input.get("agent_id"):
        return 0
    turn = limits = originator = None
    path = hook_input.get("transcript_path")
    if isinstance(path, str) and path:
        try:
            turn, limits = latest_status(read_tail_lines(path))
            originator = read_originator(path)
        except OSError:
            pass
    try:
        message = build_message(hook_input, turn, limits, originator=originator)
    except Exception:  # 形の変わった値で落ちても、依頼は止めない
        message = "[利用状況] 不明。この依頼に勧める設定と違うなら、AGENTS.md の「モデルと effort の案内」に従い、作業の前に案内する。"
    event = hook_input.get("hook_event_name") or "UserPromptSubmit"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
