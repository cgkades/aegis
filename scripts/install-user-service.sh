#!/usr/bin/env bash
# Install Aegis as a systemd --user service.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="${ROOT}/systemd/aegis.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="${UNIT_DIR}/aegis.service"

export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found; install from https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "Syncing package…"
cd "$ROOT"
uv sync --all-extras
uv tool install --force --editable . 2>/dev/null || {
  # Fallback: ensure console script on PATH via uv run shim note
  mkdir -p "${HOME}/.local/bin"
  cat > "${HOME}/.local/bin/aegis" <<EOF
#!/usr/bin/env bash
exec uv run --directory "${ROOT}" aegis "\$@"
EOF
  chmod +x "${HOME}/.local/bin/aegis"
  echo "Installed wrapper at ~/.local/bin/aegis"
}

mkdir -p "$UNIT_DIR"
# Rewrite ExecStart to absolute aegis if available
AEGIS_BIN="$(command -v aegis || true)"
if [[ -z "${AEGIS_BIN}" ]]; then
  AEGIS_BIN="${HOME}/.local/bin/aegis"
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
