import html
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from shared import call_gemini_json, get_env, send_telegram_message

TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=ID"
NEWS_RSS_BASE = "https://news.google.com/rss"
HT_NS = "{https://trends.google.com/trending/rss}"


def fetch_google_trends_id(limit: int = 12) -> list[dict[str, str]]:
    resp = requests.get(TRENDS_RSS_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        traffic = (item.findtext(f"{HT_NS}approx_traffic") or "").strip()
        news_titles = [
            (ni.findtext(f"{HT_NS}news_item_title") or "").strip()
            for ni in item.findall(f"{HT_NS}news_item")[:2]
        ]
        news_titles = [t for t in news_titles if t]
        if title:
            items.append(
                {
                    "keyword": title,
                    "traffic": traffic,
                    "context": " | ".join(news_titles),
                }
            )
    return items


def fetch_google_news_id(niche: str = "", limit: int = 10) -> list[dict[str, str]]:
    if niche.strip():
        q = urllib.parse.quote_plus(niche.strip())
        url = f"{NEWS_RSS_BASE}/search?q={q}&hl=id&gl=ID&ceid=ID:id"
    else:
        url = f"{NEWS_RSS_BASE}?hl=id&gl=ID&ceid=ID:id"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        if title:
            items.append({"keyword": title, "traffic": "Berita ramai", "context": ""})
    return items


def summarize_viral_topics(
    gemini_api_key: str,
    gemini_model: str,
    raw_trends: list[dict[str, str]],
    niche: str = "",
) -> list[dict[str, str]]:
    wib = timezone(timedelta(hours=7))
    now_str = datetime.now(wib).strftime("%Y-%m-%d %H:%M WIB")
    lines = []
    for idx, t in enumerate(raw_trends, start=1):
        extra = f" — {t['context']}" if t.get("context") else ""
        traf = f" ({t['traffic']})" if t.get("traffic") else ""
        lines.append(f"{idx}. {t['keyword']}{traf}{extra}")
    trend_block = "\n".join(lines)
    niche_rule = (
        f"Prioritaskan topik yang paling nyambung atau bisa ditarik ke niche: '{niche}'."
        if niche
        else "Pilih topik yang paling ramai dibahas masyarakat Indonesia dan paling enak dijadikan utas edukatif/rekomendasi produk."
    )

    prompt = f"""Kamu adalah kurator konten Threads Indonesia.
Waktu sekarang: {now_str}.

Berikut daftar topik dan berita yang sedang ramai dicari/dibahas di Indonesia saat ini:
{trend_block}

Tugasmu:
Pilih **tepat 3 topik terbaik** yang paling menarik untuk dijadikan utas Threads gaya santai (`gue/lo`). {niche_rule}
Hindari topik sensitif SARA, politik keras, atau tragedi yang tidak pantas dikaitkan dengan rekomendasi produk.

Untuk tiap topik, buat:
- `id`: "1", "2", "3"
- `judul`: judul topik singkat & menarik (maks 60 karakter)
- `ringkasan`: 1-2 kalimat bahasa santai menjelaskan kenapa topik ini lagi ramai dibahas
- `ide_hook`: contoh kalimat hook pembuka ala Threads (`gue/lo` + 1 emoji)
- `saran_produk`: jenis produk/alat bantu yang paling masuk akal dikaitkan dengan topik ini

Kembalikan HANYA JSON valid:
{{
  "topics": [
    {{
      "id": "1",
      "judul": "...",
      "ringkasan": "...",
      "ide_hook": "...",
      "saran_produk": "..."
    }}
  ]
}}"""

    parsed = call_gemini_json(gemini_api_key, gemini_model, prompt, temperature=0.8)
    topics = parsed.get("topics", [])
    cleaned: list[dict[str, str]] = []
    for idx, topic in enumerate(topics[:3], start=1):
        cleaned.append(
            {
                "id": str(idx),
                "judul": str(topic.get("judul", "")).strip(),
                "ringkasan": str(topic.get("ringkasan", "")).strip(),
                "ide_hook": str(topic.get("ide_hook", "")).strip(),
                "saran_produk": str(topic.get("saran_produk", "")).strip(),
            }
        )
    if not cleaned:
        raise RuntimeError(f"Gemini tidak mengembalikan daftar topik: {parsed}")
    return cleaned


def save_topics_to_webhook(
    webhook_url: str,
    webhook_secret: str,
    topics: list[dict[str, str]],
) -> None:
    resp = requests.post(
        webhook_url,
        json={
            "action": "save_topics",
            "secret": webhook_secret,
            "topics": topics,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Gagal menyimpan topik ke webhook: {data}")


def format_telegram_digest(topics: list[dict[str, str]], niche: str = "") -> tuple[str, dict[str, Any]]:
    header = "🔥 <b>Topik Lagi Ramai di Indonesia</b>"
    if niche:
        header += f" <i>({html.escape(niche)})</i>"
    parts = [header, "Pilih topik mana yang mau dibuatkan utas Threads:"]

    buttons: list[dict[str, str]] = []
    for t in topics:
        tid = t["id"]
        parts.append(
            f"\n<b>{tid}. {html.escape(t['judul'])}</b>\n"
            f"📌 {html.escape(t['ringkasan'])}\n"
            f"💡 <i>Hook:</i> {html.escape(t['ide_hook'])}\n"
            f"🛒 <i>Cocok untuk:</i> {html.escape(t['saran_produk'])}"
        )
        buttons.append({"text": f"✅ ACC #{tid}", "callback_data": f"acc:{tid}"})

    reply_markup = {
        "inline_keyboard": [
            buttons,
            [{"text": "❌ Skip Kali Ini", "callback_data": "skip"}],
        ]
    }
    return "\n".join(parts), reply_markup


def main() -> int:
    gemini_api_key = get_env("GEMINI_API_KEY", required=True)
    gemini_model = get_env("GEMINI_MODEL", "gemini-2.5-flash")
    telegram_bot_token = get_env("TELEGRAM_BOT_TOKEN", required=True)
    telegram_chat_id = get_env("TELEGRAM_CHAT_ID", required=True)
    webhook_url = get_env("BOT_WEBHOOK_URL", required=True)
    webhook_secret = get_env("BOT_WEBHOOK_SECRET", required=True)
    niche = get_env("TARGET_NICHE", "")

    trends = fetch_google_trends_id()
    if niche:
        trends = fetch_google_news_id(niche=niche, limit=8) + trends[:6]
    else:
        trends = trends[:10] + fetch_google_news_id(limit=5)

    topics = summarize_viral_topics(gemini_api_key, gemini_model, trends, niche=niche)
    save_topics_to_webhook(webhook_url, webhook_secret, topics)
    text, reply_markup = format_telegram_digest(topics, niche=niche)
    send_telegram_message(telegram_bot_token, telegram_chat_id, text, reply_markup=reply_markup)

    print(f"Berhasil kirim {len(topics)} topik ke Telegram chat {telegram_chat_id}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
