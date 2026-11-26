import html
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from shared import (
    THREADS_API_BASE,
    call_gemini_json,
    ensure_links_in_post,
    extract_links,
    get_env,
    sanitize_topic_tag,
    send_telegram_message,
    trim_post,
)


def generate_viral_thread(
    gemini_api_key: str,
    gemini_model: str,
    topic: dict[str, Any],
    product_name: str,
    product_link: str,
    manual_topic_tag: str = "",
) -> tuple[list[str], str]:
    current_year = datetime.now(timezone(timedelta(hours=7))).year
    judul = str(topic.get("judul", "")).strip()
    ringkasan = str(topic.get("ringkasan", "")).strip()
    ide_hook = str(topic.get("ide_hook", "")).strip()
    links = extract_links(product_link)
    links_display = "\n".join(links) if links else product_link.strip()
    produk_info = product_name.strip() or "produk/alat bantu rekomendasi di link berikut"

    prompt = f"""Kamu adalah kreator Threads Indonesia yang gaya penulisannya santai, jujur, padat, membumi, pakai sapaan "gue" dan "lo" (bukan bahasa iklan kaku, bukan gaya motivator klise).

TUGAS SEKARANG:
Gabungkan **topik yang lagi ramai dibahas sekarang** ini menjadi 1 utas Threads **8 postingan berantai** yang nyambung secara natural ke produk/link di bawah:
- Tahun saat ini: {current_year} (kalau menyebut tahun, wajib pakai {current_year} atau setelahnya!)
- Topik yang lagi ramai: {judul}
- Konteks topik: {ringkasan}
- Referensi ide hook: {ide_hook}
- Produk yang disisipkan di akhir utas: {produk_info}
- Link Produk (wajib masuk persis di Utas 7):
{links_display}

CONTOH RITME, EMOJI, & GAYA YANG DIINGINKAN:
- Utas 1 (Hook kuat 3 paragraf pendek dipisah baris kosong + 1 emoji di akhir judul):
  Belakangan lagi rame bahas [topik] 🙏

  Gue nggak bahas pakai teori ribet. Cuma 5 poin penting ini biar lo nggak salah langkah.

  Mumpung lagi hangat dibahas, wajib paham dari sekarang.
- Utas 2 (Fase masalah pembaca + 3 bullet "•" berupa kondisi nyata + emoji):
  Lo sekarang lagi di fase mana? 🤔

  • Ikut mantau beritanya, tapi bingung dampaknya ke diri sendiri
  • Pengen siap-siap, tapi nggak tahu mulai dari mana
  • Udah coba cari info, tapi malah makin pusing

  Kalau salah satunya, 5 poin ini buat lo 👇
- Utas 3–5 (Tips / insight konkret nyambung ke topik + 1-2 emoji per kartu + baris kosong antar paragraf):
  📌 Tip 1: ...
  ✍️ Tip 2 & 3: ...
  ⚡ Tip 4 & 5: ...
- Utas 6 (Jembatan natural ke produk + ikon emoji + Alasannya + Kekurangannya secara jujur):
  Soal alat bantu biar lebih siap, gue rekomendasiin ini:

  📘 [{produk_info}]

  Alasannya: ...
  Kekurangannya: ...
- Utas 7 (Pengantar emoji 👇 + Link Produk + soft CTA tanpa memaksa):
  Ini link-nya 👇
  {links_display}

  Kalau udah punya yang serupa, pakai aja. Yang penting gerak sekarang, bukan nanti.
- Utas 8 (Closing ajak komen + penyemangat + emoji, TANPA link):
  Kalau ada yang mau ditanyain atau diskusi soal topik ini, tanya di komen. Gue jawab satu-satu.

  Semangat, semoga makin siap ke depannya! 💪

Aturan ketat:
1. Utas 1 wajib 3 paragraf pendek dipisah baris kosong (`\\n\\n`), baris 1 hook + 1 emoji, dilarang kalimat klise ("Sering banget lihat...", "Tahukah kamu...").
2. Utas 1–5 fokus membahas isu yang lagi ramai & tips praktisnya (JANGAN jualan atau taruh link di Utas 1–5).
3. Utas 6 kenalkan produk secara jujur beserta `Alasannya:` dan `Kekurangannya:`.
4. Utas 7 khusus link produk persis aslinya + soft CTA santai.
5. Utas 8 murni ajakan diskusi di komentar + penyemangat, TANPA link.
6. JANGAN beri nomor kartu ("1.", "2.", "3.") dan JANGAN pakai hashtag (#).
7. Setiap postingan MAKSIMAL 450 karakter.
8. Sertakan `"topic_tag"` untuk topik biru Threads (1-3 kata relevan tanpa `#`, `.`, `&`, maks 50 karakter).

Kembalikan HANYA JSON valid:
{{
  "topic_tag": "Nama Topik",
  "posts": ["utas 1", "utas 2", "utas 3", "utas 4", "utas 5", "utas 6", "utas 7", "utas 8"]
}}"""

    parsed = call_gemini_json(gemini_api_key, gemini_model, prompt, temperature=0.9)
    posts = [
        str(p).replace("\ufffc", "").strip()
        for p in parsed.get("posts", [])
        if str(p).replace("\ufffc", "").strip()
    ]
    if len(posts) < 3:
        raise RuntimeError(f"Jumlah utas dari Gemini terlalu sedikit: {posts}")

    if links:
        posts[-2] = ensure_links_in_post(posts[-2], links)

    ai_topic = sanitize_topic_tag(str(parsed.get("topic_tag", "")))
    topic_tag = sanitize_topic_tag(manual_topic_tag) or ai_topic
    return [trim_post(p) for p in posts], topic_tag


def wait_container_ready(container_id: str, access_token: str, max_attempts: int = 10) -> None:
    for attempt in range(1, max_attempts + 1):
        resp = requests.get(
            f"{THREADS_API_BASE}/{container_id}",
            params={"fields": "status,error_message", "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        info = resp.json()
        status = info.get("status", "")
        if status in {"FINISHED", "PUBLISHED"}:
            return
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Container {container_id} gagal ({status}): {info.get('error_message')}")
        print(f"Menunggu container {container_id} ({attempt}/{max_attempts}, status={status})...")
        time.sleep(5)
    raise RuntimeError(f"Timeout menunggu container {container_id}.")


def create_and_publish_single(
    user_id: str,
    access_token: str,
    text: str,
    reply_to_id: str | None = None,
    topic_tag: str | None = None,
) -> str:
    payload: dict[str, str] = {
        "media_type": "TEXT",
        "text": text,
        "access_token": access_token,
    }
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    if topic_tag:
        payload["topic_tag"] = topic_tag

    create_resp = requests.post(f"{THREADS_API_BASE}/{user_id}/threads", json=payload, timeout=30)
    if not create_resp.ok:
        raise RuntimeError(f"Gagal buat container Threads ({create_resp.status_code}): {create_resp.text}")
    container_id = create_resp.json().get("id")
    if not container_id:
        raise RuntimeError(f"Response container tidak memiliki 'id': {create_resp.text}")

    time.sleep(1)
    wait_container_ready(container_id, access_token)

    pub_resp = requests.post(
        f"{THREADS_API_BASE}/{user_id}/threads_publish",
        json={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    if not pub_resp.ok:
        raise RuntimeError(f"Gagal publish Threads ({pub_resp.status_code}): {pub_resp.text}")
    post_id = pub_resp.json().get("id")
    if not post_id:
        raise RuntimeError(f"Response publish tidak memiliki 'id': {pub_resp.text}")
    return str(post_id)


def publish_thread_chain(
    user_id: str,
    access_token: str,
    posts: list[str],
    topic_tag: str = "",
    dry_run: bool = False,
) -> list[str]:
    if dry_run:
        print("=== DRY RUN MODE ===")
        for idx, text in enumerate(posts, start=1):
            extra = f" [TOPIC: {topic_tag}]" if (idx == 1 and topic_tag) else ""
            print(f"\n--- Utas {idx}/{len(posts)}{extra} ({len(text)} karakter) ---\n{text}")
        return [f"dry-run-{i}" for i in range(1, len(posts) + 1)]

    published_ids: list[str] = []
    prev_id: str | None = None
    for idx, text in enumerate(posts, start=1):
        tag = topic_tag if (idx == 1 and topic_tag) else None
        post_id = create_and_publish_single(
            user_id=user_id,
            access_token=access_token,
            text=text,
            reply_to_id=prev_id,
            topic_tag=tag,
        )
        published_ids.append(post_id)
        prev_id = post_id
        print(f"Berhasil publish Utas {idx}/{len(posts)} -> ID: {post_id}")
        if idx < len(posts):
            time.sleep(4)
    return published_ids


def main() -> int:
    dry_run = get_env("DRY_RUN", "false").lower() in {"1", "true", "yes"}
    gemini_api_key = get_env("GEMINI_API_KEY", required=True)
    gemini_model = get_env("GEMINI_MODEL", "gemini-2.5-flash")
    threads_user_id = get_env("THREADS_USER_ID", required=not dry_run)
    threads_access_token = get_env("THREADS_ACCESS_TOKEN", required=not dry_run)
    telegram_bot_token = get_env("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = get_env("TELEGRAM_CHAT_ID", "")

    topic_raw = get_env("TOPIC_JSON", required=True)
    topic = json.loads(topic_raw)
    product_name = get_env("PRODUCT_NAME", "")
    product_link = get_env("PRODUCT_LINK", required=True)
    manual_topic_tag = get_env("MANUAL_TOPIC_TAG", "")

    try:
        posts, topic_tag = generate_viral_thread(
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            topic=topic,
            product_name=product_name,
            product_link=product_link,
            manual_topic_tag=manual_topic_tag,
        )
        published_ids = publish_thread_chain(
            user_id=threads_user_id,
            access_token=threads_access_token,
            posts=posts,
            topic_tag=topic_tag,
            dry_run=dry_run,
        )
        if telegram_bot_token and telegram_chat_id:
            status_label = "🧪 DRY RUN selesai" if dry_run else "✅ Berhasil diposting ke Threads!"
            msg = (
                f"{status_label}\n"
                f"<b>Topik biru:</b> {html.escape(topic_tag or '-')}\n"
                f"<b>Jumlah utas:</b> {len(published_ids)} bagian\n\n"
                f"<b>Preview Hook:</b>\n{html.escape(posts[0])}"
            )
            send_telegram_message(telegram_bot_token, telegram_chat_id, msg)
        return 0
    except Exception as exc:
        if telegram_bot_token and telegram_chat_id:
            try:
                send_telegram_message(
                    telegram_bot_token,
                    telegram_chat_id,
                    f"❌ Gagal memposting utas ke Threads:\n<code>{html.escape(str(exc))}</code>",
                )
            except Exception:
                pass
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
