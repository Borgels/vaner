"""Hardware detection for Vaner setup wizard.

Read-only, fail-safe probes that build a :class:`HardwareProfile` snapshot of
the local machine and map it to a :data:`HardwareTier` for policy-bundle
selection (spec §6.1).

Every probe is wrapped so that a missing dependency, a non-zero subprocess
exit, a timeout, or unrecognised output yields a sensible default rather than
raising. This module must import cleanly on any supported platform.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib import request as urlrequest
from urllib.error import URLError

from vaner.setup.enums import HardwareTier

OS = Literal["linux", "darwin", "windows"]
CPUClass = Literal["low", "mid", "high"]
GPU = Literal["none", "integrated", "nvidia", "amd", "apple_silicon"]
Runtime = Literal["ollama", "llama.cpp", "lmstudio", "vllm", "mlx"]

_RUNTIMES: tuple[Runtime, ...] = ("ollama", "llama.cpp", "lmstudio", "vllm", "mlx")

_HTTP_TIMEOUT = 1.0
_SUBPROCESS_TIMEOUT = 2.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Immutable snapshot of detected hardware capabilities.

    The ``tier`` field is computed once at construction time so consumers do
    not need to re-derive it.
    """

    os: OS
    cpu_class: CPUClass
    ram_gb: int
    gpu: GPU
    gpu_vram_gb: int | None
    is_battery: bool
    thermal_constrained: bool
    detected_runtimes: tuple[Runtime, ...]
    detected_models: tuple[tuple[str, str, str], ...]
    tier: HardwareTier = field(default="unknown")


# ---------------------------------------------------------------------------
# OS / CPU / RAM probes
# ---------------------------------------------------------------------------


def _probe_os() -> OS | None:
    """Return the host OS or ``None`` if it cannot be classified."""
    try:
        plat = sys.platform
        if plat.startswith("linux"):
            return "linux"
        if plat == "darwin":
            return "darwin"
        if plat in {"win32", "cygwin"}:
            return "windows"
        # Fall back to platform.system() spelling.
        sysname = platform.system().lower()
        if sysname == "linux":
            return "linux"
        if sysname == "darwin":
            return "darwin"
        if sysname == "windows":
            return "windows"
    except Exception:  # pragma: no cover - defensive
        logger.debug("os probe failed", exc_info=True)
    return None


def _read_meminfo_gb() -> int | None:
    """Parse Linux ``/proc/meminfo`` for total RAM in GB. Best-effort."""
    try:
        text = Path("/proc/meminfo").read_text()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                # MemTotal:       16327208 kB
                parts = line.split()
                kb = int(parts[1])
                return max(1, round(kb / (1024 * 1024)))
    except Exception:
        logger.debug("/proc/meminfo probe failed", exc_info=True)
    return None


def _sysctl_memsize_gb() -> int | None:
    """Run ``sysctl hw.memsize`` (macOS) and convert to GB. Best-effort."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            return None
        value = int(result.stdout.strip())
        return max(1, round(value / (1024**3)))
    except Exception:
        logger.debug("sysctl hw.memsize failed", exc_info=True)
        return None


def _probe_cpu_and_ram() -> tuple[CPUClass, int]:
    """Return CPU class + RAM in GB.

    Heuristic for ``cpu_class`` (documented):

    * ``low``  — fewer than 4 logical CPUs **or** less than 8 GB RAM.
    * ``high`` — 12+ logical CPUs **and** 32+ GB RAM.
    * ``mid``  — everything in between.

    On total probe failure both return ``"low"`` / ``0``; the tier mapper then
    treats the resulting profile as ``unknown``.
    """
    logical_cpus: int | None = None
    ram_gb: int | None = None

    try:
        import psutil  # type: ignore[import-untyped]

        logical_cpus = psutil.cpu_count(logical=True)
        try:
            vmem = psutil.virtual_memory()
            ram_gb = max(1, round(vmem.total / (1024**3)))
        except Exception:
            logger.debug("psutil.virtual_memory failed", exc_info=True)
    except ImportError:
        logger.debug("psutil unavailable; falling back to stdlib")
    except Exception:
        logger.debug("psutil unavailable / errored", exc_info=True)

    if logical_cpus is None:
        try:
            logical_cpus = os.cpu_count()
        except Exception:
            logical_cpus = None

    if ram_gb is None:
        plat = sys.platform
        if plat.startswith("linux"):
            ram_gb = _read_meminfo_gb()
        elif plat == "darwin":
            ram_gb = _sysctl_memsize_gb()
        # Windows without psutil: leave ram_gb as None → 0 below.

    cpu_count = logical_cpus or 0
    ram = ram_gb or 0

    if cpu_count == 0 and ram == 0:
        return "low", 0

    cpu_class: CPUClass
    if cpu_count >= 12 and ram >= 32:
        cpu_class = "high"
    elif cpu_count < 4 or ram < 8:
        cpu_class = "low"
    else:
        cpu_class = "mid"
    return cpu_class, ram


# ---------------------------------------------------------------------------
# GPU probe
# ---------------------------------------------------------------------------


def _probe_gpu_nvidia() -> tuple[GPU, int | None] | None:
    """Use pynvml when available to identify NVIDIA GPUs + VRAM."""
    try:
        import pynvml  # type: ignore[import-not-found]
    except ImportError:
        return None
    except Exception:
        logger.debug("pynvml import failed", exc_info=True)
        return None

    try:
        pynvml.nvmlInit()
        try:
            count = pynvml.nvmlDeviceGetCount()
            if count <= 0:
                return None
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            vram_gb = max(1, round(mem.total / (1024**3)))
            return "nvidia", vram_gb
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:  # pragma: no cover - cleanup
                logger.debug("nvmlShutdown failed", exc_info=True)
    except Exception:
        logger.debug("pynvml probe failed", exc_info=True)
        return None


def _probe_gpu_macos() -> tuple[GPU, int | None]:
    """macOS: ``system_profiler SPDisplaysDataType -json`` parsing."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return "none", None
        data = json.loads(result.stdout)
        displays = data.get("SPDisplaysDataType", [])
        if not isinstance(displays, list) or not displays:
            return "none", None
        first = displays[0]
        vendor = (first.get("spdisplays_vendor") or "").lower()
        name = (first.get("sppci_model") or "").lower()
        if "apple" in vendor or "apple" in name:
            # Apple Silicon → unified memory; no separate VRAM number.
            return "apple_silicon", None
        if "nvidia" in vendor or "nvidia" in name:
            return "nvidia", None
        if "amd" in vendor or "amd" in name or "radeon" in name:
            return "amd", None
        if "intel" in vendor or "intel" in name:
            return "integrated", None
        return "integrated", None
    except Exception:
        logger.debug("system_profiler GPU probe failed", exc_info=True)
        return "none", None


def _probe_gpu_linux() -> tuple[GPU, int | None]:
    """Linux fallback: ``lspci`` for VGA-class controllers."""
    if not shutil.which("lspci"):
        return "none", None
    try:
        result = subprocess.run(
            ["lspci"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            return "none", None
        text = result.stdout.lower()
        # Filter to VGA / 3D / Display controller lines.
        gpu_lines = [line for line in text.splitlines() if "vga" in line or "3d controller" in line or "display controller" in line]
        if not gpu_lines:
            return "none", None
        joined = " ".join(gpu_lines)
        if "nvidia" in joined:
            return "nvidia", None
        if "amd" in joined or "advanced micro devices" in joined or "radeon" in joined:
            return "amd", None
        if "intel" in joined:
            return "integrated", None
        return "integrated", None
    except Exception:
        logger.debug("lspci probe failed", exc_info=True)
        return "none", None


def _probe_gpu() -> tuple[GPU, int | None]:
    """Compose GPU probes; never raises."""
    try:
        # NVIDIA via pynvml is the most reliable signal when present.
        nvidia = _probe_gpu_nvidia()
        if nvidia is not None:
            return nvidia
        plat = sys.platform
        if plat == "darwin":
            return _probe_gpu_macos()
        if plat.startswith("linux"):
            return _probe_gpu_linux()
        return "none", None
    except Exception:
        logger.debug("GPU probe failed", exc_info=True)
        return "none", None


# ---------------------------------------------------------------------------
# Battery / thermal
# ---------------------------------------------------------------------------


def _probe_battery() -> bool:
    """Return True when a battery is present (laptop / portable)."""
    try:
        import psutil

        try:
            batt = psutil.sensors_battery()
        except Exception:
            batt = None
        if batt is not None:
            return True
    except ImportError:
        pass
    except Exception:
        logger.debug("psutil battery probe failed", exc_info=True)

    if sys.platform.startswith("linux"):
        try:
            power = Path("/sys/class/power_supply")
            if power.exists():
                for entry in power.iterdir():
                    if entry.name.upper().startswith("BAT"):
                        return True
        except Exception:
            logger.debug("/sys/class/power_supply probe failed", exc_info=True)

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
                check=False,
            )
            if result.returncode == 0 and "InternalBattery" in result.stdout:
                return True
        except Exception:
            logger.debug("pmset battery probe failed", exc_info=True)

    return False


def _probe_thermal() -> bool:
    """Best-effort thermal pressure hint.

    Linux: read ``/sys/class/thermal/thermal_zone*/temp``; flag if any zone
    reports above 85 °C. Returns False on every other platform / probe error.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        base = Path("/sys/class/thermal")
        if not base.exists():
            return False
        for zone in base.glob("thermal_zone*"):
            temp_file = zone / "temp"
            try:
                raw = temp_file.read_text().strip()
                # Sysfs reports millidegrees Celsius.
                centi = int(raw)
                celsius = centi / 1000.0
                if celsius >= 85.0:
                    return True
            except Exception:
                continue
    except Exception:
        logger.debug("thermal probe failed", exc_info=True)
    return False


# ---------------------------------------------------------------------------
# Runtime + model probes
# ---------------------------------------------------------------------------


def _http_get_ok(url: str, timeout: float = _HTTP_TIMEOUT) -> tuple[bool, str | None]:
    """GET ``url`` and return ``(ok, body)``. ``ok`` is True iff status == 200."""
    try:
        req = urlrequest.Request(url, method="GET")
        with urlrequest.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - localhost only
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return False, None
            try:
                body = resp.read().decode("utf-8", errors="replace")
            except Exception:
                body = None
            return True, body
    except (URLError, TimeoutError, OSError):
        return False, None
    except Exception:
        logger.debug("HTTP probe failed for %s", url, exc_info=True)
        return False, None


def _probe_ollama() -> bool:
    if shutil.which("ollama"):
        return True
    ok, _ = _http_get_ok("http://127.0.0.1:11434/api/tags")
    return ok


def _probe_llama_cpp() -> bool:
    return bool(shutil.which("llama-server") or shutil.which("llama-cpp-server"))


def _probe_lmstudio() -> bool:
    ok, _ = _http_get_ok("http://127.0.0.1:1234/v1/models")
    return ok


def _probe_vllm() -> bool:
    return bool(shutil.which("vllm"))


def _probe_mlx() -> bool:
    if sys.platform != "darwin":
        return False
    if shutil.which("mlx_lm.generate"):
        return True
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import mlx_lm"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        logger.debug("mlx import probe failed", exc_info=True)
        return False


def _probe_runtimes() -> tuple[Runtime, ...]:
    """Return the tuple of locally detected runtimes. Never raises."""
    found: list[Runtime] = []
    probes: dict[Runtime, Callable[[], bool]] = {
        "ollama": _probe_ollama,
        "llama.cpp": _probe_llama_cpp,
        "lmstudio": _probe_lmstudio,
        "vllm": _probe_vllm,
        "mlx": _probe_mlx,
    }
    for name in _RUNTIMES:
        try:
            if probes[name]():
                found.append(name)
        except Exception:
            logger.debug("runtime probe %s failed", name, exc_info=True)
    return tuple(found)


def _label_size(size_bytes: int | None) -> str:
    if not size_bytes or size_bytes <= 0:
        return "unknown"
    gb = size_bytes / (1024**3)
    if gb < 1:
        return f"{round(size_bytes / (1024**2))}MB"
    return f"{gb:.1f}GB"


def _probe_models_ollama() -> list[tuple[str, str, str]]:
    ok, body = _http_get_ok("http://127.0.0.1:11434/api/tags")
    if not ok or not body:
        return []
    try:
        data = json.loads(body)
    except Exception:
        return []
    rows: list[tuple[str, str, str]] = []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model")
        size = entry.get("size")
        if not isinstance(name, str):
            continue
        size_label = _label_size(size if isinstance(size, int) else None)
        rows.append(("ollama", name, size_label))
    return rows


def _probe_models_lmstudio() -> list[tuple[str, str, str]]:
    ok, body = _http_get_ok("http://127.0.0.1:1234/v1/models")
    if not ok or not body:
        return []
    try:
        data = json.loads(body)
    except Exception:
        return []
    rows: list[tuple[str, str, str]] = []
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str):
            continue
        # LM Studio's /v1/models doesn't expose size; leave label as unknown.
        rows.append(("lmstudio", model_id, "unknown"))
    return rows


def _probe_models(runtimes: Sequence[str]) -> tuple[tuple[str, str, str], ...]:
    """Best-effort model enumeration for runtimes that expose a list endpoint."""
    rows: list[tuple[str, str, str]] = []
    if "ollama" in runtimes:
        try:
            rows.extend(_probe_models_ollama())
        except Exception:
            logger.debug("ollama model probe failed", exc_info=True)
    if "lmstudio" in runtimes:
        try:
            rows.extend(_probe_models_lmstudio())
        except Exception:
            logger.debug("lmstudio model probe failed", exc_info=True)
    return tuple(rows)


# ---------------------------------------------------------------------------
# Tier mapping
# ---------------------------------------------------------------------------


def tier_for(profile: HardwareProfile) -> HardwareTier:
    """Map a :class:`HardwareProfile` to a :data:`HardwareTier` (spec §6.1).

    Rules (evaluated in order):

    * ``unknown`` — RAM probe returned 0 GB (the OS probe is required for
      construction but RAM == 0 is the strongest "we couldn't read anything"
      signal).
    * ``light`` — ``ram_gb < 16`` **or** (``gpu == "none"`` and ``cpu_class == "low"``)
      **or** (``is_battery`` and ``ram_gb < 24``).
    * ``high_performance`` — discrete or unified GPU
      (``gpu in {"nvidia", "apple_silicon"}``) with ``gpu_vram_gb`` either
      unknown (Apple Silicon shares RAM) or ``>= 16``, ``ram_gb >= 32``, and
      not on battery.
    * ``capable`` — everything else.
    """
    if profile.ram_gb <= 0:
        return "unknown"
    if profile.ram_gb < 16:
        return "light"
    if profile.gpu == "none" and profile.cpu_class == "low":
        return "light"
    if profile.is_battery and profile.ram_gb < 24:
        return "light"
    if (
        profile.gpu in ("nvidia", "apple_silicon")
        and (profile.gpu_vram_gb is None or profile.gpu_vram_gb >= 16)
        and profile.ram_gb >= 32
        and not profile.is_battery
    ):
        return "high_performance"
    return "capable"


# ---------------------------------------------------------------------------
# Top-level detect()
# ---------------------------------------------------------------------------


def detect() -> HardwareProfile:
    """Compose every probe and return a frozen :class:`HardwareProfile`.

    Pure modulo system probes; no caching at module level. The daemon decides
    when to cache (typically once at startup).
    """
    os_kind = _probe_os()
    cpu_class, ram_gb = _probe_cpu_and_ram()
    gpu, gpu_vram_gb = _probe_gpu()
    is_battery = _probe_battery()
    thermal = _probe_thermal()
    runtimes = _probe_runtimes()
    models = _probe_models(runtimes)

    # When the OS probe fails entirely we still need a literal value for the
    # frozen dataclass; fall back to "linux" but force the tier to "unknown"
    # so consumers treat the snapshot as untrusted.
    effective_os: OS = os_kind if os_kind is not None else "linux"

    profile = HardwareProfile(
        os=effective_os,
        cpu_class=cpu_class,
        ram_gb=ram_gb,
        gpu=gpu,
        gpu_vram_gb=gpu_vram_gb,
        is_battery=is_battery,
        thermal_constrained=thermal,
        detected_runtimes=runtimes,
        detected_models=models,
        tier="unknown",
    )
    final_tier: HardwareTier = "unknown" if os_kind is None else tier_for(profile)
    return HardwareProfile(
        os=effective_os,
        cpu_class=cpu_class,
        ram_gb=ram_gb,
        gpu=gpu,
        gpu_vram_gb=gpu_vram_gb,
        is_battery=is_battery,
        thermal_constrained=thermal,
        detected_runtimes=runtimes,
        detected_models=models,
        tier=final_tier,
    )


__all__ = [
    "HardwareProfile",
    "HardwareTier",
    "detect",
    "tier_for",
]
