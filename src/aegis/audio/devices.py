"""Audio device discovery."""

from __future__ import annotations

from dataclasses import dataclass

from aegis.util.logging import get_logger

log = get_logger("audio.devices")


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int | None
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    hostapi: str = ""


def sounddevice_available() -> bool:
    try:
        import sounddevice  # noqa: F401

        return True
    except Exception:
        return False


def list_devices() -> list[AudioDevice]:
    """List host audio devices. Empty list if sounddevice is unavailable."""
    try:
        import sounddevice as sd
    except Exception as exc:
        log.debug("sounddevice unavailable: %s", exc)
        return []

    devices: list[AudioDevice] = []
    try:
        hostapis = sd.query_hostapis()
        for idx, info in enumerate(sd.query_devices()):
            host = ""
            try:
                host = str(hostapis[info["hostapi"]]["name"])
            except Exception:
                pass
            devices.append(
                AudioDevice(
                    index=idx,
                    name=str(info.get("name", f"device-{idx}")),
                    max_input_channels=int(info.get("max_input_channels", 0)),
                    max_output_channels=int(info.get("max_output_channels", 0)),
                    default_samplerate=float(info.get("default_samplerate", 0.0)),
                    hostapi=host,
                )
            )
    except Exception as exc:
        log.warning("failed to query audio devices: %s", exc)
        return []
    return devices


def default_input_device() -> AudioDevice | None:
    try:
        import sounddevice as sd

        idx = sd.default.device[0]
        if idx is None or idx < 0:
            return None
        info = sd.query_devices(idx)
        return AudioDevice(
            index=int(idx),
            name=str(info["name"]),
            max_input_channels=int(info["max_input_channels"]),
            max_output_channels=int(info["max_output_channels"]),
            default_samplerate=float(info["default_samplerate"]),
        )
    except Exception:
        return None


def default_output_device() -> AudioDevice | None:
    try:
        import sounddevice as sd

        idx = sd.default.device[1]
        if idx is None or idx < 0:
            return None
        info = sd.query_devices(idx)
        return AudioDevice(
            index=int(idx),
            name=str(info["name"]),
            max_input_channels=int(info["max_input_channels"]),
            max_output_channels=int(info["max_output_channels"]),
            default_samplerate=float(info["default_samplerate"]),
        )
    except Exception:
        return None
