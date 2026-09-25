"""Test dell'aggiornamento automatico contro un finto GitHub in locale."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nwn_voce import updater  # noqa: E402

INSTALLER = b"MZ finto installer NWN Voce" * 1000
SHA = hashlib.sha256(INSTALLER).hexdigest()


class FakeGitHub:
    """Serve /latest (JSON della Release) e /dl/<file> (l'installer)."""

    def __init__(self):
        self.routes = {}
        routes = self.routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status, headers, body = routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def release(self, tag="v9.9.9", body="Novità.", digest=f"sha256:{SHA}", **extra):
        rel = {"tag_name": tag, "body": body, "html_url": self.base + "/page",
               "draft": False, "prerelease": False,
               "assets": [{"name": f"NWN-Voce-Setup-{tag.lstrip('v')}.exe",
                           "browser_download_url": f"{self.base}/dl/NWN-Voce-Setup-{tag.lstrip('v')}.exe",
                           "size": len(INSTALLER), "digest": digest}]}
        rel.update(extra)
        self.routes["/latest"] = (200, {"Content-Type": "application/json"}, json.dumps(rel).encode())
        self.routes[f"/dl/NWN-Voce-Setup-{tag.lstrip('v')}.exe"] = (200, {}, INSTALLER)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


LOCAL = {"allowed_hosts": {"127.0.0.1"}, "schemes": ("http",)}


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.api = self.gh.base + "/latest"

    def tearDown(self):
        self.gh.close()

    def test_versions(self):
        self.assertTrue(updater.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(updater.is_newer("1.2.0", "1.2"))
        self.assertEqual(updater.parse_version("v1.2"), (1, 2, 0, 0))

    def test_new_version_found_and_mandatory(self):
        self.gh.release(body="Corretto l'audio.\nVersione minima: 9.0.0\n")
        upd = updater.check("1.2.0", api_url=self.api, **LOCAL)
        self.assertIsNotNone(upd)
        self.assertEqual(upd.version, "9.9.9")
        self.assertTrue(upd.mandatory)
        self.assertEqual(upd.notes, "Corretto l'audio.")      # la riga tecnica non si mostra
        self.assertEqual(upd.sha256, SHA)

    def test_optional_when_above_minimum(self):
        self.gh.release(body="Versione minima: 1.0.0")
        self.assertFalse(updater.check("1.2.0", api_url=self.api, **LOCAL).mandatory)

    def test_nothing_when_up_to_date(self):
        self.gh.release(tag="v1.2.0")
        self.assertIsNone(updater.check("1.2.0", api_url=self.api, **LOCAL))

    def test_ignores_prerelease_and_missing_digest(self):
        self.gh.release(prerelease=True)
        self.assertIsNone(updater.check("1.2.0", api_url=self.api, **LOCAL))
        self.gh.release(digest=None)
        self.assertIsNone(updater.check("1.2.0", api_url=self.api, **LOCAL))

    def test_offline_is_silent(self):
        self.assertIsNone(updater.check("1.2.0", api_url="http://127.0.0.1:1/latest", **LOCAL))

    def test_download_verified(self):
        self.gh.release()
        upd = updater.check("1.2.0", api_url=self.api, **LOCAL)
        seen = []
        with tempfile.TemporaryDirectory() as d:
            path = updater.download(upd, d, progress=lambda a, b: seen.append((a, b)), **LOCAL)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), INSTALLER)
        self.assertEqual(seen[-1], (len(INSTALLER), len(INSTALLER)))

    def test_tampered_file_is_discarded(self):
        self.gh.release(digest="sha256:" + "0" * 64)
        upd = updater.check("1.2.0", api_url=self.api, **LOCAL)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(updater.UpdateError):
                updater.download(upd, d, **LOCAL)
            self.assertEqual(os.listdir(d), [], "niente file scaricati lasciati in giro")

    def test_only_allowed_hosts(self):
        self.gh.release()
        upd = updater.check("1.2.0", api_url=self.api, **LOCAL)
        with tempfile.TemporaryDirectory() as d, self.assertRaises(updater.UpdateError):
            updater.download(upd, d, allowed_hosts={"github.com"}, schemes=("https",))

    def test_redirect_to_foreign_host_is_blocked(self):
        self.gh.release()
        upd = updater.check("1.2.0", api_url=self.api, **LOCAL)
        # il download rimbalza su "localhost": stesso PC, ma NON e' un dominio consentito
        self.gh.routes[f"/dl/NWN-Voce-Setup-9.9.9.exe"] = (
            302, {"Location": f"http://localhost:{self.gh.port}/altro"}, b"")
        self.gh.routes["/altro"] = (200, {}, INSTALLER)
        with tempfile.TemporaryDirectory() as d, self.assertRaises(updater.UpdateError):
            updater.download(upd, d, **LOCAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
