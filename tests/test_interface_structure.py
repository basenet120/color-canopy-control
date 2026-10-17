"""Offline structural checks. Run with stdlib unittest; no Hubspace credentials or network."""

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

    def test_expected_app_pages_exist(self):
        for page in [
            "ops",
            "ops/module",
            "ops/accounts",
            "ops/bench",
            "ops/bench/clock",
            "ops/calibrate",
            "ops/showcase",
            "kiosk",
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

    def test_the_removed_pages_stay_removed(self):
        """D67: the vignette rooms and the Ubuntu box's Chromium resting page are gone."""
        for page in ["vignette", "kiosk-resting"]:
            with self.subTest(page=page):
                self.assertFalse((WEB / page).exists())


if __name__ == "__main__":
    unittest.main()
