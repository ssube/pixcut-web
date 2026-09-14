#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root: sudo bash scripts/install-udev-rule.sh" >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_rule="${script_dir}/../config/udev/70-pixcut-s1.rules"
target_rule="/etc/udev/rules.d/70-pixcut-s1.rules"

getent group plugdev >/dev/null || {
  echo "The plugdev group does not exist on this host." >&2
  exit 1
}

install -D -m 0644 "${source_rule}" "${target_rule}"
udevadm control --reload-rules
udevadm trigger \
  --action=change \
  --subsystem-match=usb \
  --attr-match=idVendor=302c \
  --attr-match=idProduct=3101 \
  --settle

echo "Installed ${target_rule} and reapplied it to connected PixCut devices."
