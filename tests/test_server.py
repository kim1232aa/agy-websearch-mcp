"""Offline regression tests for server.py (no network, no credentials needed).

Run: python3 -m unittest discover -s tests -v
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server  # noqa: E402


GOOGLE_FIXTURE = {
    "response": {
        "candidates": [{
            "content": {"parts": [{"text": "Python 3.13 was released in October 2024."}]},
            "groundingMetadata": {
                "webSearchQueries": ["python 3.13 release date"],
                "groundingChunks": [
                    {"web": {"uri": "https://example.org/a", "title": "example.org"}},
                    {"web": {"uri": "https://example.com/b", "title": "example.com"}},
                    {"web": {"uri": "https://example.org/a", "title": "dup"}},
                    {"web": {"uri": "javascript:alert(1)", "title": "bad"}},
                    {"retrievedContext": {"uri": "gs://x"}},
                ],
                "groundingSupports": [
                    {"segment": {"text": "Python 3.13 was released"}, "groundingChunkIndices": [1]},
                    {"segment": {"text": "in October 2024."}, "groundingChunkIndices": [1, 0]},
                ],
            },
        }]
    }
}


def fake_engine(query, domains=(), max_results=10):
    items = [server.make_item(f"t{i}", f"https://example.com/{i}", f"c{i}", 1 - i / 100) for i in range(max_results)]
    return server.make_result("google", query, f"answer for {query}", items, [query])


def failing_engine(query, domains=(), max_results=10):
    raise server.SearchError("upstream exploded", "upstream")


class NormalizationTests(unittest.TestCase):
    def test_query(self):
        self.assertEqual(server.normalize_query("  hi "), "hi")
        for bad in (None, "", "   ", 5, ["x"], "x" * (server.MAX_QUERY_LEN + 1)):
            with self.assertRaises(server.SearchError):
                server.normalize_query(bad)

    def test_domains(self):
        self.assertEqual(server.normalize_domains("https://www.Docs.Python.org/3/"), ["docs.python.org"])
        self.assertEqual(server.normalize_domains(["github.com", "github.com:443", "www.github.com"]), ["github.com"])
        self.assertEqual(server.normalize_domains(None), [])
        self.assertEqual(server.normalize_domains(""), [])
        for bad in (5, [1], "exa mple.com", "a_b.com", ["x.com"] * (server.MAX_DOMAINS + 1)):
            with self.assertRaises(server.SearchError):
                server.normalize_domains(bad)

    def test_max_results(self):
        self.assertEqual(server.normalize_max_results(None), server.DEFAULT_MAX_RESULTS)
        self.assertEqual(server.normalize_max_results("5"), 5)
        self.assertEqual(server.normalize_max_results(3.0), 3)
        for bad in (True, -1, 21, "x", 2.5):
            with self.assertRaises(server.SearchError):
                server.normalize_max_results(bad)

    def test_engine(self):
        self.assertEqual(server.normalize_engine(None), "google")
        self.assertEqual(server.normalize_engine(" GROK "), "grok")
        for bad in (5, "nope"):
            with self.assertRaises(server.SearchError):
                server.normalize_engine(bad)
        with self.assertRaises(server.SearchError):
            server.normalize_engine("codex", allowed=("google",))


class GoogleParsingTests(unittest.TestCase):
    def test_parse(self):
        result = server._parse_google_response("q", GOOGLE_FIXTURE, 10)
        self.assertEqual(result["answer"], "Python 3.13 was released in October 2024.")
        self.assertEqual(result["search_queries"], ["python 3.13 release date"])
        urls = [r["url"] for r in result["results"]]
        self.assertEqual(urls, ["https://example.com/b", "https://example.org/a"])  # deduped, ranked, bad URL dropped
        self.assertGreater(result["results"][0]["score"], result["results"][1]["score"])
        self.assertIn("Python 3.13 was released", result["results"][0]["content"])

    def test_parse_limits_and_empty(self):
        self.assertEqual(len(server._parse_google_response("q", GOOGLE_FIXTURE, 1)["results"]), 1)
        empty = server._parse_google_response("q", {"response": {"candidates": []}}, 10)
        self.assertEqual(empty["results"], [])
        with self.assertRaises(server.SearchError):
            server._parse_google_response("q", {"error": "x"}, 10)

    def test_markdown(self):
        text = server.format_markdown(server._parse_google_response("q", GOOGLE_FIXTURE, 10))
        self.assertIn("### 权威溯源链接 (Sources)", text)
        self.assertIn("(https://example.com/b)", text)


class ResponsesParsingTests(unittest.TestCase):
    def test_parse(self):
        data = {"output": [
            {"type": "web_search_call", "action": {"query": "q1", "sources": [{"url": "https://a.example/x", "title": "A"}]}},
            {"type": "message", "content": [{"text": "hello", "annotations": [
                {"type": "url_citation", "url": "https://b.example/y", "title": "B"},
                {"type": "url_citation", "url": "https://a.example/x"},
                {"type": "url_citation", "url": "ftp://nope"},
            ]}]},
            "garbage",
        ]}
        result = server._responses_to_result("grok", "q", data, 10)
        self.assertEqual(result["answer"], "hello")
        self.assertEqual([r["url"] for r in result["results"]], ["https://a.example/x", "https://b.example/y"])
        self.assertEqual(result["search_queries"], ["q1"])


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self._engines = dict(server.ENGINES)
        server.ENGINES["google"] = fake_engine

    def tearDown(self):
        server.ENGINES.clear()
        server.ENGINES.update(self._engines)

    def rpc(self, payload):
        return server.handle_request(payload if isinstance(payload, str) else json.dumps(payload))

    def test_initialize_negotiates_version(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(res["result"]["protocolVersion"], "2024-11-05")
        res = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(res["result"]["protocolVersion"], server.DEFAULT_PROTOCOL_VERSION)

    def test_malformed_inputs_never_crash(self):
        self.assertEqual(self.rpc("{not json")["error"]["code"], -32700)
        self.assertEqual(self.rpc("[1,2]")["error"]["code"], -32600)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": "oops"})["error"]["code"], -32602)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "search_web", "arguments": [1]}})["error"]["code"], -32602)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                   "params": {"name": "search_web", "arguments": {"query": "x", "engine": 5}}})["error"]["code"], -32602)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                   "params": {"name": "nope", "arguments": {}}})["error"]["code"], -32602)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 6, "method": "nope"})["error"]["code"], -32601)
        self.assertIsNone(self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(self.rpc({"jsonrpc": "2.0", "method": "unknown/notification"}))

    def test_tools_list(self):
        tools = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["search_web"])
        props = tools[0]["inputSchema"]["properties"]
        self.assertEqual(props["engine"]["enum"], ["google", "grok", "codex"])
        self.assertIn("query", props)
        self.assertIn("domain", props)
        self.assertNotIn("toolAction", props)
        self.assertNotIn("toolSummary", props)
        self.assertNotIn("agy_web_search", [t["name"] for t in tools])

    def test_tools_call_success_and_upstream_error(self):
        res = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "search_web", "arguments": {"query": "hi", "max_results": 2}}})["result"]
        self.assertFalse(res["isError"])
        self.assertNotIn("structuredContent", res)
        self.assertIn("answer for hi", res["content"][0]["text"])

        # Unknown tool (agy_web_search alias removed)
        err = self.rpc({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                        "params": {"name": "agy_web_search", "arguments": {"query": "hi"}}})
        self.assertEqual(err["error"]["code"], -32602)

        server.ENGINES["google"] = failing_engine
        res = self.rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": "search_web", "arguments": {"query": "hi"}}})["result"]
        self.assertTrue(res["isError"])
        self.assertIn("upstream exploded", res["content"][0]["text"])


class CredentialTests(unittest.TestCase):
    def test_save_credentials_permissions_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "credentials.json")
            server.save_credentials({"refresh_token": "abc"}, path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["refresh_token"], "abc")
            if os.name == "posix":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                link = os.path.join(tmp, "link.json")
                os.symlink(os.path.join(tmp, "target.json"), link)
                with self.assertRaises(OSError):
                    server.save_credentials({"refresh_token": "x"}, link)
                self.assertFalse(os.path.exists(os.path.join(tmp, "target.json")))

    def test_cwd_is_not_searched(self):
        self.assertNotIn(os.path.abspath("credentials.json"),
                         [p for p in server.get_possible_cred_paths()
                          if os.path.dirname(p) != os.path.dirname(os.path.abspath(server.__file__))])


class PiAuthTests(unittest.TestCase):
    """grok/codex reuse pi's saved OAuth credentials read-only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "auth.json")
        self.old = os.environ.get("AGY_PI_AUTH_PATH")
        os.environ["AGY_PI_AUTH_PATH"] = self.path

    def tearDown(self):
        if self.old is None:
            os.environ.pop("AGY_PI_AUTH_PATH", None)
        else:
            os.environ["AGY_PI_AUTH_PATH"] = self.old
        self.tmp.cleanup()

    def write(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)

    def test_valid_expired_missing(self):
        import time as _t
        future = int((_t.time() + 3600) * 1000)
        past = int((_t.time() - 10) * 1000)
        self.write({"xai": {"type": "oauth", "access": "tok", "refresh": "r", "expires": future},
                    "openai-codex": {"type": "oauth", "access": "tok2", "refresh": "r", "expires": past}})
        before = open(self.path).read()
        self.assertEqual(server.pi_access_token("xai")["access"], "tok")
        with self.assertRaises(server.SearchError) as ctx:
            server.pi_access_token("openai-codex")
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertIsNone(server.pi_access_token("anthropic"))
        self.assertEqual(open(self.path).read(), before)  # never written

    def test_missing_file(self):
        self.assertIsNone(server.pi_access_token("xai"))

    def test_strip_tracking(self):
        data = {"output": [{"type": "message", "content": [{"type": "output_text",
                "text": "See ([a.org](https://a.org/x?utm_source=openai))",
                "annotations": [{"type": "url_citation", "url": "https://a.org/x?utm_source=openai", "title": "A"},
                                {"type": "url_citation", "url": "https://a.org/x", "title": "A2"},
                                {"type": "url_citation", "url": "https://b.org/?q=1&utm_source=openai", "title": "B"}]}]}]}
        result = server._responses_to_result("codex", "q", data, 10)
        self.assertEqual([r["url"] for r in result["results"]], ["https://a.org/x", "https://b.org/?q=1"])
        self.assertNotIn("utm_source", result["answer"])


class HttpApiTests(unittest.TestCase):
    TOKEN = "test-token-0123456789abcdef"

    @classmethod
    def setUpClass(cls):
        cls._engines = dict(server.ENGINES)
        server.ENGINES["google"] = fake_engine
        settings = server.HttpSettings(tokens=[cls.TOKEN], engines=["google"], rate_limit_per_minute=5, max_concurrency=2)
        cls.httpd = server.build_http_server("127.0.0.1", 0, settings)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.ENGINES.clear()
        server.ENGINES.update(cls._engines)

    def setUp(self):
        self.httpd.limiter = server.RateLimiter(5)

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), (json.loads(payload) if payload else None)

    def auth(self):
        return {"Authorization": f"Bearer {self.TOKEN}"}

    def test_health_no_auth(self):
        status, _, body = self.request("GET", "/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))

    def test_auth_required(self):
        self.assertEqual(self.request("POST", "/search", {"query": "x"})[0], 401)
        self.assertEqual(self.request("POST", "/search", {"query": "x"}, {"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request("POST", "/search", {"query": "x"}, {"X-API-Key": self.TOKEN})[0], 200)

    def test_tavily_shape(self):
        status, headers, body = self.request("POST", "/search",
                                             {"query": "hello", "max_results": 3, "include_answer": True}, self.auth())
        self.assertEqual(status, 200)
        for key in ("query", "answer", "images", "results", "response_time", "request_id"):
            self.assertIn(key, body)
        self.assertEqual(len(body["results"]), 3)
        self.assertEqual(set(body["results"][0]), {"title", "url", "content", "score", "raw_content"})
        self.assertEqual(body["answer"], "answer for hello")
        self.assertIn("X-Request-Id", headers)
        _, _, body = self.request("POST", "/search", {"query": "hello"}, self.auth())
        self.assertIsNone(body["answer"])

    def test_validation_errors(self):
        self.httpd.limiter = server.RateLimiter(100)
        cases = [
            ({"query": ""}, 400),
            ({"query": "x", "max_results": 99}, 400),
            ({"query": "x", "include_domains": "bad domain"}, 400),
            ({"query": "x", "engine": "codex"}, 400),  # not enabled on this server
        ]
        for body, expected in cases:
            status, _, payload = self.request("POST", "/search", body, self.auth())
            self.assertEqual(status, expected, body)
            self.assertIn("error", payload["detail"])
        self.assertEqual(self.request("POST", "/search", raw=b"{nope", headers=self.auth())[0], 400)
        self.assertEqual(self.request("POST", "/search", raw=b"[1]", headers=self.auth())[0], 400)
        self.assertEqual(self.request("POST", "/search", {"query": "x"},
                                      dict(self.auth(), **{"Content-Type": "text/plain"}))[0], 415)
        self.assertEqual(self.request("POST", "/search", {"query": "x" * 20000}, self.auth())[0], 413)
        self.assertEqual(self.request("POST", "/nope", {"query": "x"}, self.auth())[0], 404)
        self.assertEqual(self.request("GET", "/search")[0], 405)

    def test_upstream_error_maps_to_502(self):
        server.ENGINES["google"] = failing_engine
        try:
            status, _, body = self.request("POST", "/search", {"query": "x"}, self.auth())
        finally:
            server.ENGINES["google"] = fake_engine
        self.assertEqual(status, 502)
        self.assertIn("upstream exploded", body["detail"]["error"])

    def test_rate_limit(self):
        statuses = [self.request("POST", "/search", {"query": "x"}, self.auth())[0] for _ in range(7)]
        self.assertEqual(statuses[:5], [200] * 5)
        self.assertEqual(statuses[5], 429)

    def test_forwarded_for_ignored_without_trust_proxy(self):
        statuses = [self.request("POST", "/search", {"query": "x"},
                                 dict(self.auth(), **{"X-Forwarded-For": f"10.0.0.{i}"}))[0] for i in range(7)]
        self.assertIn(429, statuses)


class HttpTokenLoadingTests(unittest.TestCase):
    def test_short_token_rejected(self):
        old = os.environ.get("AGY_HTTP_TOKENS")
        os.environ["AGY_HTTP_TOKENS"] = "short"
        try:
            with self.assertRaises(SystemExit):
                server.load_http_tokens(None)
        finally:
            if old is None:
                os.environ.pop("AGY_HTTP_TOKENS", None)
            else:
                os.environ["AGY_HTTP_TOKENS"] = old


if __name__ == "__main__":
    unittest.main()
