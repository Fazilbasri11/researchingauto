import json
import os
import unittest
from unittest.mock import MagicMock, patch

import publish_from_telegram as pub
import research_trending as res
import shared


class TestResearchingAuto(unittest.TestCase):
    def test_sanitize_topic_tag_strips_forbidden_chars(self):
        self.assertEqual(shared.sanitize_topic_tag("#CPNS 2026 & SKD."), "CPNS 2026")
        self.assertLessEqual(len(shared.sanitize_topic_tag("a" * 80)), 50)

    @patch("research_trending.requests.get")
    def test_fetch_google_trends_id_parses_xml(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = """<?xml version="1.0" encoding="UTF-8"?>
        <rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
          <channel>
            <item>
              <title>rupiah</title>
              <ht:approx_traffic>500+</ht:approx_traffic>
              <ht:news_item>
                <ht:news_item_title>Dolar Menguat Hari Ini</ht:news_item_title>
              </ht:news_item>
            </item>
          </channel>
        </rss>"""
        mock_get.return_value = mock_resp

        items = res.fetch_google_trends_id()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["keyword"], "rupiah")
        self.assertEqual(items[0]["traffic"], "500+")
        self.assertIn("Dolar Menguat", items[0]["context"])

    @patch("research_trending.fetch_google_news_id", side_effect=RuntimeError("503"))
    @patch("research_trending.fetch_google_trends_id", return_value=[{"keyword": "CPNS", "traffic": "1K+", "context": ""}])
    @patch("research_trending.fetch_bing_news_id", return_value=[])
    @patch("research_trending.fetch_gemini_live_topics", return_value=[])
    def test_collect_trending_sources_skips_failing_source(self, _g, _b, _t, _n):
        items = res.collect_trending_sources("k", "m", niche="cpns")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["keyword"], "CPNS")

    @patch("research_trending.call_gemini_json", return_value={"topics": []})
    def test_summarize_viral_topics_allows_empty_when_unsuitable(self, _mock_gem):
        items = res.summarize_viral_topics("k", "m", [{"keyword": "Banjir", "traffic": "10K+", "context": ""}])
        self.assertEqual(items, [])

    @patch("shared.requests.post")
    def test_generate_viral_thread_enforces_link_at_second_last(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "topic_tag": "#Karir & Kerja.",
                                        "posts": [
                                            "Hook ramai " + ("x" * 600),
                                            "Fase masalah",
                                            "Tip 1",
                                            "Tip 2",
                                            "Tip 3",
                                            "Produk + alasan + kekurangan",
                                            "Cek di sini ya",
                                            "Tanya di komen!",
                                        ],
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        posts, topic_tag = pub.generate_viral_thread(
            "fake-key",
            "gemini-2.5-flash",
            {"judul": "Tips Kerja Remote", "ringkasan": "Banyak dicari", "ide_hook": "Kerja remote 🔥"},
            "Buku Panduan Kerja",
            "https://example.com/produk-1\nhttps://example.com/produk-2",
        )
        self.assertEqual(topic_tag, "Karir Kerja")
        self.assertEqual(len(posts), 8)
        self.assertIn("https://example.com/produk-1", posts[-2])
        self.assertIn("https://example.com/produk-2", posts[-2])
        self.assertNotIn("https://example.com/produk-1", posts[-1])

        _, manual_topic = pub.generate_viral_thread(
            "fake-key",
            "gemini-2.5-flash",
            {"judul": "Tips Kerja Remote", "ringkasan": "Banyak dicari", "ide_hook": "Kerja remote 🔥"},
            "Buku Panduan Kerja",
            "https://example.com/produk-1",
            manual_topic_tag="Tips Karir",
        )
        self.assertEqual(manual_topic, "Tips Karir")
        for post in posts:
            self.assertLessEqual(len(post), shared.MAX_THREADS_CHARS)

    @patch("publish_from_telegram.time.sleep", return_value=None)
    @patch("publish_from_telegram.create_and_publish_single")
    def test_publish_thread_chain_sets_topic_only_on_first(self, mock_pub, _sleep):
        mock_pub.side_effect = ["id1", "id2", "id3"]
        ids = pub.publish_thread_chain("u1", "tok", ["p1", "p2", "p3"], topic_tag="Karir", dry_run=False)
        self.assertEqual(ids, ["id1", "id2", "id3"])
        self.assertEqual(mock_pub.call_args_list[0].kwargs["topic_tag"], "Karir")
        self.assertIsNone(mock_pub.call_args_list[1].kwargs["topic_tag"])
        self.assertEqual(mock_pub.call_args_list[2].kwargs["reply_to_id"], "id2")

    @patch("shared.time.sleep", return_value=None)
    @patch("shared.requests.post")
    def test_call_gemini_json_retries_on_503_then_succeeds(self, mock_post, mock_sleep):
        err_resp = MagicMock()
        err_resp.ok = False
        err_resp.status_code = 503
        err_resp.text = '{"error": {"code": 503, "status": "UNAVAILABLE"}}'
        err_resp.headers = {}

        ok_resp = MagicMock()
        ok_resp.ok = True
        ok_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
        }
        mock_post.side_effect = [err_resp, ok_resp]

        result = shared.call_gemini_json("fake-key", "gemini-2.5-flash", "hello")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertIn("gemini-2.5-flash:generateContent", mock_post.call_args_list[1].args[0])

    @patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "gemini-3.6-flash, gemini-3.5-flash-lite"})
    @patch("shared.time.sleep", return_value=None)
    @patch("shared.requests.post")
    def test_call_gemini_json_falls_back_when_primary_unavailable(self, mock_post, _sleep):
        err_503 = MagicMock()
        err_503.ok = False
        err_503.status_code = 503
        err_503.text = "high demand"
        err_503.headers = {}

        ok_resp = MagicMock()
        ok_resp.ok = True
        ok_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"fallback": true}'}]}}]
        }
        # Primary fails 3 times with 503, then fallback gemini-3.6-flash succeeds
        mock_post.side_effect = [err_503, err_503, err_503, ok_resp]

        result = shared.call_gemini_json("fake-key", "gemini-2.5-flash", "hello")
        self.assertEqual(result, {"fallback": True})
        self.assertEqual(mock_post.call_count, 4)
        self.assertIn("gemini-3.6-flash:generateContent", mock_post.call_args_list[3].args[0])

    @patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "gemini-3.6-flash"})
    @patch("shared.time.sleep", return_value=None)
    @patch("shared.requests.post")
    def test_call_gemini_search_skips_retries_on_404_and_uses_fallback(self, mock_post, mock_sleep):
        err_404 = MagicMock()
        err_404.ok = False
        err_404.status_code = 404
        err_404.text = "not found"
        err_404.headers = {}

        ok_resp = MagicMock()
        ok_resp.ok = True
        ok_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Topik hari ini"}]}}]
        }
        mock_post.side_effect = [err_404, ok_resp]

        result = shared.call_gemini_search_text("fake-key", "gemini-2.5-flash", "cari tren")
        self.assertEqual(result, "Topik hari ini")
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 0)
        self.assertIn("gemini-3.6-flash:generateContent", mock_post.call_args_list[1].args[0])

    @patch("shared.time.sleep", return_value=None)
    @patch("shared.requests.post")
    def test_call_gemini_fails_fast_on_401(self, mock_post, mock_sleep):
        err_401 = MagicMock()
        err_401.ok = False
        err_401.status_code = 401
        err_401.text = "invalid key"
        err_401.headers = {}
        mock_post.return_value = err_401

        with self.assertRaises(RuntimeError) as ctx:
            shared.call_gemini_json("bad-key", "gemini-2.5-flash", "hello")
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_sleep.call_count, 0)

    def test_build_gemini_model_candidates_env_none(self):
        with patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "none"}):
            self.assertEqual(
                shared.build_gemini_model_candidates("gemini-2.5-flash"),
                ["gemini-2.5-flash"],
            )


if __name__ == "__main__":
    unittest.main()
