"""Runs - Run Pipeline に previewRun で投げる本文を作る（ado.ps1 preview の -BodyFile に渡す）。

previewRun は常に true にする。--yaml を渡すとルートの YAML をそのファイルで置き換える
（yamlOverride が置き換えるのはルートの YAML だけで、参照しているテンプレートは --ref のものが使われる）。
"""

import argparse
import json
import sys


def build(yaml_text=None, ref=None, params=None):
    body = {"previewRun": True}
    if yaml_text is not None:
        body["yamlOverride"] = yaml_text
    if ref:
        body["resources"] = {"repositories": {"self": {"refName": ref}}}
    if params:
        body["templateParameters"] = params
    return body


def parse_params(items):
    params = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError("--param は name=value の形: %r" % item)
        k, v = item.split("=", 1)
        params[k] = v
    return params


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--yaml", help="ルートの YAML の代わりに使うファイル")
    p.add_argument("--ref", help="テンプレートを読むブランチ（例: refs/heads/feature/x）")
    p.add_argument("--param", action="append", help="templateParameters（name=value、複数可）")
    p.add_argument("--out", required=True, help="書き出す JSON（UTF-8、BOM なし）")
    args = p.parse_args(argv)
    yaml_text = None
    if args.yaml:
        with open(args.yaml, encoding="utf-8-sig") as f:
            yaml_text = f.read()
    try:
        body = build(yaml_text, args.ref, parse_params(args.param))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
