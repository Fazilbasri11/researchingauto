/**
 * Google Apps Script (Standalone di script.new) untuk repo Fazilbasri11/researchingauto:
 * 1. Menyimpan 3 topik viral terbaru dari GitHub Actions (via Web App doPost).
 * 2. Mengecek klik tombol ACC, input beberapa link produk, & input/skip Topic biru
 *    lewat Trigger tiap 1 menit (pollTelegram memakai getUpdates + offset).
 *
 * CARA SETUP DI script.new:
 * 1. Paste seluruh kode ini ke https://script.new dan isi CONFIG di bawah.
 * 2. Klik Deploy -> Manage deployments -> Edit -> New version -> Deploy.
 * 3. Pastikan Trigger Time-driven (Every minute) untuk fungsi pollTelegram sudah aktif.
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

function triggerPublishWorkflow_(topicObj, productName, productLink, manualTopic, chatId) {
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
        product_name: productName || "",
        product_link: productLink,
        topic_tag: manualTopic || "",
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

function startPublishJob_(state, manualTopic, chatId, props) {
  props.deleteProperty("PENDING_STATE_" + chatId);
  try {
    triggerPublishWorkflow_(
      state.topic,
      state.productName,
      state.productLink,
      manualTopic,
      chatId
    );
    const topicLabel = manualTopic ? manualTopic : "Auto pilihan AI";
    tgApi_("sendMessage", {
      chat_id: chatId,
      text:
        "⏳ Siap! Sedang merangkai utas 8 bagian lewat Gemini AI dan memposting ke Threads.\n" +
        "🏷️ Topic biru: " + topicLabel + "\n" +
        "Tunggu ~1 menit ya...",
    });
  } catch (err) {
    tgApi_("sendMessage", {
      chat_id: chatId,
      text: "❌ Gagal memicu GitHub Actions: " + err.message,
    });
  }
}

function handleTelegramUpdate_(update, props) {
  // 1. Callback tombol Inline Keyboard
  if (update.callback_query) {
    const cq = update.callback_query;
    const chatId = String((cq.message && cq.message.chat && cq.message.chat.id) || "");
    if (CONFIG.ALLOWED_CHAT_ID && chatId !== String(CONFIG.ALLOWED_CHAT_ID)) {
      return;
    }

    const data = String(cq.data || "");
    tgApi_("answerCallbackQuery", { callback_query_id: cq.id });

    if (data === "skip") {
      props.deleteProperty("PENDING_STATE_" + chatId);
      tgApi_("sendMessage", {
        chat_id: chatId,
        text: "👌 Oke, topik kali ini di-skip. Tunggu info topik ramai berikutnya ya!",
      });
      return;
    }

    if (data === "skip_topic") {
      const stateRaw = props.getProperty("PENDING_STATE_" + chatId);
      if (!stateRaw) {
        tgApi_("sendMessage", {
          chat_id: chatId,
          text: "⚠️ Sesi topik tidak ditemukan atau sudah diproses.",
        });
        return;
      }
      const state = JSON.parse(stateRaw);
      if (state.step !== "WAIT_TOPIC") {
        return;
      }
      startPublishJob_(state, "", chatId, props);
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

      props.setProperty(
        "PENDING_STATE_" + chatId,
        JSON.stringify({ step: "WAIT_LINK", topic: chosen })
      );
      tgApi_("sendMessage", {
        chat_id: chatId,
        parse_mode: "HTML",
        text:
          "✅ <b>Topik dipilih:</b> " + chosen.judul + "\n\n" +
          "Sekarang balas chat ini dengan <b>Nama Produk | Link</b> (boleh kirim beberapa link sekaligus, pisahkan spasi atau baris baru).\n\n" +
          "Contoh:\n<code>Buku A &amp; Buku B | https://example.com/1 https://example.com/2</code>",
      });
    }
    return;
  }

  // 2. Pesan teks balasan dari user
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
        text: "🤖 Bot aktif! Tunggu daftar topik viral tiap 6 jam, lalu klik ACC → kirim Nama Produk | Link (bisa beberapa link) → isi Topic biru atau Skip.",
      });
      return;
    }

    const stateRaw = props.getProperty("PENDING_STATE_" + chatId);
    if (!stateRaw) {
      return;
    }
    const state = JSON.parse(stateRaw);

    if (state.step === "WAIT_LINK") {
      const urls = text.match(/https?:\/\/[^\s,]+/gi);
      if (!urls || urls.length === 0) {
        tgApi_("sendMessage", {
          chat_id: chatId,
          text: "⚠️ Belum ada link yang valid (harus diawali https:// atau http://). Coba kirim lagi ya, misal:\nNama Produk | https://example.com/1 https://example.com/2",
        });
        return;
      }

      let productName = "";
      if (text.indexOf("|") !== -1) {
        productName = text.split("|")[0].trim();
      } else {
        let remaining = text;
        for (let i = 0; i < urls.length; i++) {
          remaining = remaining.replace(urls[i], "");
        }
        productName = remaining.replace(/\s+/g, " ").trim();
      }

      const nextState = {
        step: "WAIT_TOPIC",
        topic: state.topic,
        productName: productName,
        productLink: urls.join("\n"),
      };
      props.setProperty("PENDING_STATE_" + chatId, JSON.stringify(nextState));

      tgApi_("sendMessage", {
        chat_id: chatId,
        parse_mode: "HTML",
        text:
          "🔗 <b>" + urls.length + " link diterima!</b>\n\n" +
          "🏷️ Sekarang mau pakai <b>Topic biru</b> apa di Threads?\n" +
          "Balas chat ini dengan nama topic-nya (contoh: <code>CPNS 2026</code>), atau klik tombol <b>Skip</b> di bawah biar AI yang pilihkan otomatis.",
        reply_markup: {
          inline_keyboard: [[{ text: "⏭️ Skip (Auto AI)", callback_data: "skip_topic" }]],
        },
      });
      return;
    }

    if (state.step === "WAIT_TOPIC") {
      const manualTopic = text.toLowerCase() === "/skip" || text.toLowerCase() === "skip" ? "" : text;
      startPublishJob_(state, manualTopic, chatId, props);
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

  if (body.action === "save_topics") {
    if (body.secret !== CONFIG.WEBHOOK_SECRET) {
      return jsonResponse_({ ok: false, error: "Unauthorized" });
    }
    props.setProperty("LATEST_TOPICS", JSON.stringify(body.topics || []));
    return jsonResponse_({ ok: true });
  }

  return jsonResponse_({ ok: true });
}
