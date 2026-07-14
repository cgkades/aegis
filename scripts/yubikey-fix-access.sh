#!/usr/bin/env bash
# Install packages + udev rules so the console user can use YubiKey with Firefox.
# Requires sudo password.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-running with sudo…"
  exec sudo "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# FIDO/WebAuthn access + management tools + smartcard stack (for NEO CCID/PIV)
apt-get install -y \
  libyubikey-udev \
  libu2f-udev \
  fido2-tools \
  yubikey-manager \
  pcscd \
  libccid \
  scdaemon \
  opensc

# Explicit udev rule covering product IDs seen on this machine (NEO 0115, Security Key 0120)
# Modern systems also use fido_id → ID_SECURITY_TOKEN → uaccess; this is belt-and-suspenders.
cat >/etc/udev/rules.d/70-yubikey-local.rules <<'EOF'
# Local YubiKey / Security Key access for WebAuthn + management tools
ACTION!="add|change", GOTO="yubikey_local_end"

# All Yubico USB devices (vendor 1050)
SUBSYSTEM=="usb", ATTR{idVendor}=="1050", TAG+="uaccess", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1050", TAG+="uaccess"

# HID interface nodes used by FIDO2/U2F
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1050", TAG+="uaccess", GROUP="plugdev", MODE="0660"
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1050", ENV{ID_SECURITY_TOKEN}="1"

# Known product IDs observed on this host
# 0115 = YubiKey NEO U2F+CCID
# 0120 = Security Key by Yubico
ATTRS{idVendor}=="1050", ATTRS{idProduct}=="0115|0120|0010|0110|0111|0114|0116|0401|0403|0405|0407|0410|0402|0404|0406|0408|0409|0411", ENV{ID_SECURITY_TOKEN}="1"

LABEL="yubikey_local_end"
EOF

udevadm control --reload-rules
udevadm trigger

systemctl enable --now pcscd.socket 2>/dev/null || systemctl enable --now pcscd 2>/dev/null || true

echo
echo "Installed. Unplug and re-plug the YubiKey, then run:"
echo "  lsusb | grep -i yubi"
echo "  fido2-token -L"
echo "  ./scripts/yubikey-diagnose.sh"
echo
echo "Then restart Firefox completely (all windows) and test:"
echo "  https://webauthn.io/  or  https://demo.yubico.com/webauthn-technical/"
