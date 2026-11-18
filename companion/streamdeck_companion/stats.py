"""PC statistics for the stats screen.

`Collector` reads the numbers (psutil for CPU / RAM / disk / network, the NVIDIA driver's NVML for the GPU);
`StatsReader` runs it on a worker thread, but only while the stats screen is visible (`set_active`).
A value that cannot be read is -1 (the board shows "n/a"): e.g. the CPU temperature, which Windows only
exposes to elevated programs or through extra sensor software.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger("stats")

MISSING = -1
INTERVAL_S = 1.0


class NvmlGpu:
    """First NVIDIA GPU through the driver's NVML library (no process is spawned per reading)."""

    def __init__(self):
        import pynvml  # nvidia-ml-py

        self._n = pynvml
        pynvml.nvmlInit()
        self._h = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.name = pynvml.nvmlDeviceGetName(self._h)

    def read(self) -> dict:
        n, h = self._n, self._h
        out = {"gpu": MISSING, "gpuTemp": MISSING, "gpuPower": MISSING, "vramUsedMB": MISSING, "vramTotalMB": MISSING}
        for key, fn in (
            ("gpu", lambda: n.nvmlDeviceGetUtilizationRates(h).gpu),
            ("gpuTemp", lambda: n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU)),
            ("gpuPower", lambda: round(n.nvmlDeviceGetPowerUsage(h) / 1000)),
        ):
            try:
                out[key] = int(fn())
            except Exception:  # one sensor failing must not hide the others
                pass
        try:
            mem = n.nvmlDeviceGetMemoryInfo(h)
            out["vramUsedMB"], out["vramTotalMB"] = int(mem.used >> 20), int(mem.total >> 20)
        except Exception:
            pass
        return out


class Collector:
    def __init__(self, psutil=None, gpu="auto", clock: Callable[[], float] = time.monotonic, drive: str | None = None):
        if psutil is None:
            import psutil as _psutil

            psutil = _psutil
        self._ps = psutil
        self._clock = clock
        self._drive = drive or (os.environ.get("SystemDrive", "C:") + "\\")
        self._gpu = self._open_gpu() if gpu == "auto" else gpu
        self._last_net: tuple[float, int, int] | None = None
        self.prime()

    @staticmethod
    def _open_gpu():
        try:
            gpu = NvmlGpu()
            log.info("GPU: %s", gpu.name)
            return gpu
        except Exception as e:
            log.info("no NVIDIA GPU readings (%s)", e)
            return None

    def prime(self) -> None:
        """Start a fresh measuring interval (CPU load and network speed are averages since the last call)."""
        self._ps.cpu_percent(None)
        c = self._ps.net_io_counters()
        self._last_net = (self._clock(), c.bytes_recv, c.bytes_sent)

    def sample(self) -> dict:
        ps = self._ps
        out = {"cpu": round(ps.cpu_percent(None)), "cpuTemp": MISSING}
        vm = ps.virtual_memory()
        out.update(ramPct=round(vm.percent), ramUsedMB=int(vm.used >> 20), ramTotalMB=int(vm.total >> 20))
        try:
            disk = ps.disk_usage(self._drive)
            out.update(diskPct=round(disk.percent), diskUsedGB=int(disk.used >> 30), diskTotalGB=int(disk.total >> 30))
        except OSError:
            out.update(diskPct=MISSING, diskUsedGB=MISSING, diskTotalGB=MISSING)
        out.update(self._network())
        out.update(self._gpu.read() if self._gpu else
                   {"gpu": MISSING, "gpuTemp": MISSING, "gpuPower": MISSING, "vramUsedMB": MISSING,
                    "vramTotalMB": MISSING})
        return out

    def _network(self) -> dict:
        now, c = self._clock(), self._ps.net_io_counters()
        then, recv0, sent0 = self._last_net or (now, c.bytes_recv, c.bytes_sent)
        dt = max(now - then, 1e-3)
        self._last_net = (now, c.bytes_recv, c.bytes_sent)
        return {"netDownKBs": max(0, round((c.bytes_recv - recv0) / dt / 1024)),
                "netUpKBs": max(0, round((c.bytes_sent - sent0) / dt / 1024))}


class StatsReader:
    """Samples once a second on a worker thread while active; idle (no work at all) otherwise."""

    def __init__(self, collector_factory: Callable[[], Collector] = Collector, interval: float = INTERVAL_S):
        self._factory = collector_factory
        self._interval = interval
        self._active = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._latest: tuple[int, dict] | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="stats", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self.error is None

    def stop(self) -> None:
        self._stop.set()
        self._active.set()  # wake the thread so it can exit

    def set_active(self, on: bool) -> None:
        if on:
            with self._lock:
                self._latest = None  # never show numbers from before the screen was left
            self._active.set()
        else:
            self._active.clear()

    def latest(self) -> tuple[int, dict] | None:
        """(sequence number, sample) of the newest reading since the screen became active, or None."""
        with self._lock:
            return self._latest

    def _run(self) -> None:
        try:
            collector = self._factory()
        except Exception as e:
            self.error = str(e)
            log.error("stats unavailable: %s", e)
            self._ready.set()
            return
        self._ready.set()
        was_active = False
        while not self._stop.is_set():
            if not self._active.wait(0.25):
                was_active = False
                continue
            if self._stop.is_set():
                break
            if not was_active:  # just became visible: start a clean measuring interval
                collector.prime()
                was_active = True
                self._stop.wait(0.4)
                if not self._active.is_set():
                    continue
            try:
                sample = collector.sample()
            except Exception as e:
                log.debug("sample failed: %s", e)
                self._stop.wait(self._interval)
                continue
            with self._lock:
                if self._active.is_set():  # deactivated while sampling: drop the result
                    self._seq += 1
                    self._latest = (self._seq, sample)
            self._stop.wait(self._interval)
