"""プロンプト類（.md / .txt / .json / .jsonl）を機械的に検査する。

見るもの:
  - 文字コード: UTF-8 として読めるか、BOM の有無
  - 改行: 1 ファイルに CRLF と LF が混ざっていないか
  - JSON / JSONL: 構文、同じオブジェクト内の重複キー
  - テンプレート変数（--var-style を渡したときだけ）: 書式の誤り、定義との突き合わせ
  - サイズ（--max-chars を渡したときだけ）

テンプレート変数は、その方式の本来のパーサで読む（brace は string.Formatter、dollar は string.Template）。
JSON ファイルでは文字列の値だけを対象にする。
"""

import argparse
import json
import os
import re
import string
import sys

EXTENSIONS = {".md", ".txt", ".json", ".jsonl"}
DOUBLE_BRACE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class Finding:
    def __init__(self, path, line, level, code, message):
        self.path, self.line, self.level, self.code, self.message = path, line, level, code, message

    def as_dict(self):
        return {"path": self.path, "line": self.line, "level": self.level, "code": self.code, "message": self.message}

    def __str__(self):
        return "%s:%s: %s: %s: %s" % (self.path, self.line or "-", self.level, self.code, self.message)


class DuplicateKey(Exception):
    pass


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise DuplicateKey(k)
        seen[k] = v
    return seen


def _line_of(text, index):
    return text.count("\n", 0, index) + 1


# --- テンプレート変数 ------------------------------------------------------------------------


def vars_brace(text):
    """str.format の書式。戻り値は (変数名の集合, 誤りの説明 or None)。"""
    names = set()
    try:
        return names, _brace_fields(text, names, nested=False)
    except ValueError as e:
        return names, "str.format として読めない: %s" % e


def _brace_fields(text, names, nested):
    for _, field, spec, _ in string.Formatter().parse(text):
        if field is None:
            continue
        name = re.split(r"[.\[]", field, 1)[0]
        if name == "" or name.isdigit():
            return "位置引数の {} / {0} がある（名前付きにするか、波括弧を {{ }} でエスケープする）"
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            # str.format は {"key": 1} も「"key" という名前」として受け付け、実行時に KeyError になる
            return "{%s} が変数名として読めない（JSON の例なら波括弧を {{ }} でエスケープする）" % field
        names.add(name)
        # {value:{width}.{precision}f} の width / precision も変数（入れ子は str.format の仕様で 1 段まで）
        if spec and not nested:
            error = _brace_fields(spec, names, nested=True)
            if error:
                return error
    return None


def vars_dollar(text):
    """string.Template の書式。"""
    names = set()
    for m in string.Template.pattern.finditer(text):
        if m.group("invalid") is not None:
            return names, "string.Template として読めない $ がある（位置 %d。$$ でエスケープする）" % m.start("invalid")
        name = m.group("named") or m.group("braced")
        if name:
            names.add(name)
    return names, None


def vars_double_brace(text):
    """{{ name }} の書式（Jinja / Mustache 風）。名前として読めないものと、閉じていない {{ を誤りにする。"""
    names = set()
    for m in DOUBLE_BRACE.finditer(text):
        inner = m.group(1)
        if not IDENTIFIER.match(inner):
            return names, "{{ }} の中が変数名として読めない: %r" % inner
        names.add(inner)
    stripped = DOUBLE_BRACE.sub("", text)
    if "{{" in stripped or "}}" in stripped:
        return names, "閉じていない {{ または対応しない }} がある"
    return names, None


VAR_STYLES = {"brace": vars_brace, "dollar": vars_dollar, "double-brace": vars_double_brace}


def _json_strings(value, pointer=""):
    if isinstance(value, str):
        yield pointer, value
    elif isinstance(value, dict):
        for k, v in value.items():
            for item in _json_strings(v, pointer + "/" + str(k)):
                yield item
    elif isinstance(value, list):
        for i, v in enumerate(value):
            for item in _json_strings(v, pointer + "/" + str(i)):
                yield item


# --- 1 ファイルの検査 ------------------------------------------------------------------------


def check_file(path, var_style=None, defined=None, max_chars=None):
    findings = []
    used = set()
    with open(path, "rb") as f:
        raw = f.read()

    if raw.startswith(b"\xef\xbb\xbf"):
        findings.append(Finding(path, 1, "warning", "bom", "UTF-8 の BOM が付いている"))
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        findings.append(Finding(path, None, "error", "encoding", "UTF-8 として読めない（%s）。Shift_JIS などの可能性" % e.reason))
        return findings, used

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        findings.append(Finding(path, None, "warning", "mixed-newline", "改行が混在（CRLF %d 行、LF %d 行）" % (crlf, lf)))

    if max_chars is not None and len(text) > max_chars:
        findings.append(Finding(path, None, "warning", "size", "%d 文字（上限 %d）" % (len(text), max_chars)))

    ext = os.path.splitext(path)[1].lower()
    targets = []  # (行番号, 場所の説明, 検査する文字列)
    if ext == ".json":
        try:
            data = json.loads(text, object_pairs_hook=_no_duplicates)
        except DuplicateKey as e:
            findings.append(Finding(path, None, "error", "duplicate-key", "重複キー: %r（後の値が前を黙って上書きする）" % str(e)))
            return findings, used
        except ValueError as e:
            findings.append(Finding(path, getattr(e, "lineno", None), "error", "json", str(e)))
            return findings, used
        targets = [(None, ptr or "/", s) for ptr, s in _json_strings(data)]
    elif ext == ".jsonl":
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line, object_pairs_hook=_no_duplicates)
            except DuplicateKey as e:
                findings.append(Finding(path, n, "error", "duplicate-key", "重複キー: %r" % str(e)))
                continue
            except ValueError as e:
                findings.append(Finding(path, n, "error", "json", str(e)))
                continue
            targets.extend((n, ptr or "/", s) for ptr, s in _json_strings(data))
    else:
        targets = [(None, None, text)]

    if var_style:
        parse = VAR_STYLES[var_style]
        for line, where, s in targets:
            names, error = parse(s)
            used |= names
            if error:
                at = " (%s)" % where if where else ""
                findings.append(Finding(path, line, "error", "template", error + at))
        if defined is not None:
            for name in sorted(used - defined):
                findings.append(Finding(path, None, "error", "undefined-var", "定義に無い変数: %s" % name))
    return findings, used


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in sorted(files):
                    if os.path.splitext(name)[1].lower() in EXTENSIONS:
                        yield os.path.join(root, name)
        elif os.path.isfile(p):
            yield p
        else:
            yield p  # 存在しないパスは check で落とす


def load_defined(args):
    if not args.vars_file and not args.var:
        return None
    defined = set(args.var or [])
    if args.vars_file:
        with open(args.vars_file, encoding="utf-8-sig") as f:
            content = f.read()
        try:
            loaded = json.loads(content)
            defined |= set(loaded)
        except ValueError:
            defined |= {line.strip() for line in content.splitlines() if line.strip()}
    return defined


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", help="ファイルかディレクトリ")
    p.add_argument("--var-style", choices=sorted(VAR_STYLES), help="テンプレート変数の書式。渡さなければ変数は見ない")
    p.add_argument("--var", action="append", help="定義済みの変数名（複数可）")
    p.add_argument("--vars-file", help="定義済みの変数名の一覧（JSON 配列か 1 行 1 名）")
    p.add_argument("--max-chars", type=int)
    p.add_argument("--json", action="store_true", help="結果を JSON で出す")
    args = p.parse_args(argv)

    defined = load_defined(args)
    findings = []
    used_all = set()
    checked = 0
    for path in iter_files(args.paths):
        if not os.path.isfile(path):
            findings.append(Finding(path, None, "error", "missing", "ファイルが無い"))
            continue
        checked += 1
        f, used = check_file(path, args.var_style, defined, args.max_chars)
        findings.extend(f)
        used_all |= used
    unused = sorted(defined - used_all) if defined is not None and args.var_style else []

    errors = sum(1 for f in findings if f.level == "error")
    if args.json:
        print(json.dumps({
            "checked": checked,
            "errors": errors,
            "findings": [f.as_dict() for f in findings],
            "variables_used": sorted(used_all),
            "variables_unused": unused,
        }, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f)
        if args.var_style:
            print("変数（使用）: %s" % (", ".join(sorted(used_all)) or "なし"))
        if unused:
            print("変数（定義のみで未使用）: %s" % ", ".join(unused))
        print("検査 %d ファイル、error %d、warning %d" % (checked, errors, len(findings) - errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
