"""テストが使う PowerShell を、読み込みの時点で 1 度だけ決める（test_ado_ps1.py と test_hook_command.py が同じものを使う）。

powershell（Windows PowerShell 5.1）を先に、無ければ pwsh を探す。見つからなければ PATH は None で、PowerShell が要るテストは飛ばす。
CI では環境変数で必須にし、飛ばしたテストで通ったことにしない:

- DEV_COM_REQUIRE_POWERSHELL=1: 見つからなければ PowerShellError を投げる
- DEV_COM_EXPECT_POWERSHELL=5.1: 必須にしたうえで、見つけた PowerShell の版の Major.Minor が違うか、版を取れなければ投げる

投げるのは読み込みの時点なので、unittest はそのテストのファイルを読めなかった失敗として数え、skip より先に止まる。
"""

import os
import shutil
import subprocess
import sys


class PowerShellError(Exception):
    pass


def version(path):
    """PowerShell の $PSVersionTable.PSVersion（例: 5.1.26100.4061）。起動できないか、版が出なければ投げる。"""
    try:
        r = subprocess.run([path, "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise PowerShellError("%s を起動できない: %s" % (path, e))
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        raise PowerShellError("%s の版を取れない（終了コード %d）: %s" % (path, r.returncode, (r.stderr or out).strip()[:200]))
    return out


def find(env=None, which=shutil.which, get_version=version):
    """(PowerShell の絶対パス, 版)。必須でなく見つからなければ (None, None)、版を求められていなければ版は None。"""
    env = os.environ if env is None else env
    expect = env.get("DEV_COM_EXPECT_POWERSHELL", "").strip()
    required = env.get("DEV_COM_REQUIRE_POWERSHELL") == "1" or bool(expect)
    found = which("powershell") or which("pwsh")
    if not found:
        if required:
            raise PowerShellError("PowerShell（powershell か pwsh）が見つからない。DEV_COM_REQUIRE_POWERSHELL か "
                                  "DEV_COM_EXPECT_POWERSHELL で必須にしている")
        return None, None
    path = os.path.abspath(found)
    if not expect:
        return path, None
    v = get_version(path)
    if ".".join(v.split(".")[:2]) != expect:
        raise PowerShellError("PowerShell の版が違う: %s は %s（DEV_COM_EXPECT_POWERSHELL は %s）" % (path, v, expect))
    return path, v


PATH, VERSION = find()
if os.environ.get("DEV_COM_REQUIRE_POWERSHELL") == "1" or os.environ.get("DEV_COM_EXPECT_POWERSHELL"):
    print("テストが使う PowerShell: %s（版 %s）" % (PATH, VERSION or "確かめていない"), file=sys.stderr)
