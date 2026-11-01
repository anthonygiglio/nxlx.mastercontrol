// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvj-link (D117, pvj/SOUND.md): the box follows an Ableton Link session.
//
// It reads a session's tempo and its place in a four-beat bar and writes them into one small file for the panel. It
// never sets a tempo, a beat or play and stop: there is no commit of a session state anywhere in this program. The
// panel's account tells it on or off over a socket; nothing else talks to it.
//
// Why it waits for someone else before it joins. When two Link sessions meet, the older one wins and the younger takes
// its tempo (ableton/link/Sessions.hpp). A box that stood alone in a session of its own for an hour would give Link's
// default tempo to any program that switched Link on, which is the box setting the session's tempo. So while it is
// alone the Link object stays disabled and this program only listens for another peer's "alive" message on Link's
// multicast address, sending nothing. It enables Link JOIN_WAIT after the first one it hears, so the other session is
// older by more than Link's margin of 0.5 s and the box is the one that joins. When the last peer goes it disables
// Link again.
//
// Link's clock is not the box's: on Linux it is CLOCK_MONOTONIC_RAW, while everything else on the box uses
// CLOCK_MONOTONIC (the levels file too). So the two are read side by side and a record says "this was the phase at this
// CLOCK_MONOTONIC time", in the box's own clock.
//
// The record is 56 bytes, little-endian, written in place, at most 20 times a second and once a second while nobody is
// there. pvj/link.py has the same layout (pack, unpack); tests/test_link.py and tests/test_pvj_link.py hold them together.
//
//   offset  0  4 bytes  magic NXLK
//           4  u16      version (1)
//           6  u16      size (56)
//           8  u32      sequence number
//          12  u32      flags: 1 on (the Owner's switch), 2 the Link object is enabled
//          16  f64      when, CLOCK_MONOTONIC seconds
//          24  f64      the session's tempo, BPM (0 with no peers)
//          32  f64      the place in the bar at that time, 0 up to 4 (0 with no peers)
//          40  u32      peers
//          44  u32      reserved (0)
//          48  u32      CRC-32 (zlib's) of bytes 0 to 47
//          52  u32      sequence number again

#include <ableton/Link.hpp>

#include <arpa/inet.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/times.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace
{

constexpr double QUANTUM = 4.0;           // the bar: as BAR_BEATS in pvj/clock.py
constexpr double NEVER_SHOWN_TEMPO = 120.0;   // Link must be given one; it is never written, never said, never set on anyone
constexpr double RECORD_EVERY = 0.05;     // 20 records a second while Link is enabled
constexpr double IDLE_EVERY = 1.0;        // and one a second otherwise, so the panel can tell the helper is there
constexpr double JOIN_WAIT = 0.75;        // seconds from the first peer heard to enabling Link: more than Link's 0.5 s margin
constexpr double JOIN_LIMIT = 5.0;        // enabled this long and nobody came: back to listening
constexpr double EMPTY_GRACE = 1.0;       // no peers for this long: Link is disabled again
constexpr double QUIET_AFTER_LEAVE = 0.5; // what is still in the air of our own Link after leaving is not a peer
constexpr double RESCAN_EVERY = 5.0;      // the networks are looked at again this often while on
constexpr int ASK_MS = 200;               // a caller gets this long to say its one line
constexpr std::size_t MAX_LINE = 128;
constexpr std::size_t RECORD_SIZE = 56;
constexpr std::uint32_t F_ON = 1, F_ENABLED = 2;

const char* const LINK_GROUP = "224.76.78.75";   // Link's discovery: ableton/discovery/IpInterface.hpp
constexpr std::uint16_t LINK_PORT = 20808;
constexpr char ALIVE_HEAD[8] = {'_', 'a', 's', 'd', 'p', '_', 'v', 1};  // ableton/discovery/v1/Messages.hpp
constexpr unsigned char MSG_ALIVE = 1;

enum class Stage
{
  Off,      // the Owner's switch is off: nothing on the network, not even listening
  Alone,    // on, and nobody heard: listening only
  Waiting,  // on, and someone was heard: JOIN_WAIT
  Joining,  // Link enabled, no peer in the session yet
  Session,  // following
};

const char* stageName(const Stage s)
{
  switch (s)
  {
  case Stage::Off:
    return "off";
  case Stage::Alone:
    return "listening";
  case Stage::Waiting:
  case Stage::Joining:
    return "joining";
  case Stage::Session:
    return "following";
  }
  return "off";
}

volatile sig_atomic_t stopping = 0;

void onSignal(int)
{
  stopping = 1;
}

double monotonic()
{
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return static_cast<double>(ts.tv_sec) + 1e-9 * static_cast<double>(ts.tv_nsec);
}

std::uint32_t crc32(const unsigned char* data, const std::size_t size)
{
  static std::uint32_t table[256];
  static bool made = false;
  if (!made)
  {
    for (std::uint32_t i = 0; i < 256; ++i)
    {
      std::uint32_t c = i;
      for (int k = 0; k < 8; ++k)
      {
        c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      }
      table[i] = c;
    }
    made = true;
  }
  std::uint32_t c = 0xFFFFFFFFu;
  for (std::size_t i = 0; i < size; ++i)
  {
    c = table[(c ^ data[i]) & 0xFF] ^ (c >> 8);
  }
  return c ^ 0xFFFFFFFFu;
}

// The file: the box is little-endian (arm64, x86-64), so the values go in as they are.
class StateFile
{
public:
  bool open(const std::string& path)
  {
    mFd = ::open(path.c_str(), O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0640);
    if (mFd < 0)
    {
      return false;
    }
    struct stat st;
    if (fstat(mFd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != getuid()
        || ftruncate(mFd, RECORD_SIZE) != 0)
    {
      ::close(mFd);
      mFd = -1;
      return false;
    }
    return true;
  }

  void write(const std::uint32_t flags,
    const double at,
    const double bpm,
    const double phase,
    const std::uint32_t peers)
  {
    unsigned char b[RECORD_SIZE] = {0};
    const std::uint16_t version = 1, size = RECORD_SIZE;
    ++mSeq;
    std::memcpy(b, "NXLK", 4);
    std::memcpy(b + 4, &version, 2);
    std::memcpy(b + 6, &size, 2);
    std::memcpy(b + 8, &mSeq, 4);
    std::memcpy(b + 12, &flags, 4);
    std::memcpy(b + 16, &at, 8);
    std::memcpy(b + 24, &bpm, 8);
    std::memcpy(b + 32, &phase, 8);
    std::memcpy(b + 40, &peers, 4);
    const std::uint32_t crc = crc32(b, 48);
    std::memcpy(b + 48, &crc, 4);
    std::memcpy(b + 52, &mSeq, 4);
    if (pwrite(mFd, b, RECORD_SIZE, 0) != static_cast<ssize_t>(RECORD_SIZE) && !mSaid)
    {
      mSaid = true;
      std::fprintf(stderr, "pvj-link: the state file cannot be written: %s\n", std::strerror(errno));
    }
  }

private:
  int mFd = -1;
  std::uint32_t mSeq = 0;
  bool mSaid = false;
};

// Listening for another peer, without being one. Only while on and alone does this hear anything that matters.
class Listener
{
public:
  ~Listener()
  {
    close();
  }

  bool open()
  {
    if (mFd >= 0)
    {
      return true;
    }
    mFd = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (mFd < 0)
    {
      return false;
    }
    const int yes = 1;
    sockaddr_in where;
    std::memset(&where, 0, sizeof where);
    where.sin_family = AF_INET;
    where.sin_port = htons(LINK_PORT);
    where.sin_addr.s_addr = inet_addr(LINK_GROUP);
    if (setsockopt(mFd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes) != 0
        || bind(mFd, reinterpret_cast<const sockaddr*>(&where), sizeof where) != 0)
    {
      close();
      return false;
    }
    return true;
  }

  void close()
  {
    if (mFd >= 0)
    {
      ::close(mFd);
      mFd = -1;
    }
  }

  int fd() const
  {
    return mFd;
  }

  // The group is joined on every network that can carry it (never the loopback; the support tunnel has no multicast).
  // Asked again every RESCAN_EVERY, since a network comes and goes: one that is joined already answers EADDRINUSE.
  void joinNetworks() const
  {
    ifaddrs* all = nullptr;
    if (mFd < 0 || getifaddrs(&all) != 0)
    {
      return;
    }
    for (const ifaddrs* i = all; i != nullptr; i = i->ifa_next)
    {
      if (i->ifa_addr == nullptr || i->ifa_addr->sa_family != AF_INET
          || !(i->ifa_flags & IFF_UP) || !(i->ifa_flags & IFF_RUNNING)
          || !(i->ifa_flags & IFF_MULTICAST) || (i->ifa_flags & IFF_LOOPBACK))
      {
        continue;
      }
      ip_mreq group;
      group.imr_multiaddr.s_addr = inet_addr(LINK_GROUP);
      group.imr_interface = reinterpret_cast<const sockaddr_in*>(i->ifa_addr)->sin_addr;
      (void)setsockopt(mFd, IPPROTO_IP, IP_ADD_MEMBERSHIP, &group, sizeof group);
    }
    freeifaddrs(all);
  }

  // Everything waiting is taken; true if any of it was an "alive" message.
  bool heardAlive() const
  {
    bool heard = false;
    unsigned char b[16];
    for (int i = 0; i < 64; ++i)
    {
      const ssize_t n = recv(mFd, b, sizeof b, MSG_TRUNC);
      if (n < 0)
      {
        break;
      }
      if (n > 8 && std::memcmp(b, ALIVE_HEAD, sizeof ALIVE_HEAD) == 0 && b[8] == MSG_ALIVE)
      {
        heard = true;
      }
    }
    return heard;
  }

private:
  int mFd = -1;
};

class Helper
{
public:
  Helper()
    : mLink(NEVER_SHOWN_TEMPO)
    , mStarted(monotonic())
  {
    // Disabled from the start, and start and stop are never shared: tempo and beat only.
    mLink.enable(false);
    mLink.enableStartStopSync(false);
  }

  void setOn(const bool on)
  {
    if (on == mOn)
    {
      return;
    }
    mOn = on;
    if (on)
    {
      mStage = Stage::Alone;
      mWhy = mListener.open() ? "" : "the box cannot listen for Link peers";
      mListener.joinNetworks();
      mNextScan = monotonic() + RESCAN_EVERY;
    }
    else
    {
      mLink.enable(false);
      mListener.close();
      mStage = Stage::Off;
    }
  }

  bool fast() const
  {
    return mStage == Stage::Waiting || mStage == Stage::Joining || mStage == Stage::Session;
  }

  int listenerFd() const
  {
    return mListener.fd();
  }

  void step(const bool readable)
  {
    const double now = monotonic();
    const bool heard = readable && mListener.heardAlive() && now >= mIgnoreUntil;
    if (mOn && now >= mNextScan)
    {
      if (mListener.open())
      {
        mWhy = "";
      }
      mListener.joinNetworks();
      mNextScan = now + RESCAN_EVERY;
    }
    switch (mStage)
    {
    case Stage::Off:
      break;
    case Stage::Alone:
      if (heard)
      {
        mStage = Stage::Waiting;
        mSince = now;
      }
      break;
    case Stage::Waiting:
      if (now - mSince >= JOIN_WAIT)
      {
        mLink.enable(true);
        mStage = Stage::Joining;
        mSince = now;
      }
      break;
    case Stage::Joining:
      if (mLink.numPeers() > 0)
      {
        mStage = Stage::Session;
        mEmptySince = -1.0;
      }
      else if (now - mSince >= JOIN_LIMIT)
      {
        leave(now);
      }
      break;
    case Stage::Session:
      if (mLink.numPeers() > 0)
      {
        mEmptySince = -1.0;
      }
      else if (mEmptySince < 0.0)
      {
        mEmptySince = now;
      }
      else if (now - mEmptySince >= EMPTY_GRACE)
      {
        leave(now);
      }
      break;
    }
  }

  void publish(StateFile& file)
  {
    const std::uint32_t flags = (mOn ? F_ON : 0) | (mLink.isEnabled() ? F_ENABLED : 0);
    readSession();
    file.write(flags, mAt, mBpm, mPhase, mPeers);
  }

  std::string status() const
  {
    tms t;
    (void)times(&t);
    const double tick = static_cast<double>(sysconf(_SC_CLK_TCK));
    char out[256];
    std::snprintf(out, sizeof out,
      "{\"ok\": true, \"on\": %s, \"stage\": \"%s\", \"peers\": %u, \"bpm\": %.3f, "
      "\"why\": \"%s\", \"cpu_seconds\": %.2f, \"uptime\": %.1f}\n",
      mOn ? "true" : "false", stageName(mStage), mPeers, mBpm, mWhy,
      static_cast<double>(t.tms_utime + t.tms_stime) / tick, monotonic() - mStarted);
    return out;
  }

  void shutDown()
  {
    mLink.enable(false);
  }

private:
  void leave(const double now)
  {
    mLink.enable(false);
    mStage = Stage::Alone;
    mIgnoreUntil = now + QUIET_AFTER_LEAVE;
  }

  // Tempo and place are read for one instant of Link's clock, and that instant is named on the box's clock by
  // reading the box's on both sides of it: the two reads are a microsecond apart or so.
  void readSession()
  {
    mAt = monotonic();
    mPeers = 0;
    mBpm = 0.0;
    mPhase = 0.0;
    if (!mLink.isEnabled())
    {
      return;
    }
    const auto peers = mLink.numPeers();
    if (peers < 1)
    {
      return;       // Link's own tempo: a session of nobody says nothing
    }
    const auto session = mLink.captureAppSessionState();
    const double before = monotonic();
    const auto host = mLink.clock().micros();
    const double after = monotonic();
    mAt = 0.5 * (before + after);
    mPeers = static_cast<std::uint32_t>(peers);
    mBpm = session.tempo();
    mPhase = session.phaseAtTime(host, QUANTUM);
  }

  ableton::Link mLink;
  Listener mListener;
  Stage mStage = Stage::Off;
  bool mOn = false;
  const char* mWhy = "";
  double mStarted;
  double mSince = 0.0, mEmptySince = -1.0, mIgnoreUntil = 0.0, mNextScan = 0.0;
  double mAt = 0.0, mBpm = 0.0, mPhase = 0.0;
  std::uint32_t mPeers = 0;
};

// One line in, one line out. The panel sends json.dumps of a small object, so these are its exact bytes: this is no
// JSON parser, and anything else is an unknown command.
bool askedFor(const std::string& line, const char* word)
{
  return line == std::string("{\"cmd\": \"") + word + "\"}";
}

void answer(int fd, Helper& helper, const uid_t allowed)
{
  ucred who;
  socklen_t size = sizeof who;
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &who, &size) != 0 || who.uid != allowed)
  {
    const char no[] = "{\"ok\": false, \"error\": \"not allowed\"}\n";
    (void)!write(fd, no, sizeof no - 1);
    return;
  }
  std::string line;
  const double end = monotonic() + ASK_MS / 1000.0;
  while (line.size() <= MAX_LINE && line.find('\n') == std::string::npos)
  {
    const double left = end - monotonic();
    pollfd p = {fd, POLLIN, 0};
    if (left <= 0.0 || poll(&p, 1, static_cast<int>(left * 1000.0) + 1) <= 0)
    {
      return;
    }
    char b[64];
    const ssize_t n = read(fd, b, sizeof b);
    if (n <= 0)
    {
      return;
    }
    line.append(b, static_cast<std::size_t>(n));
  }
  const auto eol = line.find('\n');
  line.resize(eol == std::string::npos ? 0 : eol);
  std::string reply;
  if (askedFor(line, "on"))
  {
    helper.setOn(true);
    reply = helper.status();
  }
  else if (askedFor(line, "off"))
  {
    helper.setOn(false);
    reply = helper.status();
  }
  else if (askedFor(line, "status"))
  {
    reply = helper.status();
  }
  else
  {
    reply = "{\"ok\": false, \"error\": \"unknown command\"}\n";
  }
  (void)!write(fd, reply.data(), reply.size());
}

int openServer(const std::string& path)
{
  sockaddr_un where;
  std::memset(&where, 0, sizeof where);
  where.sun_family = AF_UNIX;
  if (path.size() >= sizeof where.sun_path)
  {
    return -1;
  }
  std::memcpy(where.sun_path, path.c_str(), path.size());
  (void)unlink(path.c_str());
  const int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
  if (fd < 0)
  {
    return -1;
  }
  if (bind(fd, reinterpret_cast<const sockaddr*>(&where), sizeof where) != 0
      || chmod(path.c_str(), 0660) != 0 || listen(fd, 4) != 0)
  {
    close(fd);
    return -1;
  }
  return fd;
}

} // namespace

int main()
{
  const char* env = std::getenv("PVJ_LINK_DIR");
  const std::string dir = env != nullptr && env[0] != '\0' ? env : "/run/pvj-link";
  const passwd* panel = getpwnam("pvj-web");
  const uid_t allowed = panel != nullptr ? panel->pw_uid : getuid();  // on a desk: whoever runs it

  struct sigaction act;
  std::memset(&act, 0, sizeof act);
  act.sa_handler = onSignal;
  sigaction(SIGTERM, &act, nullptr);
  sigaction(SIGINT, &act, nullptr);
  signal(SIGPIPE, SIG_IGN);

  StateFile file;
  if (!file.open(dir + "/state"))
  {
    std::fprintf(stderr, "pvj-link: cannot open %s/state: %s\n", dir.c_str(), std::strerror(errno));
    return 1;
  }
  const int server = openServer(dir + "/link.sock");
  if (server < 0)
  {
    std::fprintf(stderr, "pvj-link: cannot open %s/link.sock: %s\n", dir.c_str(), std::strerror(errno));
    return 1;
  }

  Helper helper;
  std::printf("pvj-link: ready; off until the owner switches it on, and it only ever follows\n");
  std::fflush(stdout);
  double last = 0.0;
  while (stopping == 0)
  {
    const double next = last + (helper.fast() ? RECORD_EVERY : IDLE_EVERY);
    const double wait = next - monotonic();
    pollfd fds[2] = {{server, POLLIN, 0}, {helper.listenerFd(), POLLIN, 0}};
    const int rc = poll(fds, helper.listenerFd() >= 0 ? 2 : 1, wait > 0.0 ? static_cast<int>(wait * 1000.0) + 1 : 0);
    const bool readable = rc > 0 && helper.listenerFd() >= 0 && (fds[1].revents & POLLIN) != 0;
    if (rc > 0 && (fds[0].revents & POLLIN) != 0)
    {
      const int fd = accept4(server, nullptr, nullptr, SOCK_CLOEXEC);
      if (fd >= 0)
      {
        answer(fd, helper, allowed);
        close(fd);
      }
    }
    helper.step(readable);
    const double now = monotonic();
    if (now >= next)
    {
      helper.publish(file);
      last = now;
    }
  }
  helper.shutDown();
  return 0;
}
