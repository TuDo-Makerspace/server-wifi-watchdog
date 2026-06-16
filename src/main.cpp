/*
* WiFi Watchdog firmware entry point for controlling the relay reset protocol.
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

#ifdef ARDUINO

#include <Arduino.h>

#include <RelayResetProtocol.h>

#ifndef RELAY_PIN
#define RELAY_PIN 8
#endif

#ifndef RELAY_ACTIVE_LOW
#define RELAY_ACTIVE_LOW 1
#endif

static const uint32_t kSerialBaud = 9600;
static const RelayResetProtocol::Config kProtocolConfig = {
    1000,  // pulseMs
    10000, // cooldownMs
};

static uint32_t protocolMillis(void *context) {
  (void)context;
  return millis();
}

static void writeRelay(void *context, bool active) {
  (void)context;
#if RELAY_ACTIVE_LOW
  digitalWrite(RELAY_PIN, active ? LOW : HIGH);
#else
  digitalWrite(RELAY_PIN, active ? HIGH : LOW);
#endif
}

static void writeSerial(void *context, const char *text) {
  (void)context;
  Serial.print(text);
}

static RelayResetProtocol protocol(protocolMillis,
                                   0,
                                   writeRelay,
                                   0,
                                   writeSerial,
                                   0,
                                   kProtocolConfig);

void setup() {
  writeRelay(0, false);
  pinMode(RELAY_PIN, OUTPUT);
  protocol.begin();
  Serial.begin(kSerialBaud);
}

void loop() {
  while (Serial.available() > 0) {
    protocol.receive((char)Serial.read());
  }

  protocol.tick();
}

#else

int main() {
  return 0;
}

#endif
