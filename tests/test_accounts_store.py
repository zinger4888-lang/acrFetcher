import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from acrfetcher.accounts_store import load_accounts_csv, parse_http_proxy, parse_telethon_http_proxy


class AccountsStoreTests(unittest.TestCase):
    def test_parse_http_proxy_basic(self):
        p = parse_http_proxy("1.2.3.4:8080")
        self.assertEqual(p, {"server": "http://1.2.3.4:8080"})

    def test_parse_http_proxy_auth(self):
        p = parse_http_proxy("1.2.3.4:8080:u:p")
        self.assertEqual(p, {"server": "http://1.2.3.4:8080", "username": "u", "password": "p"})

    def test_parse_telethon_proxy(self):
        p = parse_telethon_http_proxy("1.2.3.4:8080:u:p")
        self.assertIsNotNone(p)
        self.assertEqual(len(p), 6)

    def test_load_accounts_csv(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "accounts.csv"
            p.write_text("email,phone,proxy\nx@y.z,123,1.2.3.4:8080\n", encoding="utf-8")
            rows = load_accounts_csv(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].email, "x@y.z")
            self.assertEqual(rows[0].phone, "123")


if __name__ == "__main__":
    unittest.main()
