"""git remote の URL から Azure DevOps Server の Collection / Project / Repo を割り出す。

Collection URL は URL だけでは決まらない（Project と Repo が同名だと Project が省かれる）ので、
環境変数 ADO_COLLECTION_URL か --collection-url で受け取り、その後ろを読む。
"""

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit


class ContextError(Exception):
    pass


def _split(url):
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.hostname:
        raise ContextError("URL として読めない: %s" % url)
    segments = [unquote(s) for s in parts.path.split("/") if s]
    return parts.hostname.lower(), segments


def resolve(remote, collection_url):
    remote_host, remote_segs = _split(remote)
    coll_host, coll_segs = _split(collection_url)
    if remote_host != coll_host:
        raise ContextError(
            "remote のホスト %s が ADO_COLLECTION_URL のホスト %s と違う" % (remote_host, coll_host)
        )
    lowered = [s.lower() for s in remote_segs]
    prefix = [s.lower() for s in coll_segs]
    if lowered[: len(prefix)] != prefix:
        raise ContextError(
            "remote のパス /%s が Collection のパス /%s で始まっていない"
            % ("/".join(remote_segs), "/".join(coll_segs))
        )
    rest = remote_segs[len(prefix):]
    if "_git" not in rest:
        raise ContextError("remote に /_git/ が無い。Azure Repos の URL ではない: %s" % remote)
    i = rest.index("_git")
    before, after = rest[:i], rest[i + 1:]
    if after and after[0] in ("_full", "_optimized"):
        after = after[1:]
    if len(after) != 1 or len(before) > 1:
        raise ContextError("Project/_git/Repo の形として読めない: /%s" % "/".join(rest))
    repo = after[0]
    project = before[0] if before else repo
    return {
        "collection_url": collection_url.rstrip("/"),
        "project": project,
        "repo": repo,
        "project_path": quote(project, safe=""),
        "repo_path": quote(repo, safe=""),
        "remote": remote,
    }


def _git_remote(name):
    out = subprocess.run(
        ["git", "remote", "get-url", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    if out.returncode != 0:
        raise ContextError("git remote get-url %s が失敗した: %s" % (name, out.stderr.strip()))
    return out.stdout.strip()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--remote", help="remote の URL。省略時は git remote get-url <--remote-name>")
    p.add_argument("--remote-name", default="origin")
    p.add_argument("--collection-url", default=os.environ.get("ADO_COLLECTION_URL"))
    args = p.parse_args(argv)
    if not args.collection_url:
        print("ADO_COLLECTION_URL が未設定（例: https://tfs.example.local/tfs/DefaultCollection）", file=sys.stderr)
        return 2
    try:
        remote = args.remote or _git_remote(args.remote_name)
        ctx = resolve(remote, args.collection_url)
    except ContextError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(ctx, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
