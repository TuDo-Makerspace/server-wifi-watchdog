/*
* RelayResetProtocol declares the serial command state machine for relay resets.
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

#ifndef RELAY_RESET_PROTOCOL_H
#define RELAY_RESET_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

class RelayResetProtocol {
public:
  typedef uint32_t (*ClockFn)(void *context);
  typedef void (*RelayFn)(void *context, bool active);
  typedef void (*WriteFn)(void *context, const char *text);

  struct Config {
    uint32_t pulseMs;
    uint32_t cooldownMs;
  };

  static const size_t kMaxCommandLength = 24;

  RelayResetProtocol(ClockFn clockFn,
                     void *clockContext,
                     RelayFn relayFn,
                     void *relayContext,
                     WriteFn writeFn,
                     void *writeContext,
                     Config config);

  void begin();
  void receive(char ch);
  void tick();

  bool relayActive() const;
  bool pulsing() const;
  bool coolingDown() const;
  uint32_t pulseRemainingMs() const;
  uint32_t cooldownRemainingMs() const;

private:
  enum State {
    Ready,
    Pulsing,
    Cooldown,
  };

  ClockFn clockFn_;
  void *clockContext_;
  RelayFn relayFn_;
  void *relayContext_;
  WriteFn writeFn_;
  void *writeContext_;
  Config config_;

  State state_;
  bool relayActive_;
  uint32_t pulseStartedAt_;
  uint32_t cooldownStartedAt_;
  char command_[kMaxCommandLength];
  size_t commandLength_;
  bool commandOverflow_;

  uint32_t now() const;
  void setRelay(bool active);
  void write(const char *text);
  void writeStatus();
  void writeWithRemaining(const char *prefix, uint32_t remainingMs);
  void processCommand();
  void resetCommandBuffer();
  uint32_t elapsedSince(uint32_t startedAt) const;
  uint32_t remainingFrom(uint32_t startedAt, uint32_t durationMs) const;
};

#endif
