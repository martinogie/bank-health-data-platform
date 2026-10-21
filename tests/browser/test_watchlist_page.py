"""Browser smoke tests for watchlist.html (Playwright, Chromium).

Serves the repo with a local static server and drives the real page against the committed
site/data/watchlist.json. Run:

    .venv\\Scripts\\python -m playwright install chromium     # once
    .venv\\Scripts\\python -m pytest tests/browser -m browser
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[2]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url_root():
    handler = functools.partial(_QuietHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def open_page(page: Page, url: str) -> None:
    page.goto(url)
    expect(page.locator("#q")).to_be_enabled(timeout=15_000)      # data loaded


def add_by_search(page: Page, query: str, cert: int) -> None:
    page.fill("#q", query)
    page.locator(f"#results button[data-cert='{cert}']").click()
    expect(page.locator(f"article.bank[data-cert='{cert}']")).to_be_visible()


def card_certs(page: Page) -> list[int]:
    return [int(c) for c in page.locator("article.bank").evaluate_all("els => els.map(e => e.dataset.cert)")]


def test_add_three_banks_and_list_survives_reload(page: Page, base_url_root):
    open_page(page, f"{base_url_root}/watchlist.html")
    add_by_search(page, "JPMorgan Chase", 628)
    add_by_search(page, "3511", 3511)                 # by CERT
    add_by_search(page, "Synchrony", 27314)
    assert sorted(card_certs(page)) == [628, 3511, 27314]
    assert "certs=" in page.url

    page.reload()
    expect(page.locator("#q")).to_be_enabled(timeout=15_000)
    assert sorted(card_certs(page)) == [628, 3511, 27314]
    expect(page.locator("#strip")).to_contain_text("3banks watched")

    page.goto(f"{base_url_root}/watchlist.html")       # no ?certs=: comes back from localStorage
    expect(page.locator("#q")).to_be_enabled(timeout=15_000)
    assert sorted(card_certs(page)) == [628, 3511, 27314]


def test_shared_link_renders_in_a_fresh_browser(browser, base_url_root):
    context = browser.new_context()                    # empty localStorage
    page = context.new_page()
    open_page(page, f"{base_url_root}/watchlist.html?certs=628,3511")
    assert sorted(card_certs(page)) == [628, 3511]
    card = page.locator("article.bank[data-cert='628']")
    expect(card).to_contain_text("JPMorgan Chase Bank")
    expect(card.locator(".badge")).to_have_count(1)
    expect(card.locator("ol.reasons li")).to_have_count(3)
    expect(card.locator(".spark svg")).to_have_count(5)
    expect(card).to_contain_text("What changed since last quarter")
    expect(page.locator("#foot")).to_contain_text("model v")
    context.close()


def test_cards_sorted_by_risk(page: Page, base_url_root):
    open_page(page, f"{base_url_root}/watchlist.html?certs=628,59362,27314")
    order = {"D": 0, "H": 1, "E": 2, "W": 3, "L": 4}
    bands = page.locator("article.bank .score .badge").evaluate_all("els => els.map(e => e.classList[1])")
    assert [order[b] for b in bands] == sorted(order[b] for b in bands)


def test_new_bank_label_shown(page: Page, base_url_root):
    open_page(page, f"{base_url_root}/watchlist.html?certs=59362")   # Solutions Plus Bank, opened 2025
    expect(page.locator("article.bank .newbank")).to_contain_text("New bank: ratios distorted by start-up costs")


def test_unknown_certs_are_reported(page: Page, base_url_root):
    open_page(page, f"{base_url_root}/watchlist.html?certs=628,999999999")
    assert card_certs(page) == [628]
    expect(page.locator("#loadmsg")).to_contain_text("999999999")


def test_page_works_when_localstorage_is_blocked(browser, base_url_root):
    context = browser.new_context()
    context.add_init_script("""Object.defineProperty(window, 'localStorage',
        { get() { throw new DOMException('blocked', 'SecurityError'); } });""")
    page = context.new_page()
    open_page(page, f"{base_url_root}/watchlist.html?certs=628")
    add_by_search(page, "3511", 3511)
    assert sorted(card_certs(page)) == [628, 3511]
    assert "certs=628,3511" in page.url               # the URL still carries the list
    context.close()


def worst_case_certs() -> list[int]:
    """Banks with the widest content in the current watchlist.json: longest reasons, most
    complaints (long complaint row), biggest quarter-on-quarter moves (long change values)."""
    data = json.loads((ROOT / "site" / "data" / "watchlist.json").read_text(encoding="utf-8"))
    banks = data["banks"]

    def move(b):
        return max(abs((v[-1] or 0) - (v[-2] or 0)) for v in b["h"].values())

    picks = (sorted(banks, key=lambda b: -max([len(r) for r in b["r"]] or [0]))[:6]
             + sorted([b for b in banks if "cx" in b], key=lambda b: -b["cx"])[:3]
             + sorted(banks, key=move, reverse=True)[:3]
             + sorted(banks, key=lambda b: -len(b["n"]))[:2])
    certs = []
    for b in picks:
        if b["c"] not in certs:
            certs.append(b["c"])
    return certs


def test_no_horizontal_scroll_at_phone_width(browser, base_url_root):
    context = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2)
    page = context.new_page()
    certs = worst_case_certs()
    open_page(page, f"{base_url_root}/watchlist.html?certs=" + ",".join(map(str, certs)))
    expect(page.locator("article.bank")).to_have_count(len(certs))
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 375, f"page is {width}px wide at a 375px viewport"
    wide = page.evaluate("""() => [...document.querySelectorAll('article.bank *')]
        .filter(e => e.getBoundingClientRect().right > 375 + 0.5).length""")
    assert wide == 0, f"{wide} elements inside cards extend past the screen"
    context.close()
