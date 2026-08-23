"""Safe aggregate host resource metrics for the WebUI system panel (#693).

The browser only needs coarse CPU/RAM/disk usage. Linux uses procfs first;
platforms without procfs (for example macOS) fall back to psutil for aggregate
CPU/RAM metrics. Keep the payload intentionally small: no process lists,
command strings, user identities, environment variables, or filesystem topology
leave the server.
"""

from __future__ import annotations

import shutil
import threading
import time
from importlib import import_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PROC_STAT = Path("/proc/stat")
_PROC_MEMINFO = Path("/proc/meminfo")
_CPU_SAMPLE_SECONDS = 0.05


def _load_optional_psutil():
    try:
        return import_module("psutil")
    except ImportError:
        raise RuntimeError("psutil_unavailable") from None


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_percent(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0:
        numeric = 0.0
    if numeric > 100:
        numeric = 100.0
    return round(numeric, 1)


def _read_proc_stat_cpu() -> tuple[int, int]:
    """Return (idle_ticks, total_ticks) from Linux /proc/stat."""
    with _PROC_STAT.open("r", encoding="utf-8") as handle:
        first = handle.readline().strip().split()
    if not first or first[0] != "cpu":
        raise RuntimeError("proc_stat_unavailable")
    values = [int(part) for part in first[1:]]
    if len(values) < 4:
        raise RuntimeError("proc_stat_unavailable")
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    if total <= 0:
        raise RuntimeError("proc_stat_unavailable")
    return idle, total


def _cpu_delta_percent(start: tuple[int, int], end: tuple[int, int]) -> float:
    idle_delta = end[0] - start[0]
    total_delta = end[1] - start[1]
    if total_delta <= 0:
        return 0.0
    busy_delta = max(0, total_delta - max(0, idle_delta))
    return _clamp_percent((busy_delta / total_delta) * 100.0)


def _cpu_percent() -> float:
    """Sample aggregate CPU usage.

    A short local sample avoids storing cross-request state and returns a stable
    percentage on the first poll. Linux uses procfs without extra dependencies;
    platforms without procfs fall back to psutil when it is already available.
    Unsupported platforms raise a safe error code.
    """
    try:
        start = _read_proc_stat_cpu()
    except OSError:
        psutil = _load_optional_psutil()
        return _clamp_percent(psutil.cpu_percent(interval=_CPU_SAMPLE_SECONDS))
    time.sleep(_CPU_SAMPLE_SECONDS)
    try:
        end = _read_proc_stat_cpu()
    except OSError:
        psutil = _load_optional_psutil()
        return _clamp_percent(psutil.cpu_percent(interval=0.0))
    return _cpu_delta_percent(start, end)


def _read_meminfo_kib() -> dict[str, int]:
    data: dict[str, int] = {}
    with _PROC_MEMINFO.open("r", encoding="utf-8") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            if not key or not rest:
                continue
            parts = rest.strip().split()
            if not parts:
                continue
            try:
                data[key] = int(parts[0])
            except ValueError:
                continue
    return data


def _memory_usage() -> dict[str, int | float]:
    try:
        meminfo = _read_meminfo_kib()
    except OSError:
        vm = _load_optional_psutil().virtual_memory()
        total = int(getattr(vm, "total", 0) or 0)
        if total <= 0:
            raise RuntimeError("memory_unavailable") from None
        available = max(0, int(getattr(vm, "available", 0) or 0))
    else:
        total = int(meminfo.get("MemTotal") or 0) * 1024
        if total <= 0:
            raise RuntimeError("meminfo_unavailable")
        available_kib = meminfo.get("MemAvailable")
        if available_kib is None:
            available_kib = (
                meminfo.get("MemFree", 0)
                + meminfo.get("Buffers", 0)
                + meminfo.get("Cached", 0)
                + meminfo.get("SReclaimable", 0)
                - meminfo.get("Shmem", 0)
            )
        available = max(0, int(available_kib) * 1024)
    used = max(0, min(total, total - available))
    return {
        "used_bytes": used,
        "total_bytes": total,
        "percent": _clamp_percent((used / total) * 100.0),
    }


def _disk_usage() -> dict[str, int | float]:
    usage = shutil.disk_usage("/")
    total = int(usage.total)
    if total <= 0:
        raise RuntimeError("disk_unavailable")
    used = int(usage.used)
    return {
        "used_bytes": used,
        "total_bytes": total,
        "percent": _clamp_percent((used / total) * 100.0),
    }


def _safe_error(metric: str, exc: Exception) -> dict[str, str]:
    # Keep this intentionally coarse. Exception messages can contain local paths
    # on unusual platforms; the browser only needs a safe unavailable reason.
    return {"metric": metric, "code": type(exc).__name__}


_NET_MAX_BYTES_PER_SEC = 125_000_000  # ~1 Gbps reference for percent clamp
_NET_LOCK = threading.Lock()
_NET_PREV: dict[str, int] | None = None
_NET_PREV_TIME: float = 0.0


def _read_proc_net_dev() -> dict[str, int]:
    """Return {iface: total_bytes} from /proc/net/dev. Empty on non-Linux."""
    stats: dict[str, int] = {}
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                iface, _, rest = line.partition(":")
                iface = iface.strip()
                if iface == "lo":
                    continue
                parts = rest.split()
                if len(parts) < 9:
                    continue
                stats[iface] = int(parts[0]) + int(parts[8])
    except (OSError, ValueError):
        pass
    return stats


def _net_snapshot() -> tuple[float, float, float]:
    """Return (rx_bytes_per_sec, tx_bytes_per_sec, percent) with procfs or psutil fallback.

    Uses a lock to prevent concurrent requests from corrupting the global baseline.
    """
    global _NET_PREV, _NET_PREV_TIME
    now = time.time()

    with _NET_LOCK:
        current = _read_proc_net_dev()

        if not current:
            # No procfs data (macOS, containers without /proc) — try psutil
            try:
                psutil = _load_optional_psutil()
                io = psutil.net_io_counters()
                total = int(io.bytes_recv) + int(io.bytes_sent)
            except Exception:
                return (0.0, 0.0, 0.0)

            if _NET_PREV is None or now - _NET_PREV_TIME < 0.1:
                _NET_PREV = {"_psutil_total": total}
                _NET_PREV_TIME = now
                return (0.0, 0.0, 0.0)

            elapsed = now - _NET_PREV_TIME
            if elapsed <= 0:
                return (0.0, 0.0, 0.0)
            prev_total = _NET_PREV.get("_psutil_total", 0)
            total_delta = max(0, total - prev_total)
            _NET_PREV = {"_psutil_total": total}
            _NET_PREV_TIME = now
            percent = _clamp_percent((total_delta / elapsed / _NET_MAX_BYTES_PER_SEC) * 100.0)
            return (0.0, 0.0, percent)

        # Linux procfs path
        rx_total = sum(int(v) for v in current.values())
        tx_total = 0

        if _NET_PREV is None or now - _NET_PREV_TIME < 0.1:
            _NET_PREV = current
            _NET_PREV_TIME = now
            return (0.0, 0.0, 0.0)

        elapsed = now - _NET_PREV_TIME
        if elapsed <= 0:
            return (0.0, 0.0, 0.0)

        prev_total = sum(int(v) for v in _NET_PREV.values())
        total_delta = max(0, rx_total - prev_total)

        _NET_PREV = current
        _NET_PREV_TIME = now

        rx_per_sec = total_delta / elapsed
        percent = _clamp_percent((rx_per_sec / _NET_MAX_BYTES_PER_SEC) * 100.0)
        return (rx_per_sec, 0.0, percent)


_DISK_LOCK = threading.Lock()
_DISK_PREV: dict[str, int] | None = None
_DISK_PREV_TIME: float = 0.0


def _read_diskstats() -> dict[str, int]:
    """Return {device: sectors_read+written} from /proc/diskstats. Empty on non-Linux."""
    disks: dict[str, int] = {}
    try:
        with open("/proc/diskstats", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                # Skip partitions (only show whole disks)
                if name.startswith("loop") or name.startswith("sr"):
                    continue
                # sectors read = parts[5], sectors written = parts[9]
                sectors = int(parts[5]) + int(parts[9])
                disks[name] = sectors
    except (OSError, ValueError):
        pass
    return disks


def _disk_io() -> dict[str, int]:
    """Return {device: bytes_per_sec} for disk IO. Empty on non-Linux or first call."""
    global _DISK_PREV, _DISK_PREV_TIME
    now = time.time()

    with _DISK_LOCK:
        current = _read_diskstats()

        if not current:
            # No procfs data (macOS, containers without /proc)
            return {}

        if _DISK_PREV is None or now - _DISK_PREV_TIME < 0.1:
            _DISK_PREV = current
            _DISK_PREV_TIME = now
            return {}
        elapsed = now - _DISK_PREV_TIME
        if elapsed <= 0:
            return {}
        result = {}
        for name, sectors in current.items():
            if name in _DISK_PREV:
                delta = max(0, sectors - _DISK_PREV[name])
                # sectors are 512 bytes each
                result[name] = int((delta * 512) / elapsed)
        _DISK_PREV = current
        _DISK_PREV_TIME = now
        return result


def build_system_health_payload() -> dict[str, Any]:
    metrics: dict[str, Any] = {"cpu": None, "memory": None, "disk": None, "net": None}
    errors: list[dict[str, str]] = []

    # Collect network snapshot ONCE — returns (rx, tx, percent) atomically.
    # Calling _net_snapshot() twice in sequence would always zero the second
    # result due to the 0.1s baseline guard.
    net_rx, net_tx, net_percent = (0.0, 0.0, 0.0)
    net_ok = False
    try:
        net_rx, net_tx, net_percent = _net_snapshot()
        net_ok = True
    except Exception as exc:
        errors.append(_safe_error("net", exc))

    collectors = {
        "cpu": _cpu_percent,
        "memory": _memory_usage,
        "disk": _disk_usage,
    }
    for name, collect in collectors.items():
        try:
            value = collect()
            if name == "cpu":
                metrics[name] = {"percent": _clamp_percent(value)}
            else:
                metrics[name] = {
                    "used_bytes": max(0, int(value["used_bytes"])),
                    "total_bytes": max(0, int(value["total_bytes"])),
                    "percent": _clamp_percent(value["percent"]),
                }
        except Exception as exc:
            errors.append(_safe_error(name, exc))

    # Network metrics from the single snapshot above
    if net_ok:
        metrics["net"] = {"percent": _clamp_percent(net_percent)}
        if net_rx > 0 or net_tx > 0:
            metrics["net"]["rx_bytes_per_sec"] = net_rx
            metrics["net"]["tx_bytes_per_sec"] = net_tx

    # Additional metrics
    try:
        disk_io = _disk_io()
        if disk_io:
            total_io = sum(disk_io.values())
            if metrics["disk"]:
                metrics["disk"]["io_bytes_per_sec"] = total_io
            else:
                metrics["disk"] = {"io_bytes_per_sec": total_io}
    except Exception:
        pass

    available = any(metrics[name] is not None for name in metrics)
    status = "ok" if available and not errors else "partial" if available else "unavailable"
    return {
        "status": status,
        "available": available,
        "checked_at": _checked_at(),
        "cpu": metrics["cpu"],
        "memory": metrics["memory"],
        "disk": metrics["disk"],
        "net": metrics["net"],
        "errors": errors,
    }
