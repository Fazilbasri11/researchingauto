import html
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
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


LINK_PREVIEW_HEADERS = {
    "User-Agent": "TelegramBot (like TwitterBot)",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}
IGNORED_PAGE_TITLES = (
    "just a moment",
    "attention required",
    "access denied",
    "robot or human",
    "captcha",
    "situs belanja online",
    "403 forbidden",
    "404 not found",
)


class _MetaTagParser(HTMLParser):
    """Extract OpenGraph/meta tags and <title> from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = True
            return
        if tag_lower != "meta":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs if k}
        key = (attr_map.get("property") or attr_map.get("name") or "").strip().lower()
        content = attr_map.get("content", "").strip()
        if key and content and key not in self.meta:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._title_parts)).strip()


def _clean_product_text(raw_text: str) -> str:
    text = html.unescape(raw_text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:jual|beli)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\|\-–]\s*(?:shopee|tokopedia|lazada|blibli|tiktok shop).*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\.{3,}$", "", text).strip()
    if any(bad in text.lower() for bad in IGNORED_PAGE_TITLES):
        return ""
    return text


def _extract_slug_from_url(final_url: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlparse(final_url).path or "").strip("/")
    if not path or path.lower().startswith("opaanlp/"):
        return ""
    last_seg = path.split("/")[-1]
    last_seg = re.sub(r"-i\.\d+\.\d+$", "", last_seg)
    if "-" not in last_seg:
        return ""
    candidate = re.sub(r"[-_]+", " ", last_seg).strip()
    return candidate if len(candidate) >= 5 else ""


def fetch_product_info_from_url(url: str, timeout: int = 10) -> tuple[str, str]:
    """Fetch product title and description from a product URL's preview metadata."""
    try:
        resp = requests.get(
            url,
            headers=LINK_PREVIEW_HEADERS,
            allow_redirects=True,
            timeout=timeout,
        )
        if not resp.ok:
            return "", ""
        parser = _MetaTagParser()
        parser.feed(resp.text[:50000])
        raw_title = (
            parser.meta.get("og:title")
            or parser.meta.get("twitter:title")
            or parser.meta.get("title")
            or parser.title
        )
        raw_desc = (
            parser.meta.get("og:description")
            or parser.meta.get("twitter:description")
            or parser.meta.get("description")
            or ""
        )
        title = _clean_product_text(raw_title) or _extract_slug_from_url(resp.url)
        desc = _clean_product_text(raw_desc)
        if desc and title and desc.lower() == title.lower():
            desc = ""
        return title, desc
    except Exception as exc:
        print(f"Peringatan gagal membaca info produk dari link '{url}': {exc}")
        return "", ""


def resolve_product_context(product_name: str, links: list[str]) -> tuple[str, str]:
    """Return (resolved_product_name, resolved_product_detail) using link metadata when available."""
    titles: list[str] = []
    details: list[str] = []
    for url in links[:3]:
        title, desc = fetch_product_info_from_url(url)
        if title and title not in titles:
            titles.append(title)
        if desc and desc not in details:
            details.append(desc)

    explicit_name = product_name.strip()
    if explicit_name:
        resolved_name = explicit_name
    elif titles:
        resolved_name = " | ".join(titles)
        print(f"Info produk terdeteksi dari link: {resolved_name}")
    else:
        resolved_name = ""

    resolved_detail = " | ".join(details)
    return resolved_name, resolved_detail


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
    resolved_name, resolved_detail = resolve_product_context(product_name, links)
    produk_info = resolved_name or "(Nama produk tidak disebutkan — JANGAN mengarang nama/jenis produk tertentu)"
    detail_line = f"\n- Detail/deskripsi produk dari link: {resolved_detail}" if resolved_detail else ""
    produk_label_utan6 = resolved_name or "Produk rekomendasi di link berikut"

    prompt = f"""Kamu adalah kreator Threads Indonesia yang gaya penulisannya santai, jujur, padat, membumi, pakai sapaan "gue" dan "lo" (bukan bahasa iklan kaku, bukan gaya motivator klise).

TUGAS SEKARANG:
Buat 1 utas Threads **8 postingan berantai** yang mempromosikan produk di bawah secara natural, edukatif, dan relevan:
- Tahun saat ini: {current_year} (kalau menyebut tahun, wajib pakai {current_year} atau setelahnya!)
- Produk yang dipromosikan (WAJIB SESUAI INI): {produk_info}{detail_line}
- Link Produk (wajib masuk persis di Utas 7):
{links_display}
- Referensi topik yang lagi ramai (OPSIONAL, hanya dipakai jika nyambung dengan produk):
  * Topik: {judul}
  * Konteks: {ringkasan}
  * Ide hook: {ide_hook}

ATURAN KECOCOKAN PRODUK & TOPIK (PALING PENTING):
1. **WAJIB SESUAI PRODUK ASLI:** Seluruh utas (hook, masalah, tips, alasan, kekurangan, emoji, dan `topic_tag`) HARUS cocok dan nyambung dengan produk di atas (`{produk_info}`).
2. **DILARANG MENGARANG PRODUK LAIN:** Jangan pernah mengganti, mengasumsikan, atau mempromosikan produk lain (misalnya buku/kursus/barang lain) yang tidak sesuai dengan info produk di atas.
3. **CEK KECOCOKAN TOPIK:**
   - Jika referensi topik di atas nyambung secara natural dengan produknya, kaitkan topik tersebut ke produk.
   - Jika referensi topik **TIDAK nyambung** dengan produknya (misal topik soal CPNS/berita lain sedangkan produknya wadah makanan/peralatan rumah/skincare/gadget), **ABAIKAN topik yang tidak nyambung itu** dan pilih sudut pandang masalah sehari-hari + tips praktis yang benar-benar relevan dengan produk aslinya.
4. Jika nama produk belum terdeteksi (`Nama produk tidak disebutkan`), jangan menebak jenis barang spesifik; bahas secara umum dan arahkan pembaca cek detail produknya lewat link di Utas 7.

CONTOH RITME, EMOJI, & GAYA YANG DIINGINKAN:
- Utas 1 (Hook kuat 3 paragraf pendek dipisah baris kosong + 1 emoji relevan di akhir baris 1):
  Belakangan banyak yang nanya soal [masalah yang relevan dengan produk] 🙏

  Gue nggak bahas pakai teori ribet. Cuma 5 poin penting ini biar lo nggak salah pilih atau salah langkah.

  Mumpung lagi dibutuhin banyak orang, wajib paham dari sekarang.
- Utas 2 (Fase masalah pembaca yang relevan dengan produk + 3 bullet "•" + emoji):
  Lo sekarang lagi di fase mana? 🤔

  • [Masalah nyata 1 yang relevan dengan produk]
  • [Masalah nyata 2 yang relevan dengan produk]
  • [Masalah nyata 3 yang relevan dengan produk]

  Kalau salah satunya, 5 poin ini buat lo 👇
- Utas 3–5 (Tips / insight konkret yang nyambung ke produk + 1-2 emoji per kartu + baris kosong antar paragraf):
  📌 Tip 1: ...
  ✍️ Tip 2 & 3: ...
  ⚡ Tip 4 & 5: ...
- Utas 6 (Jembatan natural ke produk + 1 emoji yang cocok dengan jenis produk + Alasannya + Kekurangannya secara jujur):
  Kalau lo lagi cari rekomendasi yang praktis buat kebutuhan ini, cek ini:

  [1 emoji relevan dengan produk] {produk_label_utan6}

  Alasannya: ...
  Kekurangannya: ...
- Utas 7 (Pengantar emoji 👇 + Link Produk + soft CTA tanpa memaksa):
  Ini link-nya 👇
  {links_display}

  Kalau udah punya yang serupa, pakai aja. Yang penting sesuaikan sama kebutuhan lo sekarang.
- Utas 8 (Closing ajak komen + penyemangat + emoji, TANPA link):
  Kalau ada yang mau ditanyain atau diskusi soal ini, tanya di komen. Gue jawab satu-satu.

  Semoga membantu dan makin rapi/siap ke depannya! 💪

Aturan ketat:
1. Utas 1 wajib 3 paragraf pendek dipisah baris kosong (`\\n\\n`), baris 1 hook + 1 emoji, dilarang kalimat klise ("Sering banget lihat...", "Tahukah kamu...").
2. Utas 1–5 fokus membahas masalah & tips praktis yang relevan dengan produk (JANGAN jualan langsung atau taruh link di Utas 1–5).
3. Utas 6 kenalkan produk asli di atas secara jujur beserta `Alasannya:` dan `Kekurangannya:` yang relevan dengan produk tersebut.
4. Utas 7 khusus link produk persis aslinya + soft CTA santai.
5. Utas 8 murni ajakan diskusi di komentar + penyemangat, TANPA link.
6. JANGAN beri nomor kartu ("1.", "2.", "3.") dan JANGAN pakai hashtag (#).
7. Setiap postingan MAKSIMAL 450 karakter.
8. Sertakan `"topic_tag"` untuk topik biru Threads yang sesuai dengan isi utas/produk (MAKSIMAL 2 KATA yang padat & relevan, misal "Tips Dapur" atau "Hemat Belanja", TANPA simbol `#`, `.`, `&`, maks 50 karakter).

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
                error_text = str(exc)
                if len(error_text) > 3500:
                    error_text = error_text[:3500] + "..."
                send_telegram_message(
                    telegram_bot_token,
                    telegram_chat_id,
                    f"❌ Gagal memposting utas ke Threads:\n<code>{html.escape(error_text)}</code>",
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
