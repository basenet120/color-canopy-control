"""Offline structural checks. Run with stdlib unittest; no Hubspace credentials or network."""

import json
import re
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids, self.assets, self.links = [], [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.assets.append(attrs["href"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


class InterfaceStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {p: Page(p.read_text(encoding="utf-8")) for p in WEB.rglob("*.html")}
        cls.cases = json.loads((ROOT / "docs/test-cases.json").read_text(encoding="utf-8"))

    def test_expected_app_pages_exist(self):
        for page in [
            "ops",
            "ops/module",
            "ops/accounts",
            "ops/bench",
            "ops/bench/clock",
            "ops/calibrate",
            "kiosk",
            "kiosk-resting",
            "vignette",
            "visualizer",
        ]:
            with self.subTest(page=page):
                self.assertIn(WEB / page / "index.html", self.pages)

    def test_html_ids_are_unique(self):
        for path, page in self.pages.items():
            with self.subTest(page=str(path.relative_to(ROOT))):
                self.assertEqual([], [key for key, count in Counter(page.ids).items() if count > 1])

    def test_local_scripts_and_stylesheets_resolve(self):
        for path, page in self.pages.items():
            for asset in page.assets:
                url = urlsplit(asset)
                if url.scheme or url.netloc or not url.path:
                    continue
                local = (
                    (WEB / unquote(url.path).lstrip("/"))
                    if url.path.startswith("/")
                    else (path.parent / unquote(url.path))
                )
                with self.subTest(page=str(path.relative_to(ROOT)), asset=asset):
                    self.assertTrue(local.resolve().is_relative_to(WEB.resolve()))
                    self.assertTrue(local.is_file(), f"Missing asset: {local}")

    def test_inventory_has_unique_complete_cases(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 88)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertRegex(case["id"], r"^[A-Z]+-\d+$")
                for key in ["title", "expected", "category", "stage", "source"]:
                    self.assertTrue(case[key].strip())
                self.assertTrue(case["path"].startswith("/"))

    def test_workflow_and_inventory_stay_in_sync(self):
        workflow = (ROOT / "docs/interface-troubleshooting-workflow.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| ([A-Z]+-\d+) \| (.*?) \| (.*?) \|$", workflow, re.MULTILINE)
        actual = {case["id"]: (case["title"], case["expected"]) for case in self.cases}
        self.assertEqual({case_id: (title, expected) for case_id, title, expected in rows}, actual)

    def test_tracker_embeds_current_inventory_and_resolves_docs(self):
        source = (ROOT / "TEST-CENTER.html").read_text(encoding="utf-8")
        embedded = re.search(
            r'<script id="case-data" type="application/json">(.*?)</script>', source, re.DOTALL
        )
        self.assertIsNotNone(embedded)
        self.assertEqual(json.loads(embedded.group(1)), self.cases)
        page = Page(source)
        self.assertEqual(len(page.ids), len(set(page.ids)))
        for link in page.links:
            url = urlsplit(link)
            if not url.scheme and not url.netloc and url.path:
                self.assertTrue((ROOT / unquote(url.path)).is_file(), f"Missing tracker link: {link}")


if __name__ == "__main__":
    unittest.main()
