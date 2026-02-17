import unittest

from acrfetcher.utils import (
    extract_ticket_info,
    is_telegram_link,
    normalize_telegram_link,
    pad_ansi,
    parse_message_link,
)


class UtilsTests(unittest.TestCase):
    def test_parse_message_link_public(self):
        ch, mid = parse_message_link("https://t.me/test_channel/123")
        self.assertEqual(ch, "@test_channel")
        self.assertEqual(mid, 123)

    def test_parse_message_link_private(self):
        ch, mid = parse_message_link("https://t.me/c/111222/333")
        self.assertEqual(ch, "c/111222")
        self.assertEqual(mid, 333)

    def test_normalize_tg_link(self):
        self.assertEqual(normalize_telegram_link("t.me/a/1"), "https://t.me/a/1")
        self.assertTrue(is_telegram_link("t.me/a/1"))

    def test_pad_ansi(self):
        s = pad_ansi("abc", 5)
        self.assertEqual(len(s), 5)

    def test_extract_ticket_info(self):
        txt = "Win $50 and 10000 GTD now!"
        info = extract_ticket_info(txt)
        self.assertIn("$50", info)


if __name__ == "__main__":
    unittest.main()
