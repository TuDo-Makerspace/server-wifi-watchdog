/*
* Relay reset protocol tests verify firmware command and timing behavior.
* Copyright (C) 2026  TuDo Makerspace, Patrick Pedersen <ctx.xda@gmail.com>
*
* This program is free software: you can redistribute it and/or modify
* it under the terms of the GNU General Public License as published by
* the Free Software Foundation, either version 3 of the License, or
* (at your option) any later version.
*
* This program is distributed in the hope that it will be useful,
* but WITHOUT ANY WARRANTY; without even the implied warranty of
* MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
* GNU General Public License for more details.
*
* You should have received a copy of the GNU General Public License
* along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include <RelayResetProtocol.h>
#include <string.h>
#include <unity.h>

#ifdef ARDUINO
#include <Arduino.h>
#endif

struct Harness {
  uint32_t now;
  bool relayActive;
  uint8_t relayTransitions;
  char output[512];
  size_t outputLength;
  RelayResetProtocol protocol;

  Harness()
      : now(0),
        relayActive(false),
        relayTransitions(0),
        outputLength(0),
        protocol(clock,
                 this,
                 relay,
                 this,
                 write,
                 this,
                 RelayResetProtocol::Config{1000, 3000}) {
    output[0] = '\0';
    protocol.begin();
  }

  static uint32_t clock(void *context) {
    return static_cast<Harness *>(context)->now;
  }

  static void relay(void *context, bool active) {
    Harness *harness = static_cast<Harness *>(context);
    if (harness->relayActive != active) {
      harness->relayTransitions++;
    }
    harness->relayActive = active;
  }

  static void write(void *context, const char *text) {
    Harness *harness = static_cast<Harness *>(context);
    size_t textLength = strlen(text);
    if (harness->outputLength + textLength >= sizeof(harness->output)) {
      return;
    }

    memcpy(harness->output + harness->outputLength, text, textLength + 1);
    harness->outputLength += textLength;
  }

  void clearOutput() {
    outputLength = 0;
    output[0] = '\0';
  }

  void send(const char *line) {
    while (*line != '\0') {
      protocol.receive(*line);
      line++;
    }
  }

  void advance(uint32_t ms) {
    now += ms;
    protocol.tick();
  }
};

void test_status_querying_reports_ready() {
  Harness h;

  h.send("STATUS\n");

  TEST_ASSERT_EQUAL_STRING("OK READY\n", h.output);
  TEST_ASSERT_FALSE(h.relayActive);
}

void test_ping_works() {
  Harness h;

  h.send("PING\n");

  TEST_ASSERT_EQUAL_STRING("PONG\n", h.output);
}

void test_reset_starts_and_finishes_pulse() {
  Harness h;

  h.send("RESET\n");

  TEST_ASSERT_EQUAL_STRING("OK RESETTING\n", h.output);
  TEST_ASSERT_TRUE(h.relayActive);
  TEST_ASSERT_TRUE(h.protocol.pulsing());

  h.advance(1000);

  TEST_ASSERT_FALSE(h.relayActive);
  TEST_ASSERT_FALSE(h.protocol.pulsing());
  TEST_ASSERT_TRUE(h.protocol.coolingDown());
  TEST_ASSERT_TRUE(strstr(h.output, "OK DONE\n") != 0);
}

void test_pulse_respects_duration() {
  Harness h;

  h.send("RESET\n");
  h.clearOutput();
  h.advance(999);

  TEST_ASSERT_TRUE(h.relayActive);
  TEST_ASSERT_TRUE(h.protocol.pulsing());
  TEST_ASSERT_EQUAL_STRING("", h.output);
  TEST_ASSERT_EQUAL_UINT32(1, h.protocol.pulseRemainingMs());

  h.advance(1);

  TEST_ASSERT_FALSE(h.relayActive);
  TEST_ASSERT_EQUAL_STRING("OK DONE\n", h.output);
}

void test_overlapping_pulses_do_not_extend_duration() {
  Harness h;

  h.send("RESET\n");
  h.advance(400);
  h.clearOutput();
  h.send("RESET\n");

  TEST_ASSERT_TRUE(h.relayActive);
  TEST_ASSERT_EQUAL_STRING("ERR BUSY 600\n", h.output);
  TEST_ASSERT_EQUAL_UINT8(1, h.relayTransitions);

  h.clearOutput();
  h.advance(599);

  TEST_ASSERT_TRUE(h.relayActive);
  TEST_ASSERT_EQUAL_STRING("", h.output);

  h.advance(1);

  TEST_ASSERT_FALSE(h.relayActive);
  TEST_ASSERT_EQUAL_STRING("OK DONE\n", h.output);
}

void test_spam_of_pulses_and_other_commands() {
  Harness h;

  h.send("RESET\nPING\nSTATUS\nRESET\nNOPE\n");

  TEST_ASSERT_TRUE(h.relayActive);
  TEST_ASSERT_TRUE(strstr(h.output, "OK RESETTING\n") != 0);
  TEST_ASSERT_TRUE(strstr(h.output, "PONG\n") != 0);
  TEST_ASSERT_TRUE(strstr(h.output, "OK PULSING 1000\n") != 0);
  TEST_ASSERT_TRUE(strstr(h.output, "ERR BUSY 1000\n") != 0);
  TEST_ASSERT_TRUE(strstr(h.output, "ERR UNKNOWN\n") != 0);

  h.advance(1000);
  h.clearOutput();
  h.send("RESET\nSTATUS\nPING\n");

  TEST_ASSERT_FALSE(h.relayActive);
  TEST_ASSERT_TRUE(strstr(h.output, "ERR COOLDOWN 3000\n") != 0);
  TEST_ASSERT_TRUE(strstr(h.output, "OK COOLDOWN 3000\n") != 0);
  TEST_ASSERT_TRUE(strstr(h.output, "PONG\n") != 0);
}

void test_statuses_work_across_lifecycle() {
  Harness h;

  h.send("STATUS\n");
  TEST_ASSERT_EQUAL_STRING("OK READY\n", h.output);

  h.clearOutput();
  h.send("RESET\n");
  h.clearOutput();
  h.advance(250);
  h.send("STATUS\n");
  TEST_ASSERT_EQUAL_STRING("OK PULSING 750\n", h.output);

  h.clearOutput();
  h.advance(750);
  h.send("STATUS\n");
  TEST_ASSERT_EQUAL_STRING("OK DONE\nOK COOLDOWN 3000\n", h.output);

  h.clearOutput();
  h.advance(3000);
  h.send("STATUS\n");
  TEST_ASSERT_EQUAL_STRING("OK READY\n", h.output);
}

void setUp() {}

void tearDown() {}

static void runTests() {
  UNITY_BEGIN();
  RUN_TEST(test_status_querying_reports_ready);
  RUN_TEST(test_ping_works);
  RUN_TEST(test_reset_starts_and_finishes_pulse);
  RUN_TEST(test_pulse_respects_duration);
  RUN_TEST(test_overlapping_pulses_do_not_extend_duration);
  RUN_TEST(test_spam_of_pulses_and_other_commands);
  RUN_TEST(test_statuses_work_across_lifecycle);
  UNITY_END();
}

#ifdef ARDUINO
void setup() {
  delay(2000);
  runTests();
}

void loop() {}
#else
int main() {
  runTests();
  return 0;
}
#endif
