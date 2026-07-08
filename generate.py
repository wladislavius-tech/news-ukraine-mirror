# -*- coding: utf-8 -*-
"""
Генератор SEO-дзеркала каналу — хмарна версія (GitHub Actions).

Читає публічну сторінку t.me/s/<канал>, накопичує архів постів у
mirror_state.json і генерує статичний сайт (index.html, окремі сторінки
постів, rss.xml, sitemap.xml, robots.txt) прямо в корені цього репозиторію.
Коміт змін і пуш робить workflow; сам скрипт лише генерує файли й пінгує
IndexNow (миттєва індексація Bing/DuckDuckGo).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import secrets
import sys
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
STATE_FILE = BASE / "mirror_state.json"
MAX_ON_PAGE = 150
MAX_IN_RSS = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

CHANNEL = "News_Ukraine_world_war"
SITE_URL = "https://wladislavius-tech.github.io/news-ukraine-mirror/"
SITE_TITLE = "Новини України сьогодні — війна, події у світі"
SITE_DESC = ("Оперативні новини України та світу: війна, обстріли, політика, "
             "міжнародні події. Стрічка оновлюється цілодобово в Telegram-каналі.")

if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# --- Читання каналу ---
def fetch_channel_page(username: str) -> BeautifulSoup | None:
    try:
        r = requests.get(f"https://t.me/s/{username}", headers=HEADERS, timeout=25)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] {username}: {e}")
        return None
    return BeautifulSoup(r.text, "html.parser")


def parse_subscribers(soup: BeautifulSoup) -> str | None:
    for counter in soup.select(".tgme_channel_info_counter"):
        typ = counter.select_one(".counter_type")
        val = counter.select_one(".counter_value")
        if typ and val and "subscriber" in typ.get_text(strip=True).lower():
            return val.get_text(strip=True)
    return None


def parse_posts(soup: BeautifulSoup, username: str) -> list[dict]:
    posts = []
    for msg in soup.select(".tgme_widget_message"):
        m = re.match(rf"{re.escape(username)}/(\d+)", msg.get("data-post", ""), re.IGNORECASE)
        if not m:
            continue
        text_el = msg.select_one(".tgme_widget_message_text")
        text = text_el.get_text(" ", strip=True) if text_el else ""
        time_el = msg.select_one("time[datetime]")
        posted_at = None
        if time_el and time_el.get("datetime"):
            try:
                posted_at = dt.datetime.fromisoformat(time_el["datetime"])
            except ValueError:
                pass
        posts.append({"id": int(m.group(1)), "text": text, "posted_at": posted_at})
    return posts


# --- Стан ---
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"posts": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# --- Рендер ---
def render_index(posts: list[dict], subs: str | None) -> str:
    channel_url = f"https://t.me/{CHANNEL}"
    items = []
    for p in posts[:MAX_ON_PAGE]:
        text = html.escape(p["text"])
        items.append(f"""
  <article>
    <a href="posts/{p['id']}.html"><time datetime="{p['date_iso']}">{p['date_local']}</time></a>
    <p>{text}</p>
    <a href="https://t.me/{CHANNEL}/{p['id']}" rel="noopener">Читати та обговорити в Telegram →</a>
  </article>""")
    subs_line = f" · {html.escape(subs)} підписників" if subs else ""
    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SITE_TITLE}</title>
<meta name="description" content="{SITE_DESC}">
<meta property="og:title" content="{SITE_TITLE}">
<meta property="og:description" content="{SITE_DESC}">
<meta property="og:type" content="website">
<link rel="alternate" type="application/rss+xml" title="RSS" href="rss.xml">
<link rel="canonical" href="{SITE_URL}">
<style>
  body{{font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px;
       background:#111;color:#eee;line-height:1.55}}
  a{{color:#6ab7ff}} header a.cta,footer a.cta{{display:inline-block;background:#2b9fff;
       color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600}}
  article{{border-bottom:1px solid #333;padding:14px 0}}
  time{{color:#888;font-size:.85em}}
  h1{{font-size:1.4em}}
</style>
</head>
<body>
<header>
  <h1>{SITE_TITLE}</h1>
  <p>{SITE_DESC}</p>
  <a class="cta" href="{channel_url}" rel="noopener">Підписатися в Telegram{subs_line}</a>
</header>
<main>{"".join(items)}
</main>
<footer>
  <p><a class="cta" href="{channel_url}" rel="noopener">Всі новини — в Telegram-каналі</a></p>
  <p>Оновлено: {dt.datetime.now(dt.timezone.utc).strftime("%d.%m.%Y %H:%M")} UTC</p>
</footer>
</body>
</html>
"""


def _post_title(text: str, limit: int = 75) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def render_post_page(p: dict) -> str:
    title = _post_title(p["text"])
    page_url = f"{SITE_URL}posts/{p['id']}.html"
    channel_url = f"https://t.me/{CHANNEL}"
    desc = html.escape(" ".join(p["text"].split())[:160])
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title[:110],
        "datePublished": p["date_iso"],
        "articleBody": p["text"][:2000],
        "inLanguage": "uk",
        "publisher": {"@type": "Organization", "name": SITE_TITLE},
    }, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Новини України</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<link rel="canonical" href="{page_url}">
<script type="application/ld+json">{ld}</script>
<style>
  body{{font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px;
       background:#111;color:#eee;line-height:1.55}}
  a{{color:#6ab7ff}} a.cta{{display:inline-block;background:#2b9fff;color:#fff;
       padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600}}
  time{{color:#888;font-size:.85em}}
  h1{{font-size:1.3em}}
</style>
</head>
<body>
<p><a href="../">← Всі новини</a></p>
<article>
  <h1>{html.escape(title)}</h1>
  <time datetime="{p['date_iso']}">{p['date_local']}</time>
  <p>{html.escape(p['text'])}</p>
  <p><a href="https://t.me/{CHANNEL}/{p['id']}" rel="noopener">Обговорити в Telegram →</a></p>
</article>
<p><a class="cta" href="{channel_url}" rel="noopener">Більше новин — у Telegram-каналі</a></p>
</body>
</html>
"""


def render_rss(posts: list[dict]) -> str:
    items = []
    for p in posts[:MAX_IN_RSS]:
        title = html.escape(p["text"][:90] + ("…" if len(p["text"]) > 90 else ""))
        desc = html.escape(p["text"][:500])
        link = f"https://t.me/{CHANNEL}/{p['id']}"
        items.append(f"""
  <item>
    <title>{title}</title>
    <link>{link}</link>
    <guid>{link}</guid>
    <pubDate>{p['date_rfc']}</pubDate>
    <description>{desc}</description>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{SITE_TITLE}</title>
  <link>{SITE_URL}</link>
  <description>{SITE_DESC}</description>
  <language>uk</language>{"".join(items)}
</channel>
</rss>
"""


def render_sitemap(posts: list[dict]) -> str:
    urls = [f"  <url><loc>{SITE_URL}</loc><lastmod>{dt.date.today().isoformat()}</lastmod></url>"]
    for p in posts:
        urls.append(
            f"  <url><loc>{SITE_URL}posts/{p['id']}.html</loc>"
            f"<lastmod>{p['date_iso'][:10]}</lastmod></url>"
        )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls) + "\n</urlset>\n")


def ensure_indexnow_key(state: dict) -> str:
    key = state.get("indexnow_key")
    if not key:
        key = secrets.token_hex(16)
        state["indexnow_key"] = key
    (BASE / f"{key}.txt").write_text(key, encoding="utf-8")
    return key


def ping_indexnow(key: str, urls: list[str]) -> None:
    if not urls:
        return
    try:
        r = requests.post(
            "https://api.indexnow.org/indexnow",
            json={
                "host": urlparse(SITE_URL).netloc,
                "key": key,
                "keyLocation": f"{SITE_URL}{key}.txt",
                "urlList": urls[:100],
            },
            timeout=30,
        )
        print(f"IndexNow: {r.status_code} ({len(urls)} URL)")
    except requests.RequestException as e:
        print(f"[!] IndexNow: {e}")


def main() -> None:
    state = load_state()
    soup = fetch_channel_page(CHANNEL)
    if not soup:
        sys.exit("Не вдалося завантажити сторінку каналу.")
    subs = parse_subscribers(soup)
    new_count = 0
    for p in parse_posts(soup, CHANNEL):
        if not p["text"] or not p["posted_at"]:
            continue
        key = str(p["id"])
        if key not in state["posts"]:
            new_count += 1
        local = p["posted_at"].astimezone()
        state["posts"][key] = {
            "text": p["text"],
            "date_iso": p["posted_at"].isoformat(timespec="minutes"),
            "date_local": local.strftime("%d.%m.%Y %H:%M"),
            "date_rfc": format_datetime(p["posted_at"]),
        }
    print(f"В архіві постів: {len(state['posts'])} (нових: {new_count})")

    posts = [{"id": int(k), **v} for k, v in state["posts"].items()]
    posts.sort(key=lambda p: p["id"], reverse=True)

    (BASE / ".nojekyll").touch()
    (BASE / "index.html").write_text(render_index(posts, subs), encoding="utf-8")
    (BASE / "rss.xml").write_text(render_rss(posts), encoding="utf-8")
    (BASE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8"
    )
    posts_dir = BASE / "posts"
    posts_dir.mkdir(exist_ok=True)
    new_urls: list[str] = []
    for p in posts:
        page = posts_dir / f"{p['id']}.html"
        if not page.exists():
            new_urls.append(f"{SITE_URL}posts/{p['id']}.html")
        page.write_text(render_post_page(p), encoding="utf-8")
    (BASE / "sitemap.xml").write_text(render_sitemap(posts), encoding="utf-8")
    key = ensure_indexnow_key(state)
    save_state(state)
    print(f"Сайт згенеровано: {len(posts)} сторінок, нових: {len(new_urls)}")
    if new_urls:
        ping_indexnow(key, [SITE_URL] + new_urls)


if __name__ == "__main__":
    main()
