/**
 * Google Apps Script (Standalone di script.new) untuk repo Fazilbasri11/researchingauto:
 * 1. Menyimpan 3 topik viral terbaru dari GitHub Actions (via Web App doPost).
 * 2. Mengecek klik tombol ACC & balasan link di Telegram lewat Trigger tiap 1 menit
 *    (memakai getUpdates + offset supaya bebas bug 302 redirect & tidak pernah dobel).
 *
 * CARA SETUP DI script.new:
 * 1. Paste seluruh kode ini ke https://script.new dan isi CONFIG di bawah.
 * 2. Klik Deploy -> New deployment -> Web app (Execute as: Me, Who has access: Anyone).
 *    (Kalau cuma update kode pada deployment yang sudah ada: Deploy -> Manage deployments -> Edit -> New version -> Deploy).
 * 3. Di menu kiri Apps Script, klik ikon jam (Triggers / Pemicu) -> + Add Trigger:
 *    - Choose which function to run: pollTelegram
 *    - Select event source: Time-driven
 *    - Select type of time based trigger: Minutes timer
 *    - Select minute interval: Every minute
 *    Lalu klik Save.
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
  const resp = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
  return JSON.parse(resp.getContentText() || "{}");
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

function handleTelegramUpdate_(update, props) {
  // 1. Callback tombol Inline Keyboard (ACC / Skip)
  if (update.callback_query) {
    const cq = update.callback_query;
    const chatId = String((cq.message && cq.message.chat && cq.message.chat.id) || "");
    if (CONFIG.ALLOWED_CHAT_ID && chatId !== String(CONFIG.ALLOWED_CHAT_ID)) {
      return;
    }

    const data = String(cq.data || "");
    tgApi_("answerCallbackQuery", { callback_query_id: cq.id });

    if (data === "skip") {
      props.deleteProperty("PENDING_TOPIC_" + chatId);
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "👌 Oke, topik kali ini di-skip. Tunggu info topik ramai berikutnya ya!",
      });
      return;
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
        return;
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
    }
    return;
  }

  // 2. Pesan teks balasan dari user (berisi link produk)
  if (update.message && update.message.text) {
    const msg = update.message;
    const chatId = String((msg.chat && msg.chat.id) || "");
    if (CONFIG.ALLOWED_CHAT_ID && chatId !== String(CONFIG.ALLOWED_CHAT_ID)) {
      return;
    }

    const text = String(msg.text || "").trim();
    if (text === "/start" || text === "/help") {
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "🤖 Bot aktif! Tunggu daftar topik viral tiap 6 jam, lalu klik ACC dan balas dengan: Nama Produk | https://link-produk",
      });
      return;
    }

    const pendingRaw = props.getProperty("PENDING_TOPIC_" + chatId);
    if (!pendingRaw) {
      return;
    }

    const urlMatch = text.match(/https?:\/\/[^\s]+/i);
    if (!urlMatch) {
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "⚠️ Belum ada link yang valid (harus diawali https:// atau http://). Coba kirim lagi ya, misal:\nNama Produk | https://example.com/produk",
      });
      return;
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
}

/**
 * Jalankan fungsi ini lewat Time-driven Trigger (Every minute) di Apps Script.
 */
function pollTelegram() {
  const props = PropertiesService.getScriptProperties();
  const offset = Number(props.getProperty("TG_OFFSET") || "0");
  const res = tgApi_("getUpdates", {
    offset: offset,
    limit: 20,
    timeout: 0,
  });

  if (!res.ok || !res.result || res.result.length === 0) {
    return;
  }

  let maxId = offset;
  for (let i = 0; i < res.result.length; i++) {
    const upd = res.result[i];
    if (upd.update_id >= maxId) {
      maxId = upd.update_id + 1;
    }
    try {
      handleTelegramUpdate_(upd, props);
    } catch (err) {
      Logger.log("Error handle update " + upd.update_id + ": " + err);
    }
  }
  props.setProperty("TG_OFFSET", String(maxId));
}

function doGet() {
  return jsonResponse_({ ok: true, status: "ResearchingAuto bridge active" });
}

function doPost(e) {
  let body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || "{}");
  } catch (err) {
    return jsonResponse_({ ok: false, error: "Invalid JSON" });
  }

  const props = PropertiesService.getScriptProperties();

  // Endpoint internal dari GitHub Actions (research_trending.py)
  if (body.action === "save_topics") {
    if (body.secret !== CONFIG.WEBHOOK_SECRET) {
      return jsonResponse_({ ok: false, error: "Unauthorized" });
    }
    props.setProperty("LATEST_TOPICS", JSON.stringify(body.topics || []));
    return jsonResponse_({ ok: true });
  }

  return jsonResponse_({ ok: true });
}
