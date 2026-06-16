#!/usr/bin/env python3
#
# toggle.py sends manual serial relay commands to the WiFi Watchdog firmware.
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


def read_line(device, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = device.readline()
        if raw:
            return raw.decode("ascii", errors="replace").strip()
    raise TimeoutError("timed out waiting for Arduino response")


def main():
    parser = argparse.ArgumentParser(description="Pulse the relay once.")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.2) as device:
        time.sleep(2.0)
        device.reset_input_buffer()

        device.write(b"RESET\n")
        device.flush()

        first = read_line(device)
        print(first)
        if first != "OK RESETTING":
            return 1

        done = read_line(device)
        print(done)
        return 0 if done == "OK DONE" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
