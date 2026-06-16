/*
* RelayResetProtocol implements the serial command state machine for relay resets.
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

#include "RelayResetProtocol.h"

#include <stdio.h>
#include <string.h>

RelayResetProtocol::RelayResetProtocol(ClockFn clockFn,
                                       void *clockContext,
                                       RelayFn relayFn,
                                       void *relayContext,
                                       WriteFn writeFn,
                                       void *writeContext,
                                       Config config)
    : clockFn_(clockFn),
      clockContext_(clockContext),
      relayFn_(relayFn),
      relayContext_(relayContext),
      writeFn_(writeFn),
      writeContext_(writeContext),
      config_(config),
      state_(Ready),
      relayActive_(false),
      pulseStartedAt_(0),
      cooldownStartedAt_(0),
      commandLength_(0),
      commandOverflow_(false) {
  command_[0] = '\0';
}

void RelayResetProtocol::begin() {
  state_ = Ready;
  pulseStartedAt_ = 0;
  cooldownStartedAt_ = 0;
  resetCommandBuffer();
  setRelay(false);
}

void RelayResetProtocol::receive(char ch) {
  tick();

  if (ch == '\r') {
    return;
  }

  if (ch == '\n') {
    processCommand();
    resetCommandBuffer();
    return;
  }

  if (commandOverflow_) {
    return;
  }

  if (commandLength_ + 1 >= kMaxCommandLength) {
    commandOverflow_ = true;
    return;
  }

  command_[commandLength_] = ch;
  commandLength_++;
  command_[commandLength_] = '\0';
}

void RelayResetProtocol::tick() {
  if (state_ == Pulsing && elapsedSince(pulseStartedAt_) >= config_.pulseMs) {
    setRelay(false);
    state_ = Cooldown;
    cooldownStartedAt_ = now();
    write("OK DONE\n");
  }

  if (state_ == Cooldown &&
      elapsedSince(cooldownStartedAt_) >= config_.cooldownMs) {
    state_ = Ready;
  }
}

bool RelayResetProtocol::relayActive() const {
  return relayActive_;
}

bool RelayResetProtocol::pulsing() const {
  return state_ == Pulsing;
}

bool RelayResetProtocol::coolingDown() const {
  return state_ == Cooldown;
}

uint32_t RelayResetProtocol::pulseRemainingMs() const {
  if (state_ != Pulsing) {
    return 0;
  }
  return remainingFrom(pulseStartedAt_, config_.pulseMs);
}

uint32_t RelayResetProtocol::cooldownRemainingMs() const {
  if (state_ != Cooldown) {
    return 0;
  }
  return remainingFrom(cooldownStartedAt_, config_.cooldownMs);
}

uint32_t RelayResetProtocol::now() const {
  return clockFn_ == 0 ? 0 : clockFn_(clockContext_);
}

void RelayResetProtocol::setRelay(bool active) {
  relayActive_ = active;
  if (relayFn_ != 0) {
    relayFn_(relayContext_, active);
  }
}

void RelayResetProtocol::write(const char *text) {
  if (writeFn_ != 0) {
    writeFn_(writeContext_, text);
  }
}

void RelayResetProtocol::writeStatus() {
  if (state_ == Pulsing) {
    writeWithRemaining("OK PULSING ", pulseRemainingMs());
    return;
  }

  if (state_ == Cooldown) {
    writeWithRemaining("OK COOLDOWN ", cooldownRemainingMs());
    return;
  }

  write("OK READY\n");
}

void RelayResetProtocol::writeWithRemaining(const char *prefix,
                                            uint32_t remainingMs) {
  char line[40];
  snprintf(line, sizeof(line), "%s%lu\n", prefix, (unsigned long)remainingMs);
  write(line);
}

void RelayResetProtocol::processCommand() {
  if (commandOverflow_) {
    write("ERR TOO_LONG\n");
    return;
  }

  if (commandLength_ == 0) {
    return;
  }

  if (strcmp(command_, "PING") == 0) {
    write("PONG\n");
    return;
  }

  if (strcmp(command_, "STATUS") == 0) {
    writeStatus();
    return;
  }

  if (strcmp(command_, "RESET") == 0) {
    if (state_ == Pulsing) {
      writeWithRemaining("ERR BUSY ", pulseRemainingMs());
      return;
    }

    if (state_ == Cooldown) {
      writeWithRemaining("ERR COOLDOWN ", cooldownRemainingMs());
      return;
    }

    state_ = Pulsing;
    pulseStartedAt_ = now();
    setRelay(true);
    write("OK RESETTING\n");
    return;
  }

  write("ERR UNKNOWN\n");
}

void RelayResetProtocol::resetCommandBuffer() {
  commandLength_ = 0;
  commandOverflow_ = false;
  command_[0] = '\0';
}

uint32_t RelayResetProtocol::elapsedSince(uint32_t startedAt) const {
  return now() - startedAt;
}

uint32_t RelayResetProtocol::remainingFrom(uint32_t startedAt,
                                           uint32_t durationMs) const {
  uint32_t elapsed = elapsedSince(startedAt);
  if (elapsed >= durationMs) {
    return 0;
  }
  return durationMs - elapsed;
}
