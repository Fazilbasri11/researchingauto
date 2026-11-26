import json
import os
import re
from typing import Any

import requests

THREADS_API_BASE = "https://graph.threads.net/v1.0"
MAX_THREADS_CHARS = 500


def get_env(name: str, default: str = "", required: bool = False) -> str:
    val = os.getenv(name, default).strip()
    if required and not val:
        raise RuntimeError(f"Environment variable '{name}' wajib diisi.")
    return val


def extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def trim_post(text: str, limit: int = MAX_THREADS_CHARS) -> str:
    text = text.replace("\ufffc", "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def sanitize_topic_tag(raw_tag: str) -> str:
    tag = (raw_tag or "").strip().lstrip("#")
    tag = tag.replace("&", " dan ").replace(".", "")
    tag = re.sub(r"\s+", " ", tag).strip()
    if len(tag) > 50:
        tag = tag[:50].strip()
    return tag


def extract_links(raw_links: str) -> list[str]:
    urls = re.findall(r"https?://[^\s,]+", raw_links)
    if urls:
        return urls
    cleaned = raw_links.strip()
    return [cleaned] if cleaned else []


def ensure_links_in_post(post_text: str, links: list[str]) -> str:
    missing = [u for u in links if u not in post_text]
    if not missing:
        return trim_post(post_text)
    links_block = "\n".join(missing)
    max_body = MAX_THREADS_CHARS - len(links_block) - 2
    if max_body <= 20:
        return trim_post(links_block)
    return trim_post(post_text, max_body) + f"\n\n{links_block}"


def call_gemini_json(
    gemini_api_key: str,
    gemini_model: str,
    prompt: str,
    temperature: float = 0.85,
) -> dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": gemini_api_key,
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=45)
    if not resp.ok:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text}")
    data = resp.json()
    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json_object(raw_text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Format output Gemini tidak sesuai: {data}") from exc


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text}")
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram gagal kirim pesan: {data}")
    return data
