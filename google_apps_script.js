/**
 * Google Apps Script (Standalone di script.new) sebagai Webhook Bot Telegram 24 jam
 * sekaligus penyimpan daftar topik sementara untuk repo Fazilbasri11/researchingauto.
 *
 * CARA PAKAI:
 * 1. Buka https://script.new (login akun Google kamu).
 * 2. Hapus kode bawaan, paste seluruh kode ini.
 * 3. Isi 6 nilai konfigurasi di bawah (bisa juga lewat Project Settings -> Script Properties).
 * 4. Klik Deploy -> New deployment -> Web app:
 *    - Execute as: Me
 *    - Who has access: Anyone
 * 5. Salin Web App URL (/exec), lalu daftarkan ke Telegram dengan membuka URL ini di browser:
 *    https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<WEB_APP_URL>
 */

const CONFIG = {
  WEBHOOK_SECRET: "GANTI_RAHASIA_WEBHOOK",
  TELEGRAM_BOT_TOKEN: "GANTI_TOKEN_BOT_TELEGRAM",
  ALLOWED_CHAT_ID: "GANTI_CHAT_ID_TELEGRAM_KAMU",
  GITHUB_TOKEN: "GANTI_GITHUB_PAT",
  GITHUB_OWNER: "Fazilbasri11",
  GITHUB_REPO: "researchingauto",
  WORKFLOW_FILE: "publish_from_telegram.yml",
};

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function tgApi_(method, payload) {
  const url = "https://api.telegram.org/bot" + CONFIG.TELEGRAM_BOT_TOKEN + "/" + method;
  UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
}

function triggerPublishWorkflow_(topicObj, productName, productLink, chatId) {
  const url =
    "https://api.github.com/repos/" +
    CONFIG.GITHUB_OWNER + "/" +
    CONFIG.GITHUB_REPO +
    "/actions/workflows/" +
    CONFIG.WORKFLOW_FILE +
    "/dispatches";

  const resp = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    headers: {
      Authorization: "Bearer " + CONFIG.GITHUB_TOKEN,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    payload: JSON.stringify({
      ref: "main",
      inputs: {
        topic_json: JSON.stringify(topicObj),
        product_name: productName,
        product_link: productLink,
        chat_id: String(chatId),
        dry_run: "false",
      },
    }),
    muteHttpExceptions: true,
  });

  const code = resp.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error("GitHub API (" + code + "): " + resp.getContentText());
  }
}

function doGet() {
  return jsonResponse_({ ok: true, status: "Telegram webhook active" });
}

function doPost(e) {
  let body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
  } catch (err) {
    return jsonResponse_({ ok: false, error: "Invalid JSON" });
  }

  const props = PropertiesService.getScriptProperties();

  // 1. Endpoint internal dari GitHub Actions (research_trending.py)
  if (body.action === "save_topics") {
    if (body.secret !== CONFIG.WEBHOOK_SECRET) {
      return jsonResponse_({ ok: false, error: "Unauthorized" });
    }
    props.setProperty("LATEST_TOPICS", JSON.stringify(body.topics || []));
    return jsonResponse_({ ok: true });
  }

  // 2. Callback tombol Inline Keyboard Telegram (ACC / Skip)
  if (body.callback_query) {
    const cq = body.callback_query;
    const chatId = String(cq.message && cq.message.chat && cq.message.chat.id || "");
    if (CONFIG.ALLOWED_CHAT_ID && chatId !== String(CONFIG.ALLOWED_CHAT_ID)) {
      return jsonResponse_({ ok: true });
    }

    const data = String(cq.data || "");
    tgApi_("answerCallbackQuery", { callback_query_id: cq.id });

    if (data === "skip") {
      props.deleteProperty("PENDING_TOPIC_" + chatId);
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "👌 Oke, topik kali ini di-skip. Tunggu info topik ramai berikutnya ya!",
      });
      return jsonResponse_({ ok: true });
    }

    if (data.indexOf("acc:") === 0) {
      const targetId = data.split(":")[1];
      const topics = JSON.parse(props.getProperty("LATEST_TOPICS") || "[]");
      const chosen = topics.find((t) => String(t.id) === String(targetId));

      if (!chosen) {
        tgApi_("sendMessage", {
          chat_id: chatId,
          text: "⚠️ Topik sudah kedaluwarsa atau belum tersimpan. Jalankan riset lagi ya.",
        });
        return jsonResponse_({ ok: true });
      }

      props.setProperty("PENDING_TOPIC_" + chatId, JSON.stringify(chosen));
      tgApi_("sendMessage", {
        chat_id: chatId,
        parse_mode: "HTML",
        text:
          "✅ <b>Topik dipilih:</b> " + chosen.judul + "\n\n" +
          "Sekarang balas chat ini dengan <b>Nama Produk | Link</b> (atau cukup <b>Link</b> saja).\n" +
          "Contoh:\n<code>Buku Panduan Kerja | https://example.com/produk</code>",
      });
      return jsonResponse_({ ok: true });
    }
    return jsonResponse_({ ok: true });
  }

  // 3. Pesan teks balasan dari user (berisi link produk)
  if (body.message && body.message.text) {
    const msg = body.message;
    const chatId = String(msg.chat && msg.chat.id || "");
    if (CONFIG.ALLOWED_CHAT_ID && chatId !== String(CONFIG.ALLOWED_CHAT_ID)) {
      return jsonResponse_({ ok: true });
    }

    const text = String(msg.text || "").trim();
    if (text === "/start" || text === "/help") {
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "🤖 Bot aktif! Tunggu daftar topik viral tiap 6 jam, lalu klik ACC dan balas dengan: Nama Produk | https://link-produk",
      });
      return jsonResponse_({ ok: true });
    }

    const pendingRaw = props.getProperty("PENDING_TOPIC_" + chatId);
    if (!pendingRaw) {
      return jsonResponse_({ ok: true });
    }

    const urlMatch = text.match(/https?:\/\/[^\s]+/i);
    if (!urlMatch) {
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "⚠️ Belum ada link yang valid (harus diawali https:// atau http://). Coba kirim lagi ya, misal:\nNama Produk | https://example.com/produk",
      });
      return jsonResponse_({ ok: true });
    }

    const productLink = urlMatch[0];
    let productName = "";
    if (text.indexOf("|") !== -1) {
      productName = text.split("|")[0].trim();
    } else {
      productName = text.replace(productLink, "").trim();
    }

    const topicObj = JSON.parse(pendingRaw);
    props.deleteProperty("PENDING_TOPIC_" + chatId);

    try {
      triggerPublishWorkflow_(topicObj, productName, productLink, chatId);
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "⏳ Siap! Sedang merangkai utas 8 bagian lewat Gemini AI dan memposting ke Threads. Tunggu ~1 menit ya...",
      });
    } catch (err) {
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "❌ Gagal memicu GitHub Actions: " + err.message,
      });
    }
  }

  return jsonResponse_({ ok: true });
}
