"""Scraper pure helpers: HTML->text, link extraction, pagination detection."""
from __future__ import annotations

from uniagent.scraper import FetchResult, Fetcher, extract_links, html_to_text


def test_html_to_text_drops_scripts_and_keeps_text():
    html = "<html><head><style>.x{}</style></head><body><script>var x=1</script>" \
           "<p>Hello world</p></body></html>"
    text = html_to_text(html)
    assert "Hello world" in text
    assert "var x" not in text
    assert ".x{}" not in text


def test_html_to_text_flattens_tables_to_pipes():
    html = "<table><tr><th>Level</th><th>Fee</th></tr>" \
           "<tr><td>UG</td><td>50000</td></tr></table>"
    text = html_to_text(html)
    assert "Level | Fee" in text
    assert "UG | 50000" in text


def test_extract_links_resolves_relative_and_skips_mailto():
    html = '<a href="/about">About</a><a href="mailto:x@y.z">Mail</a>' \
           '<a href="https://ext.com/p">Ext</a>'
    links = extract_links(html, "https://uni.edu/home")
    urls = [u for _, u in links]
    assert "https://uni.edu/about" in urls
    assert "https://ext.com/p" in urls
    assert all("mailto" not in u for u in urls)


def test_find_next_link_prefers_rel_next():
    html = '<a rel="next" href="/page/2">More</a>'
    res = FetchResult(url="https://uni.edu/page/1", ok=True, html=html)
    assert Fetcher._find_next_link(res) == "https://uni.edu/page/2"


def test_find_next_link_falls_back_to_text():
    html = '<a href="/p/2">Next</a>'
    res = FetchResult(url="https://uni.edu/p/1", ok=True, html=html)
    assert Fetcher._find_next_link(res) == "https://uni.edu/p/2"


def test_find_next_link_none_when_absent():
    res = FetchResult(url="https://uni.edu/p/1", ok=True, html="<p>end</p>")
    assert Fetcher._find_next_link(res) is None
