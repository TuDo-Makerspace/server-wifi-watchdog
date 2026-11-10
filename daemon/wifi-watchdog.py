#!/usr/bin/env python3
#
# WiFi Watchdog monitors WiFi health and recovers it through soft resets or a relay.
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
import fcntl
import logging
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import termios
import time
from dataclasses import dataclass
from pathlib import Path


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


@dataclass
class Health:
    iface_exists: bool
    has_ipv4: bool
    wpa_state: str | None
    scan_failures: int
    stale_events: int = 0

    @property
    def connected(self) -> bool:
        return self.wpa_state == "COMPLETED" and self.has_ipv4


class SerialRelay:
    def __init__(self, path: str, baud: int, open_delay: float):
        self.path = path
        self.baud = baud
        self.open_delay = open_delay
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self._configure()
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        if self.open_delay > 0:
            time.sleep(self.open_delay)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _configure(self):
        if self.baud not in BAUD_RATES:
            raise ValueError(f"unsupported baud rate: {self.baud}")

        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = BAUD_RATES[self.baud] | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = BAUD_RATES[self.baud]
        attrs[5] = BAUD_RATES[self.baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def command(self, text: str, timeout_s: float = 3.0) -> str:
        os.write(self.fd, (text + "\n").encode("ascii"))
        return self.read_line(timeout_s)

    def read_line(self, timeout_s: float = 3.0) -> str:
        deadline = time.monotonic() + timeout_s
        buf = bytearray()

        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([self.fd], [], [], min(0.2, remaining))
            if not readable:
                continue

            chunk = os.read(self.fd, 128)
            if not chunk:
                continue

            for byte in chunk:
                if byte == ord("\n"):
                    return buf.decode("ascii", errors="replace").strip()
                if byte != ord("\r"):
                    buf.append(byte)

        partial = buf.decode("ascii", errors="replace")
        raise TimeoutError(f"timed out waiting for serial line; partial={partial!r}")


class Watchdog:
    def __init__(self, args):
        self.args = args
        self.state_dir = Path(args.state_dir)
        self.last_recovery_file = self.state_dir / "last-recovery"
        self.last_soft_recovery_file = self.state_dir / "last-soft-recovery"
        self.last_hard_recovery_file = self.state_dir / "last-hard-recovery"
        self.unhealthy_since_file = self.state_dir / "unhealthy-since"
        self.hard_required_file = self.state_dir / "hard-required"
        self.stale_ignore_before_file = self.state_dir / "stale-ignore-before"
        self.lock_file = Path(args.lock_file)

    def run_forever(self):
        while True:
            self.run_once()
            time.sleep(self.args.check_interval)

    def run_once(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        forced = self.consume_spoof_file()
        if forced:
            self.hard_recover("spoofed dead-stick test requested")
            return

        health = self.read_health()

        logging.info(
            "health iface_exists=%s has_ipv4=%s wpa_state=%s scan_failures=%d stale_events=%d",
            health.iface_exists,
            health.has_ipv4,
            health.wpa_state,
            health.scan_failures,
            health.stale_events,
        )

        if self.is_dead(health):
            self.recover(
                f"{self.args.iface} appears unhealthy: "
                f"scan_failures={health.scan_failures} in {self.args.failure_window}s, "
                f"stale_events={health.stale_events} in {self.args.failure_window}s, "
                f"wpa_state={health.wpa_state}, has_ipv4={health.has_ipv4}"
            )
            return

        logging.info("no recovery needed")

    def consume_spoof_file(self) -> bool:
        if not self.args.spoof_dead_file:
            return False

        spoof_path = Path(self.args.spoof_dead_file)
        if not spoof_path.exists():
            return False

        try:
            spoof_path.unlink()
        except FileNotFoundError:
            pass

        logging.warning("consumed spoof-dead file: %s", spoof_path)
        return True

    def is_dead(self, health: Health) -> bool:
        if not health.iface_exists:
            logging.warning("interface %s does not exist; not treating as wedged", self.args.iface)
            self.clear_recovery_markers()
            return False

        if health.stale_events >= self.args.min_stale_events:
            logging.warning(
                "%s has %d stale connection events in %ds",
                self.args.iface,
                health.stale_events,
                self.args.failure_window,
            )
            return True

        if health.connected:
            self.clear_recovery_markers()
            return False

        unhealthy_age = self.unhealthy_age()
        if health.scan_failures >= self.args.min_scan_failures:
            logging.warning(
                "%s is unhealthy and has %d scan failures in %ds",
                self.args.iface,
                health.scan_failures,
                self.args.failure_window,
            )
            return True

        if unhealthy_age >= self.args.unhealthy_timeout:
            logging.warning(
                "%s has been unhealthy for %.0fs; wpa_state=%s has_ipv4=%s",
                self.args.iface,
                unhealthy_age,
                health.wpa_state,
                health.has_ipv4,
            )
            return True

        remaining = self.args.unhealthy_timeout - unhealthy_age
        logging.info("%s unhealthy for %.0fs; waiting %.0fs before recovery", self.args.iface, unhealthy_age, remaining)
        return False

    def unhealthy_age(self) -> float:
        self.unhealthy_since_file.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        try:
            unhealthy_since = float(self.unhealthy_since_file.read_text(encoding="ascii").strip())
        except (FileNotFoundError, ValueError):
            self.unhealthy_since_file.write_text(str(now), encoding="ascii")
            return 0.0

        if unhealthy_since > now:
            self.unhealthy_since_file.write_text(str(now), encoding="ascii")
            return 0.0

        return now - unhealthy_since

    def clear_unhealthy_since(self):
        try:
            self.unhealthy_since_file.unlink()
        except FileNotFoundError:
            pass

    def clear_recovery_markers(self):
        self.clear_unhealthy_since()
        try:
            self.hard_required_file.unlink()
        except FileNotFoundError:
            pass

    def read_health(self) -> Health:
        return Health(
            iface_exists=Path("/sys/class/net", self.args.iface).exists(),
            has_ipv4=self.has_ipv4(),
            wpa_state=self.wpa_state(),
            scan_failures=self.scan_failure_count(),
            stale_events=self.stale_event_count(),
        )

    def has_ipv4(self) -> bool:
        result = self.run(
            ["ip", "-o", "-4", "addr", "show", "dev", self.args.iface, "scope", "global"],
            check=False,
            capture=True,
        )
        return result.returncode == 0 and bool(result.stdout.strip())

    def wpa_state(self) -> str | None:
        wpa_cli = self.find_program("wpa_cli", ["/sbin/wpa_cli", "/usr/sbin/wpa_cli"])
        if not wpa_cli:
            logging.warning("wpa_cli not found; wpa_state will be unknown")
            return None

        result = self.run([wpa_cli, "-i", self.args.iface, "status"], check=False, capture=True, log_failure=False)
        if result.returncode != 0:
            logging.warning("wpa_cli status failed: %s", result.stderr.strip())
            return None

        match = re.search(r"^wpa_state=(.+)$", result.stdout, re.MULTILINE)
        return match.group(1).strip() if match else None

    def scan_failure_count(self) -> int:
        result = self.run(
            [
                "journalctl",
                "-u",
                f"wpa_supplicant@{self.args.iface}.service",
                f"--since=-{self.args.failure_window}s",
                "--no-pager",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            logging.warning("journalctl scan failed: %s", result.stderr.strip())
            return 0

        return result.stdout.count("CTRL-EVENT-SCAN-FAILED ret=-1")

    def stale_event_count(self) -> int:
        result = self.run(
            [
                "journalctl",
                "-u",
                f"wpa_supplicant@{self.args.iface}.service",
                f"--since=-{self.args.failure_window}s",
                "--output=short-unix",
                "--no-pager",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            logging.warning("journalctl stale-event scan failed: %s", result.stderr.strip())
            return 0

        ignore_before = self.stale_ignore_before()
        patterns = (
            "CTRL-EVENT-DISCONNECTED",
            "CTRL-EVENT-ASSOC-REJECT",
            "CTRL-EVENT-EAP-FAILURE",
            "CTRL-EVENT-SSID-TEMP-DISABLED",
            "ignore list count",
        )
        count = 0
        for line in result.stdout.splitlines():
            if ignore_before is not None:
                timestamp = self.journal_line_timestamp(line)
                if timestamp is not None and timestamp <= ignore_before:
                    continue

            if any(pattern in line for pattern in patterns):
                count += 1

        return count

    def stale_ignore_before(self) -> float | None:
        try:
            return float(self.stale_ignore_before_file.read_text(encoding="ascii").strip())
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def journal_line_timestamp(line: str) -> float | None:
        match = re.match(r"^(\d+(?:\.\d+)?)\s", line)
        if not match:
            return None
        return float(match.group(1))

    def recover(self, reason: str):
        if self.hard_required_file.exists():
            self.hard_recover(f"{reason}; soft recovery previously failed")
            return

        self.soft_recover(reason)

    def soft_recover(self, reason: str):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

        with self.lock_file.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not self.cooldown_elapsed(self.last_soft_recovery_file, self.args.soft_recovery_cooldown, "soft recovery"):
                return

            logging.warning("starting soft recovery: %s", reason)

            for command in self.args.soft_command:
                self.run_formatted_command(command)

            ready = self.wait_until_ready()
            self.mark_recovery(self.last_soft_recovery_file)

            if ready:
                self.mark_stale_events_handled()
                self.run_after_ready_commands()
                self.clear_recovery_markers()
                logging.warning("soft recovery complete")
            else:
                self.mark_hard_required()
                logging.warning("soft recovery did not restore %s; hard recovery required", self.args.iface)

    def hard_recover(self, reason: str):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

        with self.lock_file.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not self.cooldown_elapsed(self.last_hard_recovery_file, self.args.recovery_cooldown, "hard recovery"):
                return

            logging.warning("starting hard recovery: %s", reason)

            for command in self.args.before_command:
                self.run_formatted_command(command)

            # Run the after-commands even if the relay fails, so WiFi comes back up.
            relay_ok = True
            try:
                self.pulse_relay()
            except Exception as exc:
                relay_ok = False
                logging.error("relay pulse failed (%s), continuing recovery without power-cycle", exc)

            if self.args.usb_settle_delay > 0 and not self.args.dry_run:
                logging.info("waiting %.1fs for USB/network stack to settle", self.args.usb_settle_delay)
                time.sleep(self.args.usb_settle_delay)

            for command in self.args.after_command:
                self.run_formatted_command(command)

            ready = self.wait_until_ready()
            self.mark_recovery(self.last_hard_recovery_file)

            if ready:
                self.mark_stale_events_handled()
                self.run_after_ready_commands()
                self.clear_recovery_markers()
                if relay_ok:
                    logging.warning("hard recovery complete")
                else:
                    logging.warning("hard recovery complete without relay pulse, check relay controller %s", self.args.serial)
            else:
                self.mark_hard_required()
                logging.warning("hard recovery did not restore %s; future hard attempts remain cooldown-limited", self.args.iface)

    def run_after_ready_commands(self):
        for command in self.args.after_ready_command:
            self.run_formatted_command(command)

    def mark_hard_required(self):
        if self.args.dry_run:
            logging.warning("dry-run: would mark hard recovery required")
            return

        self.hard_required_file.parent.mkdir(parents=True, exist_ok=True)
        self.hard_required_file.write_text(str(time.time()), encoding="ascii")

    def mark_recovery(self, path: Path):
        if self.args.dry_run:
            return

        now = str(time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(now, encoding="ascii")
        self.last_recovery_file.write_text(now, encoding="ascii")

    def mark_stale_events_handled(self):
        if self.args.dry_run:
            return

        self.stale_ignore_before_file.parent.mkdir(parents=True, exist_ok=True)
        self.stale_ignore_before_file.write_text(str(time.time()), encoding="ascii")

    def wait_until_ready(self) -> bool:
        if self.args.dry_run:
            logging.warning("dry-run: would wait for %s to become ready", self.args.iface)
            return True

        deadline = time.monotonic() + max(0.0, self.args.ready_timeout)

        while True:
            health = self.read_health()
            logging.info(
                "post-recovery readiness iface_exists=%s has_ipv4=%s wpa_state=%s stale_events=%d",
                health.iface_exists,
                health.has_ipv4,
                health.wpa_state,
                health.stale_events,
            )

            if health.connected:
                logging.info("%s is ready", self.args.iface)
                return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logging.warning(
                    "%s did not become ready within %.1fs",
                    self.args.iface,
                    self.args.ready_timeout,
                )
                return False

            time.sleep(min(self.args.ready_poll_interval, remaining))

    def cooldown_elapsed(self, path: Path, cooldown: float, label: str) -> bool:
        try:
            last_recovery = float(path.read_text(encoding="ascii").strip())
        except (FileNotFoundError, ValueError):
            return True

        elapsed = time.time() - last_recovery
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            logging.warning("%s suppressed by cooldown; %.0fs remaining", label, remaining)
            return False

        return True

    def pulse_relay(self):
        if self.args.dry_run:
            logging.warning("dry-run: would pulse relay on %s", self.args.serial)
            return

        logging.info("opening relay controller: %s", self.args.serial)
        with SerialRelay(self.args.serial, self.args.baud, self.args.serial_open_delay) as relay:
            pong = relay.command("PING")
            if pong != "PONG":
                raise RuntimeError(f"relay controller PING expected PONG, got {pong!r}")

            status = relay.command("STATUS")
            logging.info("relay controller status: %s", status)
            if status != "OK READY":
                raise RuntimeError(f"relay controller is not ready: {status!r}")

            first = relay.command("RESET")
            if first != "OK RESETTING":
                raise RuntimeError(f"RESET expected OK RESETTING, got {first!r}")
            logging.warning("relay pulse started")

            done = relay.read_line(timeout_s=self.args.relay_done_timeout)
            if done != "OK DONE":
                raise RuntimeError(f"relay pulse completion expected OK DONE, got {done!r}")
            logging.warning("relay pulse completed")

    def run_formatted_command(self, command: str):
        formatted = command.format(iface=self.args.iface)
        argv = shlex.split(formatted)
        if not argv:
            return

        if self.args.dry_run:
            logging.warning("dry-run: would run command: %s", formatted)
            return

        logging.info("running command: %s", formatted)
        self.run(argv, check=False, capture=True)

    def run(self, argv, check: bool, capture: bool, log_failure: bool = True):
        kwargs = {
            "text": True,
            "timeout": self.args.command_timeout,
        }
        if capture:
            kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE})

        try:
            result = subprocess.run(argv, **kwargs)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

            msg = (
                f"command timed out after {self.args.command_timeout:.1f}s: "
                f"{' '.join(argv)}"
            )
            if capture:
                msg += f"; stdout={stdout.strip()!r}; stderr={stderr.strip()!r}"

            if check:
                raise RuntimeError(msg) from exc

            if log_failure:
                logging.warning(msg)
            return subprocess.CompletedProcess(argv, 124, stdout, stderr)

        if result.returncode != 0:
            msg = f"command failed ({result.returncode}): {' '.join(argv)}"
            if capture:
                msg += f"; stdout={result.stdout.strip()!r}; stderr={result.stderr.strip()!r}"

            if check:
                raise RuntimeError(msg)
            if log_failure:
                logging.warning(msg)

        return result

    @staticmethod
    def find_program(name: str, fallback_paths: list[str]) -> str | None:
        found = shutil.which(name)
        if found:
            return found
        for path in fallback_paths:
            if Path(path).exists():
                return path
        return None


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor a WiFi connection and recover it through soft resets or an Arduino relay.")
    parser.add_argument("--iface", required=True, help="WiFi interface to monitor, for example wlx40a5efd86cc5")
    parser.add_argument("--serial", required=True, help="Relay controller serial device, preferably a stable udev path")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--check-interval", type=float, default=15.0)
    parser.add_argument("--failure-window", type=int, default=90)
    parser.add_argument("--min-scan-failures", type=int, default=10)
    parser.add_argument("--min-stale-events", type=int, default=6)
    parser.add_argument("--unhealthy-timeout", type=float, default=90.0)
    parser.add_argument("--soft-recovery-cooldown", type=float, default=60.0)
    parser.add_argument("--recovery-cooldown", type=float, default=600.0)
    parser.add_argument("--serial-open-delay", type=float, default=2.0)
    parser.add_argument("--relay-done-timeout", type=float, default=5.0)
    parser.add_argument("--usb-settle-delay", type=float, default=8.0)
    parser.add_argument("--command-timeout", type=float, default=120.0)
    parser.add_argument("--ready-timeout", type=float, default=90.0)
    parser.add_argument("--ready-poll-interval", type=float, default=2.0)
    parser.add_argument("--spoof-dead-file", default="/run/wifi-watchdog/force-dead")
    parser.add_argument("--state-dir", default="/run/wifi-watchdog")
    parser.add_argument("--lock-file", default="/run/wifi-watchdog/recovery.lock")
    parser.add_argument("--soft-command", action="append", default=[], help="Command to run for soft recovery; {iface} is substituted")
    parser.add_argument("--before-command", action="append", default=[], help="Command to run before pulsing; {iface} is substituted")
    parser.add_argument("--after-command", action="append", default=[], help="Command to run after pulsing; {iface} is substituted")
    parser.add_argument("--after-ready-command", action="append", default=[], help="Command to run after the interface has wpa_state=COMPLETED and IPv4; {iface} is substituted")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--dry-run", action="store_true", help="Log intended recovery actions without running commands or pulsing relay")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    watchdog = Watchdog(args)
    if args.once:
        watchdog.run_once()
    else:
        watchdog.run_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logging.exception("fatal error: %s", exc)
        raise SystemExit(1)
