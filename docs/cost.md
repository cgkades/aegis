# Aegis cost controls

## Idle

$0 API — local wake word only.

## Active session drivers

1. Audio input/output tokens (Realtime)
2. Context accumulation across long incidents
3. Silence uplink (mitigated by local VAD gate)

## Controls

| Control | Config |
| --- | --- |
| Model tier | `session.model` (`…-mini` cheaper) |
| Session cost cap | `session.max_session_cost_usd` |
| Max duration | `session.max_duration_s` |
| Idle timeout | `session.idle_timeout_s` |
| Local VAD | `audio.local_vad_enabled = true` |
| Context retention | `session.context.*` |
| Confirm speech after wake | `wake.confirm_speech_timeout_s` |

## Provisional rates

Estimates use published list prices and are **approximate**.  
Session end prints `cost~$…`. Calibrate with live usage events.

```bash
uv run aegis session once --backend realtime --max-seconds 60
```
