import threading
import time
import unittest
from types import SimpleNamespace

from streamdeck_companion.stats import MISSING, Collector, StatsReader


class FakePsutil:
    def __init__(self):
        self.cpu_calls = 0
        self.recv = 0
        self.sent = 0
        self.cpu = 37.4
        self.disk_error = False

    def cpu_percent(self, interval=None):
        self.cpu_calls += 1
        return self.cpu

    def virtual_memory(self):
        return SimpleNamespace(percent=82.6, used=12882 << 20, total=15599 << 20)

    def disk_usage(self, path):
        if self.disk_error:
            raise OSError("no such drive")
        return SimpleNamespace(percent=94.2, used=356 << 30, total=378 << 30)

    def net_io_counters(self):
        return SimpleNamespace(bytes_recv=self.recv, bytes_sent=self.sent)


class FakeGpu:
    def read(self):
        return {"gpu": 9, "gpuTemp": 45, "gpuPower": 4, "vramUsedMB": 1653, "vramTotalMB": 6141}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class CollectorTest(unittest.TestCase):
    def test_sample_values(self):
        ps, clock = FakePsutil(), Clock()
        c = Collector(psutil=ps, gpu=FakeGpu(), clock=clock, drive="C:\\")
        ps.recv, ps.sent = 2048 * 1024, 512 * 1024     # 2 s later: 1024 KB/s down, 256 KB/s up
        clock.t += 2.0
        s = c.sample()
        self.assertEqual((s["cpu"], s["ramPct"], s["ramUsedMB"], s["ramTotalMB"]), (37, 83, 12882, 15599))
        self.assertEqual((s["diskPct"], s["diskUsedGB"], s["diskTotalGB"]), (94, 356, 378))
        self.assertEqual((s["netDownKBs"], s["netUpKBs"]), (1024, 256))
        self.assertEqual((s["gpu"], s["gpuTemp"], s["gpuPower"], s["vramUsedMB"], s["vramTotalMB"]), (9, 45, 4, 1653, 6141))
        self.assertEqual(s["cpuTemp"], MISSING)  # no sensor: reported as unavailable, never invented

    def test_missing_gpu_and_disk_are_reported_as_missing(self):
        ps = FakePsutil()
        ps.disk_error = True
        s = Collector(psutil=ps, gpu=None, clock=Clock()).sample()
        self.assertEqual((s["gpu"], s["gpuTemp"], s["vramTotalMB"], s["diskPct"]), (MISSING,) * 4)

    def test_network_never_negative_and_second_sample_uses_new_baseline(self):
        ps, clock = FakePsutil(), Clock()
        c = Collector(psutil=ps, gpu=None, clock=clock)
        ps.recv = 1000 * 1024
        clock.t += 1.0
        self.assertEqual(c.sample()["netDownKBs"], 1000)
        ps.recv = 500 * 1024  # counter reset (adapter restart)
        clock.t += 1.0
        self.assertEqual(c.sample()["netDownKBs"], 0)
        ps.recv += 300 * 1024
        clock.t += 1.0
        self.assertEqual(c.sample()["netDownKBs"], 300)

    def test_prime_restarts_the_interval(self):
        ps = FakePsutil()
        c = Collector(psutil=ps, gpu=None, clock=Clock())
        before = ps.cpu_calls
        c.prime()
        self.assertEqual(ps.cpu_calls, before + 1)


class ReaderTest(unittest.TestCase):
    def make(self):
        calls = {"n": 0}

        class FakeCollector:
            def prime(self):
                pass

            def sample(self):
                calls["n"] += 1
                return {"cpu": calls["n"]}

        reader = StatsReader(FakeCollector, interval=0.02)
        self.assertTrue(reader.start())
        self.addCleanup(reader.stop)
        return reader, calls

    def wait(self, cond, seconds=3.0):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_idle_until_activated_and_idle_again_after(self):
        reader, calls = self.make()
        time.sleep(0.3)
        self.assertEqual(calls["n"], 0)            # nothing is measured while the screen is not visible
        self.assertIsNone(reader.latest())
        reader.set_active(True)
        self.assertTrue(self.wait(lambda: reader.latest() is not None))
        reader.set_active(False)
        time.sleep(0.2)
        settled = calls["n"]
        time.sleep(0.3)
        self.assertEqual(calls["n"], settled)      # and stops again when it is left

    def test_old_numbers_are_dropped_on_reactivation(self):
        reader, _ = self.make()
        reader.set_active(True)
        self.assertTrue(self.wait(lambda: reader.latest() is not None))
        reader.set_active(False)
        reader.set_active(True)
        self.assertIsNone(reader.latest())         # never show numbers from before the screen was left

    def test_failing_factory_is_reported(self):
        def boom():
            raise RuntimeError("no psutil")

        reader = StatsReader(boom)
        self.assertFalse(reader.start())
        self.assertIn("no psutil", reader.error)


if __name__ == "__main__":
    unittest.main()
