import asyncio
import time
import unittest
from unittest.mock import patch

from acrfetcher.watch_runtime import RunController
from acrfetcher.webhook import webhook_send_async


class SmokeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_controller_start_pause_run_quit(self):
        tick = {"n": 0}
        stop = asyncio.Event()

        async def worker():
            while not stop.is_set():
                tick["n"] += 1
                await asyncio.sleep(0.01)

        ctl = RunController(factories=[worker])
        await ctl.start()
        await asyncio.sleep(0.05)
        self.assertEqual(ctl.state, "running")

        await ctl.pause()
        self.assertEqual(ctl.state, "paused")
        n1 = tick["n"]
        await asyncio.sleep(0.03)
        self.assertEqual(n1, tick["n"])

        await ctl.run()
        await asyncio.sleep(0.03)
        self.assertEqual(ctl.state, "running")
        self.assertGreater(tick["n"], n1)

        stop.set()
        await ctl.quit()
        self.assertEqual(ctl.state, "quit")

    async def test_webhook_send_async_runs_in_thread(self):
        cfg = {"webhook_bot_token": "x", "webhook_chat_id": "1"}

        def slow_send(_text, _cfg):
            time.sleep(0.15)
            return (True, "")

        t0 = time.perf_counter()
        with patch("acrfetcher.webhook.webhook_send", side_effect=slow_send):
            task = asyncio.create_task(webhook_send_async("hello", cfg))
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            ok, _err = await task
        dt = time.perf_counter() - t0
        self.assertTrue(ok)
        self.assertGreaterEqual(dt, 0.15)


if __name__ == "__main__":
    unittest.main()
