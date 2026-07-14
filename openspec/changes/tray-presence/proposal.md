# Proposal: System tray presence

## Intent

DESIGN Phase 1 calls for a tray client for session state, mute, quit, and open logs. CLI disclosure exists; tray is still optional/stub-level.

## Scope

**In scope**
- Tray icon states: idle / connecting / active / approval pending
- Actions: mute mic uplink, end session, quit daemon, open logs/settings
- Thin IPC client (no policy logic in tray)

**Out of scope**
- Full settings redesign inside tray
- Multi-user remote tray

## Approach

Use a Linux-friendly tray library compatible with Wayland/X11 best-effort; degrade to CLI-only when tray APIs are unavailable.

## Impact

Improves always-on visibility and approval UX; no change to tool policy engine.
