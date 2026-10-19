"""ado.ps1 を手元の偽サーバーに向けて流す。PowerShell（powershell か pwsh）が無ければ飛ばす。

会社の Windows で `python -m unittest discover -s tests` を流すと、Windows PowerShell 5.1 で検査される。
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "ado", "scripts", "ado.ps1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import powershell  # noqa: E402

POWERSHELL = powershell.PATH


class Handler(BaseHTTPRequestHandler):
    requests = []

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        Handler.requests.append({
            "method": self.command,
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
            "body": body,
        })
        if self.path.startswith("/tfs/Coll/missing"):
            self.send_response(404)
            payload = b'{"message":"not found"}'
        elif self.path.startswith("/tfs/Coll/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://elsewhere.example/login")
            payload = b""
        else:
            self.send_response(200)
            self.send_header("x-ms-continuationtoken", "next-page")
            payload = json.dumps({"value": ["日本語"]}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = _handle

    def log_message(self, *args):
        pass


@unittest.skipUnless(POWERSHELL, "PowerShell が無い")
class AdoPs1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d/tfs/Coll" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Handler.requests = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def file(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def run_ps(self, *args, env=None):
        e = dict(os.environ, ADO_COLLECTION_URL=self.base, ADO_AUTH="default")
        e.pop("ADO_PAT", None)
        e.update(env or {})
        cmd = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT] + list(args)
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e, timeout=60)
        out = p.stdout.decode("utf-8", errors="replace").strip()
        result = json.loads(out.splitlines()[-1]) if out else None
        return p.returncode, result, p.stderr.decode("utf-8", errors="replace")

    def test_get_adds_api_version_and_saves_body(self):
        out = os.path.join(self.tmp.name, "r.json")
        code, result, _ = self.run_ps("get", "P/_apis/wit/workitems/1?$expand=relations", "-OutFile", out)
        self.assertEqual(code, 0)
        self.assertEqual(Handler.requests[0]["path"], "/tfs/Coll/P/_apis/wit/workitems/1?$expand=relations&api-version=6.0")
        self.assertEqual(result["continuationToken"], "next-page")
        with open(out, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"value": ["日本語"]})

    def test_continuation_token_is_appended(self):
        self.run_ps("get", "_apis/projects", "-ContinuationToken", "a b")
        self.assertEqual(Handler.requests[0]["path"], "/tfs/Coll/_apis/projects?api-version=6.0&continuationToken=a%20b")

    def test_query_only_read_endpoints(self):
        body = self.file("q.json", '{"query": "SELECT [System.Id] FROM WorkItems"}')
        code, _, _ = self.run_ps("query", "P/_apis/wit/wiql", "-BodyFile", body)
        self.assertEqual(code, 0)
        self.assertEqual(Handler.requests[0]["method"], "POST")
        code, _, err = self.run_ps("query", "P/_apis/wit/workitems/1", "-BodyFile", body)
        self.assertEqual(code, 2, err)
        self.assertEqual(len(Handler.requests), 1)

    def test_preview_requires_boolean_true(self):
        for text in ('{"previewRun": "true"}', '{"previewRun": false}', '{}'):
            code, _, _ = self.run_ps("preview", "P/_apis/pipelines/7/runs", "-BodyFile", self.file("b.json", text))
            self.assertEqual(code, 2, text)
        self.assertEqual(Handler.requests, [])
        code, _, _ = self.run_ps("preview", "P/_apis/pipelines/7/runs", "-BodyFile", self.file("b.json", '{"previewRun": true}'))
        self.assertEqual(code, 0)
        self.assertTrue(Handler.requests[0]["path"].endswith("api-version=6.0-preview.1"))

    def test_write_needs_matching_approval(self):
        patch = '[{"op":"test","path":"/rev","value":3},{"op":"add","path":"/fields/System.Title","value":"題名"}]'
        body = self.file("p.json", patch)
        args = ["write", "P/_apis/wit/workitems/1", "-Method", "PATCH", "-BodyFile", body]
        code, _, _ = self.run_ps(*args)
        self.assertEqual(code, 2)
        code, dry, _ = self.run_ps(*(args + ["-DryRun"]))
        self.assertEqual(code, 0)
        self.assertEqual(Handler.requests, [])
        code, _, _ = self.run_ps(*(args + ["-Approve", "0" * 64]))
        self.assertEqual(code, 2)
        self.assertEqual(Handler.requests, [])
        code, result, _ = self.run_ps(*(args + ["-Approve", dry["approval"]]))
        self.assertEqual(code, 0)
        sent = Handler.requests[0]
        self.assertEqual(sent["method"], "PATCH")
        self.assertTrue(sent["content_type"].startswith("application/json-patch+json"))
        self.assertEqual(sent["body"], patch.encode("utf-8"))

    def test_approval_is_bound_to_the_body(self):
        body = self.file("p.json", '[{"op":"add","path":"/fields/System.Title","value":"A"}]')
        args = ["write", "P/_apis/wit/workitems/1", "-Method", "PATCH", "-BodyFile", body]
        _, dry, _ = self.run_ps(*(args + ["-DryRun"]))
        self.file("p.json", '[{"op":"add","path":"/fields/System.Title","value":"B"}]')
        code, _, _ = self.run_ps(*(args + ["-Approve", dry["approval"]]))
        self.assertEqual(code, 2)
        self.assertEqual(Handler.requests, [])

    def test_absolute_url_outside_collection_is_refused(self):
        code, _, _ = self.run_ps("get", "http://127.0.0.1:1/other/_apis/projects")
        self.assertEqual(code, 2)
        code, _, _ = self.run_ps("get", self.base + "evil/_apis/projects")
        self.assertEqual(code, 2)
        self.assertEqual(Handler.requests, [])

    def test_dot_segments_cannot_leave_the_collection(self):
        for path in ("../Other/_apis/projects", self.base + "/../Other/_apis/projects", "P/%2e%2e/%2e%2e/x"):
            code, _, _ = self.run_ps("get", path)
            self.assertEqual(code, 2, path)
        self.assertEqual(Handler.requests, [])

    def test_comment_is_plain_json(self):
        body = self.file("c.json", '{"text": "<p>a</p>"}')
        args = ["write", "P/_apis/wit/workItems/1/comments", "-Method", "POST", "-BodyFile", body, "-ApiVersion", "6.0-preview.3"]
        _, dry, _ = self.run_ps(*(args + ["-DryRun"]))
        self.assertEqual(dry["contentType"], "application/json")
        _, dry, _ = self.run_ps("write", "P/_apis/wit/workitems/$Task", "-Method", "POST", "-BodyFile", self.file("t.json", "[]"), "-DryRun")
        self.assertEqual(dry["contentType"], "application/json-patch+json")

    def test_unwritable_out_file_is_refused_before_sending(self):
        body = self.file("p.json", "[]")
        out = os.path.join(self.tmp.name, "missing-dir", "r.json")
        args = ["write", "P/_apis/wit/workitems/1", "-Method", "PATCH", "-BodyFile", body, "-OutFile", out]
        _, dry, _ = self.run_ps(*(args + ["-DryRun"]))
        code, _, _ = self.run_ps(*(args + ["-Approve", dry["approval"]]))
        self.assertEqual(code, 2)
        self.assertEqual(Handler.requests, [])

    def test_http_error_and_redirect(self):
        code, result, _ = self.run_ps("get", "missing")
        self.assertEqual((code, result["status"]), (1, 404))
        code, result, _ = self.run_ps("get", "redirect")
        self.assertEqual((code, result["status"]), (1, 302))
        self.assertEqual(result["location"], "http://elsewhere.example/login")
        self.assertEqual(len(Handler.requests), 2)

    def test_bom_body_is_refused(self):
        body = self.file("q.json", b'\xef\xbb\xbf{"query": "x"}')
        code, _, _ = self.run_ps("query", "P/_apis/wit/wiql", "-BodyFile", body)
        self.assertEqual(code, 2)

    def test_pat_is_not_sent_over_http(self):
        code, _, err = self.run_ps("get", "_apis/projects", env={"ADO_AUTH": "pat", "ADO_PAT": "secret-value"})
        self.assertEqual(code, 2)
        self.assertNotIn("secret-value", err)
        self.assertEqual(Handler.requests, [])


if __name__ == "__main__":
    unittest.main()
