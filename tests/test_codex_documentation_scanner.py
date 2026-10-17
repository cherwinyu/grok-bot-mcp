"""Exact official documentation hosts are reviewed; lookalikes remain blocked."""
from __future__ import annotations

import unittest

from scripts.scan_release import _inspect, ALLOWED_URL_HOSTS


class DocumentationHostBoundaryTest(unittest.TestCase):
    def test_referenced_official_docs_are_exactly_allowlisted(self):
        official = ("https://learn.chatgpt.com/docs/extend/mcp",
                    "https://docs.cursor.com/context/model-context-protocol")
        for url in official:
            with self.subTest(url=url):
                self.assertEqual(_inspect("docs/client-compatibility.md", url), [])
        self.assertIn("learn.chatgpt.com", ALLOWED_URL_HOSTS)
        self.assertIn("docs.cursor.com", ALLOWED_URL_HOSTS)

    def test_host_suffix_and_lookalikes_remain_rejected(self):
        # Construct synthetic negative fixtures at runtime, never ship an actual
        # unauthorized endpoint URL as a packaged static documentation link.
        prefix = "https:"
        for host in ("learn.chatgpt.com.evil.invalid", "docs.cursor.com.evil.invalid",
                     "notlearn.chatgpt.com", "notdocs.cursor.com"):
            url = prefix + "//" + host + "/fictional"
            with self.subTest(host=host):
                self.assertTrue(any(x.startswith("url ") for x in
                                    _inspect("docs/client-compatibility.md", url)))
