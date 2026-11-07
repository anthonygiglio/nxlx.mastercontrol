// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A stand-in for a program on the network, for tests/test_pvj_link.py only (never installed): one Link peer that sets a
// tempo, as Ableton Live or a phone app would, and prints what it sees five times a second.
//
//   pvj-link-peer BPM SECONDS [BPM2]    (BPM2: the tempo it changes to half way through)
//
// Each line is {"time": CLOCK_MONOTONIC seconds, "peers": n, "bpm": x, "phase": place in a bar of 4 at that time}.

#include <ableton/Link.hpp>

#include <time.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>

namespace
{

double monotonic()
{
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return static_cast<double>(ts.tv_sec) + 1e-9 * static_cast<double>(ts.tv_nsec);
}

} // namespace

int main(int argc, char** argv)
{
  if (argc < 3)
  {
    std::fprintf(stderr, "usage: %s BPM SECONDS [BPM2]\n", argv[0]);
    return 64;
  }
  const double bpm = std::atof(argv[1]), seconds = std::atof(argv[2]);
  const double then = argc > 3 ? std::atof(argv[3]) : 0.0;
  ableton::Link link(bpm);
  link.enable(true);
  const double start = monotonic();
  bool changed = false;
  while (monotonic() - start < seconds)
  {
    if (then > 0.0 && !changed && monotonic() - start >= seconds / 2.0)
    {
      auto state = link.captureAppSessionState();
      state.setTempo(then, link.clock().micros());
      link.commitAppSessionState(state);
      changed = true;
    }
    const auto state = link.captureAppSessionState();
    const double before = monotonic();
    const auto host = link.clock().micros();
    const double after = monotonic();
    std::printf("{\"time\": %.6f, \"peers\": %u, \"bpm\": %.3f, \"phase\": %.6f}\n", 0.5 * (before + after),
      static_cast<unsigned>(link.numPeers()), state.tempo(), state.phaseAtTime(host, 4.0));
    std::fflush(stdout);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
  }
  link.enable(false);
  return 0;
}
