import unittest

from acrfetcher.detector import classify_result_text


class DetectorTests(unittest.TestCase):
    def test_already_claimed_is_success(self):
        status, detail = classify_result_text(
            "This offer has already been claimed.",
            success_patterns=["you got", "ticket"],
            fail_patterns=["this offer has expired"],
        )
        self.assertEqual(status, "success")
        self.assertTrue(detail)

    def test_expired_is_fail(self):
        status, detail = classify_result_text(
            "Sorry, this offer has expired.",
            success_patterns=["you got", "ticket"],
            fail_patterns=[],
        )
        self.assertEqual(status, "fail")
        self.assertTrue(detail)

    def test_none_when_no_match(self):
        status, _detail = classify_result_text(
            "Loading...",
            success_patterns=["you got", "ticket"],
            fail_patterns=["this offer has expired"],
        )
        self.assertEqual(status, "none")


if __name__ == "__main__":
    unittest.main()
