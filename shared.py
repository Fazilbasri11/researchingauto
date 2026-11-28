import json
import os
import random
import re
import time
from typing import Any

import requests

THREADS_API_BASE = "https://graph.threads.net/v1.0"
MAX_THREADS_CHARS = 500
DEFAULT_GEMINI_FALLBACK_MODELS = (
    "gemini-2.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)
RETRYABLE_GEMINI_STATUS_CODES = {429, 500, 502, 503, 504}


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


def sanitize_topic_tag(raw_tag: str, max_words: int = 2) -> str:
    tag = (raw_tag or "").strip().lstrip("#")
    tag = tag.replace("&", " ").replace(".", "")
    words = re.sub(r"\s+", " ", tag).strip().split()
    if max_words > 0:
        words = words[:max_words]
    tag = " ".join(words)
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


def build_gemini_model_candidates(
    primary_model: str,
    fallback_models: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Build an ordered, deduplicated list of Gemini models to try."""
    if fallback_models is None:
        raw_fallbacks = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
        if not raw_fallbacks:
            resolved_fallbacks: tuple[str, ...] | list[str] = DEFAULT_GEMINI_FALLBACK_MODELS
        elif raw_fallbacks.lower() == "none":
            resolved_fallbacks = []
        else:
            resolved_fallbacks = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]
    else:
        resolved_fallbacks = fallback_models

    candidates: list[str] = []
    for model in [primary_model, *resolved_fallbacks]:
        cleaned = str(model or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    if not candidates:
        raise RuntimeError("Minimal harus ada satu nama model Gemini yang valid.")
    return candidates


def _compute_retry_delay(
    resp: requests.Response | None,
    attempt: int,
    base_delay: float,
) -> float:
    """Compute backoff delay, honoring Retry-After header when provided."""
    if resp is not None:
        retry_after = (resp.headers.get("Retry-After") or "").strip()
        if retry_after:
            try:
                return min(max(float(retry_after), 0.5), 30.0)
            except ValueError:
                pass
    return base_delay * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)


def _post_gemini_with_retry(
    gemini_api_key: str,
    gemini_model: str,
    payload: dict[str, Any],
    error_prefix: str = "Gemini API error",
    fallback_models: list[str] | tuple[str, ...] | None = None,
    max_retries_per_model: int = 3,
    base_delay: float = 2.0,
    timeout: int = 45,
) -> dict[str, Any]:
    """Call Gemini generateContent with exponential backoff and model fallback."""
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": gemini_api_key,
    }
    models = build_gemini_model_candidates(gemini_model, fallback_models=fallback_models)
    attempts = max(1, max_retries_per_model)
    last_error = "Tidak diketahui"

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(1, attempts + 1):
            resp: requests.Response | None = None
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            except requests.RequestException as exc:
                last_error = f"{error_prefix} (jaringan/timeout, {model}): {exc}"
                if attempt < attempts:
                    delay = _compute_retry_delay(None, attempt, base_delay)
                    print(
                        f"Peringatan {error_prefix} jaringan di model '{model}' "
                        f"(percobaan {attempt}/{attempts}), coba lagi dalam {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    continue
                break

            if resp.ok:
                if model != models[0]:
                    print(f"Info: berhasil memakai model fallback Gemini '{model}'.")
                return resp.json()

            snippet = (resp.text or "").strip()[:300]
            last_error = f"{error_prefix} {resp.status_code} ({model}): {snippet}"

            if resp.status_code == 404:
                print(f"Peringatan model '{model}' tidak tersedia (404), beralih ke fallback...")
                break

            if resp.status_code in RETRYABLE_GEMINI_STATUS_CODES:
                if attempt < attempts:
                    delay = _compute_retry_delay(resp, attempt, base_delay)
                    print(
                        f"Peringatan {error_prefix} {resp.status_code} di model '{model}' "
                        f"(percobaan {attempt}/{attempts}), coba lagi dalam {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    continue
                print(
                    f"Peringatan model '{model}' masih error {resp.status_code} "
                    f"setelah {attempts}x percobaan, beralih ke fallback..."
                )
                break

            raise RuntimeError(f"{error_prefix} {resp.status_code}: {resp.text}")

    raise RuntimeError(
        f"{error_prefix} setelah mencoba model {', '.join(models)}. Error terakhir: {last_error}"
    )


def call_gemini_json(
    gemini_api_key: str,
    gemini_model: str,
    prompt: str,
    temperature: float = 0.85,
) -> dict[str, Any]:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    data = _post_gemini_with_retry(
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        payload=payload,
        error_prefix="Gemini API error",
    )
    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json_object(raw_text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Format output Gemini tidak sesuai: {data}") from exc


def call_gemini_search_text(
    gemini_api_key: str,
    gemini_model: str,
    prompt: str,
) -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    data = _post_gemini_with_retry(
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        payload=payload,
        error_prefix="Gemini Search error",
    )
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "\n".join(str(p.get("text", "")).strip() for p in parts if p.get("text")).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Format output Gemini Search tidak sesuai: {data}") from exc


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
