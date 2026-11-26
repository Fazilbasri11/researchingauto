import json
import unittest
from unittest.mock import MagicMock, patch

import publish_from_telegram as pub
import research_trending as res
import shared


class TestResearchingAuto(unittest.TestCase):
    def test_sanitize_topic_tag_strips_forbidden_chars(self):
        self.assertEqual(shared.sanitize_topic_tag("#CPNS 2026 & SKD."), "CPNS 2026 dan SKD")
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
        self.assertEqual(topic_tag, "Karir dan Kerja")
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


if __name__ == "__main__":
    unittest.main()
