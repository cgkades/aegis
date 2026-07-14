#!/usr/bin/env bash
# Diagnose YubiKey visibility for Firefox WebAuthn / FIDO2 on Linux.
set -euo pipefail

echo "=== 1) Is a YubiKey plugged in right now? ==="
if lsusb | grep -qiE 'yubi|1050:'; then
  lsusb | grep -iE 'yubi|1050:'
else
  echo "NO YubiKey seen by lsusb. Plug it in and re-run this script."
fi
echo

echo "=== 2) Kernel messages (last 30 min) ==="
journalctl -k --since "30 min ago" --no-pager 2>/dev/null | grep -iE 'yubi|1050|fido|u2f|ccid' | tail -20 || true
echo

echo "=== 3) hidraw devices + ACLs (user must have access) ==="
for n in /dev/hidraw*; do
  [ -e "$n" ] || continue
  echo "-- $n"
  ls -la "$n"
  getfacl -p "$n" 2>/dev/null || true
  udevadm info -q property -n "$n" 2>/dev/null | grep -E 'ID_SECURITY_TOKEN|ID_FIDO|ID_VENDOR|ID_MODEL|TAGS|DEVNAME' || true
  echo
done
echo

echo "=== 4) Packages ==="
for p in libyubikey-udev libu2f-udev pcscd libccid fido2-tools yubikey-manager libfido2-1; do
  if dpkg -s "$p" >/dev/null 2>&1; then
    echo "OK  $p"
  else
    echo "MISS $p"
  fi
done
echo

echo "=== 5) pcscd (needed for PIV/CCID, not pure FIDO) ==="
systemctl is-active pcscd 2>/dev/null || echo "pcscd inactive or not installed"
echo

echo "=== 6) FIDO list (if fido2-tools installed) ==="
if command -v fido2-token >/dev/null; then
  fido2-token -L || true
else
  echo "fido2-token not installed (sudo apt install fido2-tools)"
fi
echo

echo "=== 7) Firefox ==="
which firefox || true
echo "Session: ${XDG_SESSION_TYPE:-unknown}"
echo
echo "Done. For WebAuthn in Firefox you need: device present + ID_SECURITY_TOKEN=1 on hidraw + uaccess ACL for your user."
