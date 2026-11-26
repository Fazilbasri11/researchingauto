# ResearchingAuto — Riset Topik Viral + ACC via Telegram → Auto-Post Utas Threads

Project terpisah yang berjalan **100% gratis** di cloud:
1. **Tiap 6 jam (GitHub Actions):** Mengambil topik yang sedang ramai di Indonesia (Google Trends & Google News), meminta Gemini AI merangkum 3 topik terbaik + ide hook, lalu mengirimkannya ke **Bot Telegram** dengan tombol **ACC / Skip**.
2. **Saat kamu klik ACC di Telegram:** Google Apps Script mengecek klik & balasan Telegram tiap 1 menit lewat Trigger (`pollTelegram`), lalu membalas meminta **Nama Produk | Link**.
3. **Saat kamu kirim Link:** Apps Script langsung memicu GitHub Actions (`publish_from_telegram.yml`) untuk merangkai utas 8 bagian bergaya santai (`gue/lo`) + emoji + topik biru (`topic_tag`) lalu mempostingnya ke **Threads** dan mengirim notifikasi ke Telegram.

---

## Ringkasan Setup Bertahap
1. **Buat Bot Telegram (`@BotFather`)** → dapat `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID`.
2. **Buat GitHub Personal Access Token (PAT)** → izin `Actions: Read and write` hanya untuk repo ini (`researchingauto`).
3. **Deploy [google_apps_script.js](google_apps_script.js) di `script.new`** → dapat `BOT_WEBHOOK_URL`, lalu pasang **Trigger `pollTelegram` (Every minute)** di Apps Script.
4. **Siapkan Gemini API Key & Akun Threads (App Meta terpisah)** → dapat `GEMINI_API_KEY`, `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`.
5. **Isi GitHub Secrets** di repository `Fazilbasri11/researchingauto`:
   - `GEMINI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `BOT_WEBHOOK_URL`
   - `BOT_WEBHOOK_SECRET`
   - `THREADS_USER_ID`
   - `THREADS_ACCESS_TOKEN`
   *(Opsional di GitHub Variables: `TARGET_NICHE` jika ingin fokus ke topik tertentu).*
