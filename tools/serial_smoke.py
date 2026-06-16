#!/usr/bin/env python3
#
# serial_smoke.py runs serial smoke tests against WiFi Watchdog firmware.
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

import argparse
import sys
import time

import serial


def read_line(device, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = device.readline()
        if raw:
            return raw.decode("ascii", errors="replace").strip()
    raise TimeoutError("timed out waiting for a serial response")


def command(device, text, timeout_s=2.0):
    device.write((text + "\n").encode("ascii"))
    device.flush()
    return read_line(device, timeout_s)


def wait_ready(device, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = command(device, "STATUS")
        if status == "OK READY":
            return status
        if not (status.startswith("OK COOLDOWN ") or status.startswith("OK PULSING ")):
            raise RuntimeError(f"unexpected status while waiting: {status}")
        time.sleep(0.25)
    raise TimeoutError("device did not become ready before timeout")


def main():
    parser = argparse.ArgumentParser(description="Smoke-test relay reset firmware over serial.")
    parser.add_argument("--port", required=True, help="Serial port, for example /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600, help="Serial baud rate")
    parser.add_argument(
        "--pulse",
        action="store_true",
        help="Trigger a real relay pulse; without this only PING and STATUS are checked",
    )
    parser.add_argument(
        "--open-delay",
        type=float,
        default=2.0,
        help="Delay after opening serial; Nano boards often reset when DTR changes",
    )
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.2) as device:
        time.sleep(args.open_delay)
        device.reset_input_buffer()

        ping = command(device, "PING")
        if ping != "PONG":
          raise RuntimeError(f"PING expected PONG, got {ping!r}")
        print("PING: PONG")

        status = command(device, "STATUS")
        if not status.startswith("OK "):
            raise RuntimeError(f"STATUS expected OK response, got {status!r}")
        print(f"STATUS: {status}")

        if not args.pulse:
            return 0

        wait_ready(device, timeout_s=15.0)
        reset = command(device, "RESET")
        if reset != "OK RESETTING":
            raise RuntimeError(f"RESET expected OK RESETTING, got {reset!r}")
        print("RESET: OK RESETTING")

        done = read_line(device, timeout_s=3.0)
        if done != "OK DONE":
            raise RuntimeError(f"pulse completion expected OK DONE, got {done!r}")
        print("RESET: OK DONE")

        status = command(device, "STATUS")
        if not status.startswith("OK COOLDOWN "):
            raise RuntimeError(f"post-reset STATUS expected cooldown, got {status!r}")
        print(f"STATUS: {status}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
