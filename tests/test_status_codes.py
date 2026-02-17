import unittest

from acrfetcher.status_codes import normalize_status, status_label


class StatusCodesTests(unittest.TestCase):
    def test_missed_alias(self):
        st = normalize_status("mist")
        self.assertIsNotNone(st)
        self.assertEqual(st.value, "MISSED")

    def test_fail_label(self):
        self.assertIn("FAIL", status_label("FAIL"))

    def test_proxy_labels(self):
        self.assertIn("PROXY TGR", status_label("PROXY_TGR", "RESET"))
        self.assertIn("PROXY WEBR", status_label("PROXY_WEBR", "407"))

    def test_stopped_label(self):
        self.assertIn("STOPPED", status_label("STOPPED"))


if __name__ == "__main__":
    unittest.main()
