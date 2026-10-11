"""List-page parsing: listings + state detection. Pure functions, no network.

DOM facts (verified 2026-10-11):
  item       = a.standard-list-item[data-item-id]   (title/price duplicated in
               .collapsed-info and .extended-info — take extended first)
  price      = .extended-info .price-line > .price  (fallback .collapsed-info .price-item)
  change     = .price-change > span.down|up  + sibling text "37,8%"
  sold       = .top-right .item.sold  → <b>Parduota</b> per 5 d. | 4 val. | 30 min.
  reserved   = .top-right .item.reserved-icon
  params     = .item-params .param  → "Būklė: Nauja"
  pagination = a.pagination_link[rel=next]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from scrapling.parser import Selector

from specs import _clean

SEL_ITEM = "a.standard-list-item[data-item-id]"
SEL_NEXT = 'a.pagination_link[rel="next"]'

_TIME_UNITS = {
    "min": 1,
    "val": 60,
    "h": 60,
    "d": 1440,
    "d.": 1440,
    "w": 10080,
}


@dataclass
class Listing:
    item_id: str
    url: str
    title: str
    category: str
    price_eur: float | None
    state: str  # active | sold | reserved
    time_to_sell_min: int | None
    price_change_pct: float | None
    location: str
    condition: str | None
    raw_price_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    # "1 700 €" | "280 €" | "12€" | "1,50 €"
    m = re.search(r"([\d\s]+(?:[.,]\d+)?)\s*€", text)
    if not m:
        return None
    num = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def _parse_time_to_sell(badge_text: str) -> int | None:
    """'per 5 d.' → days, 'per 4 val.' → hours, 'per 30 min.' → minutes. Returns minutes."""
    m = re.search(r"per\s+(\d+)\s*(min|val|h|d|w)", badge_text, re.I)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * _TIME_UNITS.get(unit, 1)


def _parse_change_pct(item) -> float | None:
    ch = item.css(".price-change")
    if not ch:
        return None
    span = ch[0].css("span")
    direction = None
    if span:
        classes = span[0].attrib.get("class", "")
        if "down" in classes:
            direction = -1.0
        elif "up" in classes:
            direction = 1.0
    text = ch[0].get_all_text() or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not m:
        return None
    pct = float(m.group(1).replace(",", "."))
    return pct if direction is None else pct * direction


def _first_text(item, *selectors: str) -> str:
    for sel in selectors:
        els = item.css(sel)
        if els and els[0].text and els[0].text.strip():
            return els[0].text.strip()
    return ""


def parse_item(item, category: str, page_url: str) -> Listing | None:
    item_id = item.attrib.get("data-item-id", "").strip()
    if not item_id:
        return None
    href = item.attrib.get("href", "").strip()
    url = urljoin(page_url, href) if href else page_url

    title = _first_text(
        item, ".extended-info .title", ".collapsed-info .title", ".title"
    )

    price_text = _first_text(
        item,
        ".extended-info .price-line .price",
        ".extended-info .price-item .price",
        ".collapsed-info .price-item .price",
        ".price",
    )
    price = _parse_price(price_text)

    # state badges live in .top-right of extended block
    state = "active"
    time_to_sell = None
    sold = item.css(".extended-info .top-right .item.sold") or item.css(".item.sold")
    reserved = item.css(".extended-info .top-right .item.reserved-icon") or item.css(
        ".item.reserved-icon"
    )
    if sold:
        state = "sold"
        # full text: <b>Parduota</b> per 5 d. — get_all_text returns a string
        badge = re.sub(r"\s+", " ", sold[0].get_all_text(strip=True)).strip()
        time_to_sell = _parse_time_to_sell(badge)
    elif reserved:
        state = "reserved"

    change = _parse_change_pct(item)
    location = _first_text(item, ".extended-info .second-dataline", ".collapsed-info .second-line")

    condition = None
    for p in item.css(".item-params .param"):
        t = (p.text or "").strip()
        if _clean(t).lower().startswith("bukle"):
            condition = t.split(":", 1)[-1].strip() or None
            break

    if not title:
        return None

    return Listing(
        item_id=item_id,
        url=url,
        title=title,
        category=category,
        price_eur=price,
        state=state,
        time_to_sell_min=time_to_sell,
        price_change_pct=change,
        location=location,
        condition=condition,
        raw_price_text=price_text or None,
    )


def parse_list_page(html: str, page_url: str, category: str) -> tuple[list[Listing], str | None]:
    """Return (listings, next_page_url). Dedupes collapsed/extended duplicates by
    taking extended-first selectors (single item node per listing)."""
    page = Selector(html)
    seen: set[str] = set()
    out: list[Listing] = []
    for item in page.css(SEL_ITEM):
        listing = parse_item(item, category, page_url)
        if listing is None or listing.item_id in seen:
            continue
        seen.add(listing.item_id)
        out.append(listing)

    next_url = None
    nxt = page.css(SEL_NEXT)
    if nxt:
        href = nxt[0].attrib.get("href", "")
        if href:
            next_url = urljoin(page_url, href)
    return out, next_url


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
