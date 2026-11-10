#!/usr/bin/env python3
#
# test_watchdog.py verifies WiFi Watchdog recovery decisions without hardware.
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

import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_PATH = ROOT / "daemon" / "wifi-watchdog.py"

spec = importlib.util.spec_from_file_location("wifi_watchdog", WATCHDOG_PATH)
watchdog_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(watchdog_module)

Health = watchdog_module.Health
Watchdog = watchdog_module.Watchdog


def make_args(tmpdir: str, **overrides):
    defaults = {
        "iface": "wlan-test0",
        "serial": "/dev/null",
        "baud": 9600,
        "check_interval": 15.0,
        "failure_window": 90,
        "min_scan_failures": 10,
        "min_stale_events": 3,
        "unhealthy_timeout": 90.0,
        "soft_recovery_cooldown": 0.0,
        "recovery_cooldown": 0.0,
        "serial_open_delay": 0.0,
        "relay_done_timeout": 5.0,
        "usb_settle_delay": 0.0,
        "command_timeout": 1.0,
        "ready_timeout": 0.0,
        "ready_poll_interval": 0.0,
        "spoof_dead_file": "",
        "state_dir": str(Path(tmpdir) / "state"),
        "lock_file": str(Path(tmpdir) / "state" / "recovery.lock"),
        "soft_command": ["soft-reset {iface}"],
        "before_command": [],
        "after_command": ["ifup {iface}"],
        "after_ready_command": ["systemctl restart eduroambridge.service"],
        "once": True,
        "dry_run": False,
        "verbose": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeWatchdog(Watchdog):
    def __init__(self, args, health_sequence):
        super().__init__(args)
        self.health_sequence = list(health_sequence)
        self.events = []

    def read_health(self):
        if len(self.health_sequence) > 1:
            health = self.health_sequence.pop(0)
        else:
            health = self.health_sequence[0]
        self.events.append(f"health:{health.has_ipv4}:{health.wpa_state}")
        return health

    def pulse_relay(self):
        self.events.append("pulse")

    def run_formatted_command(self, command: str):
        self.events.append(f"cmd:{command.format(iface=self.args.iface)}")


class MissingRelayWatchdog(FakeWatchdog):
    def pulse_relay(self):
        self.events.append("pulse-failed")
        raise FileNotFoundError(2, "No such file or directory", self.args.serial)


class JournalWatchdog(Watchdog):
    def __init__(self, args, journal_output):
        super().__init__(args)
        self.journal_output = journal_output

    def run(self, argv, check: bool, capture: bool, log_failure: bool = True):
        return watchdog_module.subprocess.CompletedProcess(argv, 0, self.journal_output, "")


class WatchdogRecoveryTest(unittest.TestCase):
    def test_sustained_unhealthy_interface_triggers_recovery_without_scan_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[])
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, False, None, 0),
                    Health(True, False, None, 0),
                ],
            )

            watchdog.run_once()
            self.assertEqual(watchdog.events, ["health:False:None"])

            watchdog.unhealthy_since_file.write_text("1", encoding="ascii")
            watchdog.run_once()

            self.assertEqual(
                watchdog.events,
                [
                    "health:False:None",
                    "health:False:None",
                    "cmd:soft-reset wlan-test0",
                    "health:False:None",
                ],
            )
            self.assertTrue(watchdog.hard_required_file.exists())

    def test_connected_interface_clears_unhealthy_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir)
            watchdog = FakeWatchdog(args, [Health(True, True, "COMPLETED", 0)])
            watchdog.unhealthy_since_file.parent.mkdir(parents=True, exist_ok=True)
            watchdog.unhealthy_since_file.write_text("1", encoding="ascii")

            watchdog.run_once()

            self.assertFalse(watchdog.unhealthy_since_file.exists())
            self.assertEqual(watchdog.events, ["health:True:COMPLETED"])

    def test_after_ready_command_waits_for_ipv4(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, ready_timeout=1.0)
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, False, "COMPLETED", 0),
                    Health(True, True, "COMPLETED", 0),
                ],
            )

            watchdog.hard_recover("test")

            self.assertEqual(
                watchdog.events,
                [
                    "pulse",
                    "cmd:ifup wlan-test0",
                    "health:False:COMPLETED",
                    "health:True:COMPLETED",
                    "cmd:systemctl restart eduroambridge.service",
                ],
            )

    def test_after_ready_command_is_skipped_when_interface_never_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, ready_timeout=0.0)
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, False, "COMPLETED", 0),
                ],
            )

            watchdog.hard_recover("test")

            self.assertEqual(
                watchdog.events,
                [
                    "pulse",
                    "cmd:ifup wlan-test0",
                    "health:False:COMPLETED",
                ],
            )

    def test_relay_failure_still_runs_after_commands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(
                tmpdir,
                ready_timeout=1.0,
                before_command=["systemctl stop wpa_supplicant@{iface}.service"],
                after_command=["systemctl restart wpa_supplicant@{iface}.service", "ifup {iface}"],
            )
            watchdog = MissingRelayWatchdog(args, [Health(True, True, "COMPLETED", 0)])
            watchdog.hard_required_file.parent.mkdir(parents=True, exist_ok=True)
            watchdog.hard_required_file.write_text("1", encoding="ascii")

            watchdog.hard_recover("test")

            self.assertEqual(
                watchdog.events,
                [
                    "cmd:systemctl stop wpa_supplicant@wlan-test0.service",
                    "pulse-failed",
                    "cmd:systemctl restart wpa_supplicant@wlan-test0.service",
                    "cmd:ifup wlan-test0",
                    "health:True:COMPLETED",
                    "cmd:systemctl restart eduroambridge.service",
                ],
            )
            self.assertFalse(watchdog.hard_required_file.exists())

    def test_relay_failure_records_hard_recovery_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[], recovery_cooldown=3600.0)
            watchdog = MissingRelayWatchdog(args, [Health(True, False, "COMPLETED", 0)])

            watchdog.hard_recover("test")
            self.assertTrue(watchdog.last_hard_recovery_file.exists())
            self.assertTrue(watchdog.hard_required_file.exists())

            events_before = list(watchdog.events)
            watchdog.hard_recover("test again")

            self.assertEqual(watchdog.events, events_before)

    def test_stale_connection_uses_soft_route_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[])
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                ],
            )

            watchdog.run_once()

            self.assertEqual(
                watchdog.events,
                [
                    "health:True:COMPLETED",
                    "cmd:soft-reset wlan-test0",
                    "health:True:COMPLETED",
                ],
            )
            self.assertFalse(watchdog.hard_required_file.exists())

    def test_soft_failure_escalates_to_hard_on_next_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[])
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, False, "ASSOCIATING", 0, 4),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                ],
            )

            watchdog.run_once()
            self.assertTrue(watchdog.hard_required_file.exists())

            watchdog.run_once()

            self.assertEqual(
                watchdog.events,
                [
                    "health:True:COMPLETED",
                    "cmd:soft-reset wlan-test0",
                    "health:False:ASSOCIATING",
                    "health:True:COMPLETED",
                    "pulse",
                    "cmd:ifup wlan-test0",
                    "health:True:COMPLETED",
                ],
            )
            self.assertFalse(watchdog.hard_required_file.exists())

    def test_soft_success_next_failure_uses_soft_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[])
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                ],
            )

            watchdog.run_once()
            watchdog.run_once()

            self.assertEqual(watchdog.events.count("cmd:soft-reset wlan-test0"), 2)
            self.assertNotIn("pulse", watchdog.events)
            self.assertFalse(watchdog.hard_required_file.exists())

    def test_hard_success_next_failure_uses_soft_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[])
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, False, "ASSOCIATING", 0, 4),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                ],
            )

            watchdog.run_once()
            watchdog.run_once()
            watchdog.run_once()

            self.assertEqual(watchdog.events.count("pulse"), 1)
            self.assertEqual(watchdog.events.count("cmd:soft-reset wlan-test0"), 2)
            self.assertFalse(watchdog.hard_required_file.exists())

    def test_hard_route_is_not_chosen_when_soft_recovery_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[])
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 0),
                ],
            )

            watchdog.run_once()

            self.assertNotIn("pulse", watchdog.events)
            self.assertFalse(watchdog.hard_required_file.exists())

    def test_failed_hard_recovery_retries_hard_after_longer_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir, after_ready_command=[], recovery_cooldown=3600.0)
            watchdog = FakeWatchdog(
                args,
                [
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, False, "ASSOCIATING", 0, 4),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, False, "ASSOCIATING", 0, 4),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, True, "COMPLETED", 0, 4),
                    Health(True, False, "ASSOCIATING", 0, 4),
                ],
            )

            watchdog.run_once()
            watchdog.run_once()
            first_hard_pulses = watchdog.events.count("pulse")
            self.assertEqual(first_hard_pulses, 1)
            self.assertTrue(watchdog.hard_required_file.exists())

            watchdog.run_once()
            self.assertEqual(watchdog.events.count("pulse"), first_hard_pulses)

            old_enough = time.time() - args.recovery_cooldown - 1
            watchdog.last_hard_recovery_file.write_text(str(old_enough), encoding="ascii")

            watchdog.run_once()

            self.assertEqual(watchdog.events.count("pulse"), 2)
            self.assertEqual(watchdog.events.count("cmd:soft-reset wlan-test0"), 1)
            self.assertTrue(watchdog.hard_required_file.exists())

    def test_stale_events_before_successful_recovery_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = make_args(tmpdir)
            watchdog = JournalWatchdog(
                args,
                "\n".join(
                    [
                        "1000.0 host wpa_supplicant[1]: wlan-test0: CTRL-EVENT-DISCONNECTED reason=108",
                        "1010.0 host wpa_supplicant[1]: wlan-test0: CTRL-EVENT-ASSOC-REJECT status_code=1",
                        "1020.0 host wpa_supplicant[1]: wlan-test0: CTRL-EVENT-EAP-FAILURE EAP authentication failed",
                    ]
                ),
            )
            watchdog.stale_ignore_before_file.parent.mkdir(parents=True, exist_ok=True)
            watchdog.stale_ignore_before_file.write_text("1005.0", encoding="ascii")

            self.assertEqual(watchdog.stale_event_count(), 2)


if __name__ == "__main__":
    unittest.main()
