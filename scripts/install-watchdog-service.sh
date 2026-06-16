#!/usr/bin/env bash
#
# install-watchdog-service installs the WiFi Watchdog daemon and systemd unit.
# Copyright (C) 2026  TuDo Makerspace, Patrick Pedersen <ctx.xda@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SCRIPT_SRC="${ROOT_DIR}/daemon/wifi-watchdog.py"
SERVICE_SRC="${ROOT_DIR}/systemd/wifi-watchdog.service"
ENV_SRC="${ROOT_DIR}/systemd/wifi-watchdog.env.example"
README_SRC="${ROOT_DIR}/README.md"

SCRIPT_DST="/usr/local/sbin/wifi-watchdog.py"
SERVICE_DST="/etc/systemd/system/wifi-watchdog.service"
ENV_DST="/etc/wifi-watchdog.env"
DOC_DIR="/usr/local/share/wifi-watchdog"
README_DST="${DOC_DIR}/README.md"

SERVICE_NAME="wifi-watchdog.service"
OLD_SERVICE_NAME="wifi-stick-killer.service"
OLD_ENV_DST="/etc/wifi-stick-killer.env"

START_SERVICE=0
ENABLE_SERVICE=0

usage() {
  cat <<EOF
Usage: sudo $0 [--start] [--enable]

Installs or updates the WiFi watchdog service.

Default behavior:
  - installs/updates the watchdog script
  - installs/updates the systemd unit
  - creates /etc/wifi-watchdog.env if missing
  - preserves existing /etc/wifi-watchdog.env values on update
  - migrates values from /etc/wifi-stick-killer.env if the old env exists
  - stops/disables ${OLD_SERVICE_NAME} if it was active/enabled
  - reloads systemd
  - restarts ${SERVICE_NAME} only if it is already running
  - does not enable or start the service unless requested

Options:
  --start    Start the service after installation if it is not already running.
  --enable   Enable the service at boot.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start)
      START_SERVICE=1
      ;;
    --enable)
      ENABLE_SERVICE=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

if [[ "$(id -u)" != "0" ]]; then
  echo "error: run as root, for example: sudo ./scripts/install-watchdog-service.sh" >&2
  exit 1
fi

for path in "${SCRIPT_SRC}" "${SERVICE_SRC}" "${ENV_SRC}" "${README_SRC}"; do
  if [[ ! -f "${path}" ]]; then
    echo "error: missing required file: ${path}" >&2
    exit 1
  fi
done

existing_value() {
  local key="$1"
  local file="$2"

  if [[ ! -f "${file}" ]]; then
    return 1
  fi

  awk -F= -v key="${key}" '
    $1 == key {
      sub(/^[^=]*=/, "")
      print
      found = 1
      exit
    }
    END {
      if (!found) {
        exit 1
      }
    }
  ' "${file}"
}

default_value() {
  local key="$1"
  awk -F= -v key="${key}" '
    $1 == key {
      sub(/^[^=]*=/, "")
      print
      found = 1
      exit
    }
    END {
      if (!found) {
        exit 1
      }
    }
  ' "${ENV_SRC}"
}

merged_env_value() {
  local key="$1"
  existing_value "${key}" "${ENV_DST}" || existing_value "${key}" "${OLD_ENV_DST}" || default_value "${key}"
}

merged_new_env_or_default() {
  local key="$1"
  existing_value "${key}" "${ENV_DST}" || default_value "${key}"
}

merged_serial_value() {
  existing_value RELAY_SERIAL "${ENV_DST}" ||
    existing_value RELAY_SERIAL "${OLD_ENV_DST}" ||
    existing_value STICK_KILLER_SERIAL "${ENV_DST}" ||
    existing_value STICK_KILLER_SERIAL "${OLD_ENV_DST}" ||
    default_value RELAY_SERIAL
}

write_env_file() {
  local tmp
  tmp="$(mktemp)"

  {
    echo "WIFI_STICK_IFACE=$(merged_env_value WIFI_STICK_IFACE)"
    echo "RELAY_SERIAL=$(merged_serial_value)"
    echo "CHECK_INTERVAL=$(merged_env_value CHECK_INTERVAL)"
    echo "FAILURE_WINDOW=$(merged_env_value FAILURE_WINDOW)"
    echo "MIN_SCAN_FAILURES=$(merged_env_value MIN_SCAN_FAILURES)"
    echo "MIN_STALE_EVENTS=$(merged_env_value MIN_STALE_EVENTS)"
    echo "UNHEALTHY_TIMEOUT=$(merged_env_value UNHEALTHY_TIMEOUT)"
    echo "SOFT_RECOVERY_COOLDOWN=$(merged_env_value SOFT_RECOVERY_COOLDOWN)"
    echo "RECOVERY_COOLDOWN=$(merged_env_value RECOVERY_COOLDOWN)"
    echo "COMMAND_TIMEOUT=$(merged_env_value COMMAND_TIMEOUT)"
    echo "READY_TIMEOUT=$(merged_env_value READY_TIMEOUT)"
    echo "READY_POLL_INTERVAL=$(merged_env_value READY_POLL_INTERVAL)"
    echo "SPOOF_DEAD_FILE=$(merged_new_env_or_default SPOOF_DEAD_FILE)"
    echo "STATE_DIR=$(merged_new_env_or_default STATE_DIR)"
  } > "${tmp}"

  install -m 0644 "${tmp}" "${ENV_DST}"
  rm -f "${tmp}"
}

SERVICE_WAS_ACTIVE=0
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
  SERVICE_WAS_ACTIVE=1
fi

OLD_SERVICE_WAS_ACTIVE=0
if systemctl is-active --quiet "${OLD_SERVICE_NAME}" 2>/dev/null; then
  OLD_SERVICE_WAS_ACTIVE=1
fi

OLD_SERVICE_WAS_ENABLED=0
if systemctl is-enabled --quiet "${OLD_SERVICE_NAME}" 2>/dev/null; then
  OLD_SERVICE_WAS_ENABLED=1
fi

install -m 0755 "${SCRIPT_SRC}" "${SCRIPT_DST}"
install -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
install -d -m 0755 "${DOC_DIR}"
install -m 0644 "${README_SRC}" "${README_DST}"

if [[ -f "${ENV_DST}" ]]; then
  backup="${ENV_DST}.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "${ENV_DST}" "${backup}"
  echo "Existing ${ENV_DST} backed up to ${backup}"
elif [[ -f "${OLD_ENV_DST}" ]]; then
  backup="${OLD_ENV_DST}.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "${OLD_ENV_DST}" "${backup}"
  echo "Old ${OLD_ENV_DST} backed up to ${backup}"
fi
write_env_file

systemctl daemon-reload

if [[ "${OLD_SERVICE_WAS_ACTIVE}" == "1" ]]; then
  systemctl stop "${OLD_SERVICE_NAME}"
  echo "Stopped old ${OLD_SERVICE_NAME}"
fi

if [[ "${OLD_SERVICE_WAS_ENABLED}" == "1" ]]; then
  systemctl disable "${OLD_SERVICE_NAME}"
  echo "Disabled old ${OLD_SERVICE_NAME}"
fi

if [[ "${SERVICE_WAS_ACTIVE}" == "1" || "${OLD_SERVICE_WAS_ACTIVE}" == "1" ]]; then
  systemctl restart "${SERVICE_NAME}"
  SERVICE_ACTION="restarted because the watchdog was already running"
elif [[ "${START_SERVICE}" == "1" ]]; then
  systemctl start "${SERVICE_NAME}"
  SERVICE_ACTION="started"
else
  SERVICE_ACTION="not started"
fi

if [[ "${ENABLE_SERVICE}" == "1" || "${OLD_SERVICE_WAS_ENABLED}" == "1" ]]; then
  systemctl enable "${SERVICE_NAME}"
  ENABLE_ACTION="enabled"
else
  ENABLE_ACTION="unchanged"
fi

echo "Installed:"
echo "  ${SCRIPT_DST}"
echo "  ${SERVICE_DST}"
echo "  ${ENV_DST}"
echo "  ${README_DST}"
echo
echo "Service state:"
echo "  ${SERVICE_NAME}: ${SERVICE_ACTION}"
echo "  boot enablement: ${ENABLE_ACTION}"
echo
echo "Recommended checks:"
echo "  ls -l /dev/stick-killer"
echo "  sudo ${SCRIPT_DST} --iface wlx40a5efd86cc5 --serial /dev/stick-killer --once --dry-run --verbose"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo
echo "To start or enable later:"
echo "  sudo systemctl start ${SERVICE_NAME}"
echo "  sudo systemctl enable ${SERVICE_NAME}"
