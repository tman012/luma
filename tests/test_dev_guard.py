import os
import unittest
from unittest.mock import patch

import requests

from app import dev_guard


class TestDevGuard(unittest.TestCase):
    def setUp(self):
        self._orig = requests.sessions.Session.request
        dev_guard._installed = False

    def tearDown(self):
        requests.sessions.Session.request = self._orig
        dev_guard._installed = False

    def _install(self, urls):
        with patch.dict(os.environ, {"LUMA_READ_ONLY_URLS": urls}):
            dev_guard.install()

    def test_unset_is_noop(self):
        with patch.dict(os.environ, {"LUMA_READ_ONLY_URLS": ""}):
            dev_guard.install()
        self.assertIs(requests.sessions.Session.request, self._orig)

    def test_blocks_writes_to_listed_upstream(self):
        self._install("http://192.0.2.5:7878,192.0.2.5:8989")
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            r = requests.request(method, "http://192.0.2.5:7878/api/v3/movie", json={})
            self.assertEqual(r.status_code, 403, method)
        r = requests.post("http://192.0.2.5:8989/api/v3/series", json={})
        self.assertEqual(r.status_code, 403)

    def test_allows_reads_and_other_hosts(self):
        calls = []

        def fake(self_, method, url, *a, **kw):
            calls.append((method, url))
            return "passed"

        requests.sessions.Session.request = fake
        self._install("http://192.0.2.5:7878")
        self.assertEqual(requests.get("http://192.0.2.5:7878/api/v3/movie"), "passed")
        self.assertEqual(requests.post("http://192.0.2.6:7878/api/v3/movie"), "passed")
        self.assertEqual(requests.post("http://192.0.2.5:9999/x"), "passed")
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
