# Proposal: Always-on daemon dogfood polish

## Intent

Core daemon/IPC/wake spine exists, but real-desk dogfood (PortAudio/PipeWire, systemd autostart, reliable wake → session) needs polishing so daily use is practical.

## Scope

**In scope**
- Audio device selection robustness on PipeWire
- systemd user unit install reliability
- End-to-end wake → session → teardown on real hardware
- Clear doctor failures for missing audio/wake deps

**Out of scope**
- Multi-device ambient mesh (Phase 3)
- Tray UI (separate change)

## Approach

Iterate on `aegis daemon` + real mic with mock and then realtime/ollama backends; fix device defaults; document Pop!_OS/Wayland activation.

## Impact

Raises confidence that non-negotiable idle privacy holds under real load; unblocks daily use.
