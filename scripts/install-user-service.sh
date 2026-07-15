#!/usr/bin/env bash
# Install Aegis as a systemd --user service.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="${ROOT}/systemd/aegis.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="${UNIT_DIR}/aegis.service"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found; install from https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "Syncing package…"
cd "$ROOT"
uv sync --all-extras
TOOL_BIN="$(uv tool dir --bin)"
AEGIS_BIN="${TOOL_BIN}/aegis"
# Include audio (+ porcupine when available) so the tool binary can capture mic
# and run wake — bare `uv tool install .` only pulls core deps.
if ! uv tool install --force --editable ".[audio,porcupine]" 2>/dev/null \
  || [[ ! -x "${AEGIS_BIN}" ]]; then
  # Fallback: ensure console script on PATH via uv run shim note
  mkdir -p "${TOOL_BIN}"
  cat > "${AEGIS_BIN}" <<EOF
#!/usr/bin/env bash
exec uv run --directory "${ROOT}" --all-extras aegis "\$@"
EOF
  chmod +x "${AEGIS_BIN}"
  echo "Installed wrapper at ${AEGIS_BIN}"
fi
# Soft check: warn if sounddevice is missing from the installed tool env
if ! python3 -c "import sounddevice" 2>/dev/null; then
  # Prefer the tool's python if present
  if ! uv run --directory "$ROOT" python -c "import sounddevice" 2>/dev/null; then
    echo "warning: sounddevice not importable — mic/wake need: uv sync --extra audio" >&2
  fi
fi

mkdir -p "$UNIT_DIR"
if [[ ! -x "${AEGIS_BIN}" ]]; then
  echo "aegis executable was not installed at ${AEGIS_BIN}" >&2
  exit 1
fi

sed "s|ExecStart=.*|ExecStart=${AEGIS_BIN} daemon|" "$UNIT_SRC" > "$UNIT_DST"
echo "Wrote $UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable aegis.service
echo "Enabled aegis.service (user)."
echo "Start with:  systemctl --user start aegis"
echo "Status:      systemctl --user status aegis"
echo "Logs:        journalctl --user -u aegis -f"
echo
echo "Ensure OPENAI_API_KEY is available to user services, e.g.:"
echo "  mkdir -p ~/.config/environment.d"
echo "  echo 'OPENAI_API_KEY=sk-...' >> ~/.config/environment.d/aegis.conf"
echo "  systemctl --user import-environment OPENAI_API_KEY  # or re-login"
