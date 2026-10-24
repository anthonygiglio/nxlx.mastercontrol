// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The control socket: mpv's JSON IPC protocol, a fixed subset (project-log/DECISIONS.md, D95). One JSON object a
// line, {"command": [...], "request_id": n}, answered {"request_id": n, "error": "success", "data": ...}. Anything
// outside the subset is answered with mpv's own words ("invalid parameter", "property not found"), so the panel's
// code treats it as it treats mpv refusing something. Phase 2 (D98) adds one command, pvj-crossfade (below), and
// every other command speaks to the video layer on the screen, so the panel still sees one player. Sources (D101) add
// pvj-source, which speaks to source 2 to N on a short list of its own, and the property pvj-sources; every other
// command and property still means source 1.
//
// The socket is reachable by the panel's group only (0660, as mpv's is made by the player's service). Still every
// line is bounded, every command and every name that may be set is on a fixed list, and nothing here can run a
// program, load a script or set an option the box does not use.
//
// Each client has a thread of its own (at most MAX_CLIENTS at once), as each of mpv's IPC clients has: a command
// may wait (a screenshot, a shader the render thread builds, libmpv itself), and a client that does not read its
// answers is waited on for SEND_SECONDS at most, then dropped. Neither holds up any other client, and none of it
// holds up the render thread. One thread for all, as before, let one slow client stop every other: on the first
// Pi run the listen queue filled and the panel's plays answered 503 (2026-10-18).
#define _GNU_SOURCE
#include "pvj.h"
#include "json.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define MAX_LINE (64 * 1024)
#define MAX_CLIENTS 32                  // at once; one more is closed at once (it hears nothing, as from a busy mpv)
#define BACKLOG 64
#define SEND_SECONDS 2                  // a client that has not taken an answer in this long is dropped
#define IDLE_SECONDS 30.0               // a client that has sent nothing in this long is dropped (nothing comes unasked)
#define CLIENT_STACK (512 * 1024)

static int listen_fd = -1;
static const char *sock_path;

typedef struct {
    int fd;                             // -1: a free slot
    pthread_t th;
    int finished;                       // set by its thread as it ends (atomics: the acceptor reads it)
} Client;

static Client clients[MAX_CLIENTS];
static int ipc_done;                    // the acceptor has ended and every client thread with it
static __thread int t_send_failed;      // this client's thread: an answer could not be sent; the client goes

int ipc_open(const char *path, char *err, size_t n)
{
    struct sockaddr_un sa = { .sun_family = AF_UNIX };
    if (strlen(path) >= sizeof sa.sun_path) { snprintf(err, n, "the socket's path is too long"); return -1; }
    strcpy(sa.sun_path, path);
    struct stat st;
    if (lstat(path, &st) == 0) {
        // an old socket of ours is replaced; anything else at that name is not ours to remove
        if (!S_ISSOCK(st.st_mode)) { snprintf(err, n, "something that is not a socket is at %s", path); return -1; }
        unlink(path);
    }
    listen_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (listen_fd < 0) { snprintf(err, n, "socket: %s", strerror(errno)); return -1; }
    if (bind(listen_fd, (struct sockaddr *)&sa, sizeof sa) || listen(listen_fd, BACKLOG)) {
        snprintf(err, n, "cannot listen on %s: %s", path, strerror(errno));
        close(listen_fd);
        listen_fd = -1;
        return -1;
    }
    // the panel (group pvj) must reach it; nobody else
    if (chmod(path, 0660)) LOG("could not open the socket to the group: %s", strerror(errno));
    sock_path = path;
    for (int i = 0; i < MAX_CLIENTS; i++) clients[i].fd = -1;
    return 0;
}

int ipc_finished(void) { return __atomic_load_n(&ipc_done, __ATOMIC_ACQUIRE); }

void ipc_close(void)
{
    // after ipc_thread has returned: every client's thread has ended and its socket is closed
    if (listen_fd >= 0) {
        close(listen_fd);
        listen_fd = -1;
        if (sock_path) unlink(sock_path);
    }
}

/* ---- the lists ---------------------------------------------------------------------------------------------------- */
// Names the panel may set, all on the video layer on the screen (glsl-shaders is handled apart). The names in MIRRORED
// are set on both video layers (D98): they are how the picture looks and sounds (the panel's opacity is
// `brightness`), so the clip a crossfade brings in has them from its first frame, and a Blackout or a fade of the
// level during a crossfade reaches both pictures. The others are the file's own (pause, loops, a live input's keys)
// and go to the layer on the screen only.
static const char *const SETTABLE[] = {
    "pause", "speed", "volume", "mute", "loop-file", "loop-playlist", "keep-open", "image-display-duration",
    "audio-device", "brightness", "video-zoom", "video-pan-x", "video-pan-y", "video-rotate", "video-crop",
    "keepaspect", "fbo-format", "screenshot-format", "screenshot-jpeg-quality", "screenshot-png-compression",
    "screenshot-png-filter", "screenshot-high-bit-depth",
    // a live input's pipe (pvj/player.py, _play_pipe, MKV_ONLY, PIPE_DEFAULTS)
    "demuxer", "demuxer-rawvideo-w", "demuxer-rawvideo-h", "demuxer-rawvideo-mp-format", "demuxer-rawvideo-fps",
    "cache", "cache-pause", "initial-audio-sync", "demuxer-readahead-secs", "demuxer-max-bytes", "vd", "ad", "sid",
    "ordered-chapters", "cover-art-auto", "embeddedfonts", "audio-delay", NULL };

static const char *const MIRRORED[] = {
    "speed", "volume", "mute", "audio-device", "brightness", "video-zoom", "video-pan-x", "video-pan-y", "video-rotate",
    "video-crop", "keepaspect", "fbo-format", "screenshot-format", "screenshot-jpeg-quality", "screenshot-png-compression",
    "screenshot-png-filter", "screenshot-high-bit-depth", NULL };

// Keys a crossfade's options may hold: what Player.play sets before a load, for the incoming clip alone.
static const char *const FADE_KEYS[] = { "keep-open", "image-display-duration", NULL };

// Sources 2 to N (D101): what may be set on one, and what of it may be read. `brightness` is also the panel's level:
// a set_property of it on the socket (opacity, Blackout, a fade) reaches every source, so a source's own brightness
// lasts until the next change of the level.
static const char *const SOURCE_SET[] = { "pause", "loop-file", "loop-playlist", "speed", "brightness", NULL };
static const char *const SOURCE_GET[] = { "path", "pause", "idle-active", "time-pos", "duration", "width", "height",
                                          "playlist-count", "playlist-pos", "loop-file", "loop-playlist", "speed",
                                          "brightness", "aid", "current-ao", "keepaspect", "fbo-format", NULL };
// Names set on source 1 that reach the other sources too: the picture's level, and the mapping's stretch of the
// picture to the whole frame with its 8-bit buffers (Player.set_mapping_mode). Without keepaspect a source's picture
// was letterboxed inside its texture, so a surface showed black bars where the picture's edge should be.
static const char *const TO_SOURCES[] = { "brightness", "keepaspect", "fbo-format", NULL };

// Keys loadfile's options may hold (the pipe's, set with the load on mpv 0.38 and later).
static const char *const LOAD_KEYS[] = {
    "demuxer", "demuxer-rawvideo-w", "demuxer-rawvideo-h", "demuxer-rawvideo-mp-format", "demuxer-rawvideo-fps",
    "cache", "cache-pause", "initial-audio-sync", "demuxer-readahead-secs", "demuxer-max-bytes", "audio-delay", NULL };

static int in_list(const char *const *list, const char *s)
{
    for (int i = 0; list[i]; i++)
        if (!strcmp(list[i], s)) return 1;
    return 0;
}

/* ---- replies -------------------------------------------------------------------------------------------------- */
static void reply(int fd, const mpv_node *id, const char *error, const mpv_node *data)
{
    Buf b;
    buf_init(&b);
    buf_str(&b, "{");
    if (data) {
        buf_str(&b, "\"data\":");
        json_write(&b, data);
        buf_str(&b, ",");
    }
    buf_str(&b, "\"error\":");
    json_write_string(&b, error);
    if (id) {
        buf_str(&b, ",\"request_id\":");
        json_write(&b, id);
    } else {
        buf_str(&b, ",\"request_id\":0");
    }
    buf_str(&b, "}\n");
    if (!b.failed && !t_send_failed) {
        size_t off = 0;
        while (off < b.len) {
            ssize_t w = send(fd, b.p + off, b.len - off, MSG_NOSIGNAL);
            if (w < 0 && errno == EINTR) continue;
            if (w <= 0) {
                // gone, or not reading (SO_SNDTIMEO ran out): an answer cut in two is no answer, so the client goes
                if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
                    LOG("ipc: a client took no answer for %d seconds; it is dropped", SEND_SECONDS);
                t_send_failed = 1;
                break;
            }
            off += (size_t)w;
        }
    }
    buf_free(&b);
}

static mpv_node node_int(int64_t v) { mpv_node n = { .format = MPV_FORMAT_INT64 }; n.u.int64 = v; return n; }
static mpv_node node_dbl(double v) { mpv_node n = { .format = MPV_FORMAT_DOUBLE }; n.u.double_ = v; return n; }
static mpv_node node_str(const char *s) { mpv_node n = { .format = MPV_FORMAT_STRING }; n.u.string = (char *)s; return n; }

static const mpv_node *arg(const mpv_node *cmd, int i)
{
    mpv_node_list *l = cmd->u.list;
    return i < l->num ? &l->values[i] : NULL;
}

static const char *arg_str(const mpv_node *cmd, int i)
{
    const mpv_node *a = arg(cmd, i);
    return a && a->format == MPV_FORMAT_STRING ? a->u.string : NULL;
}

static int is_num(const mpv_node *a) { return a && (a->format == MPV_FORMAT_INT64 || a->format == MPV_FORMAT_DOUBLE); }
static double num(const mpv_node *a) { return a->format == MPV_FORMAT_INT64 ? (double)a->u.int64 : a->u.double_; }

// Run a command on one of the libmpv instances and answer with its result.
static void forward(int fd, const mpv_node *id, mpv_handle *h, const mpv_node *cmd)
{
    mpv_node res;
    int r = mpv_command_node(h, (mpv_node *)cmd, &res);
    if (r < 0) {
        reply(fd, id, mpv_error_string(r), NULL);
        return;
    }
    reply(fd, id, "success", res.format == MPV_FORMAT_NONE ? NULL : &res);
    mpv_free_node_contents(&res);
}

static int plain_value(const char *v)
{
    if (!*v || strlen(v) > 64) return 0;
    for (const char *c = v; *c; c++)
        if (!((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') || *c == '.' || *c == '-' || *c == '_'))
            return 0;
    return 1;
}

// loadfile's per-file options: "k=v,k=v", every key from LOAD_KEYS and every value plain.
static int options_ok(const char *s, const char *const *keys);
static int load_options_ok(const char *s) { return options_ok(s, LOAD_KEYS); }

static int options_ok(const char *s, const char *const *keys)
{
    if (strlen(s) > 1024) return 0;
    char *copy = strdup(s), *save = NULL;
    if (!copy) return 0;
    int ok = 1;
    for (char *part = strtok_r(copy, ",", &save); part && ok; part = strtok_r(NULL, ",", &save)) {
        char *eq = strchr(part, '=');
        if (!eq) { ok = 0; break; }
        *eq = 0;
        ok = in_list(keys, part) && plain_value(eq + 1);
    }
    free(copy);
    return ok;
}

static int seek_flags_ok(const char *s)
{
    static const char *const words[] = { "relative", "absolute", "absolute-percent", "relative-percent", "exact", "keyframes", NULL };
    char *copy = strdup(s), *save = NULL;
    if (!copy) return 0;
    int ok = *s != 0;
    for (char *w = strtok_r(copy, "+", &save); w && ok; w = strtok_r(NULL, "+", &save)) ok = in_list(words, w);
    free(copy);
    return ok;
}

/* ---- the two video layers (D98) ------------------------------------------------------------------------------------ */
// One change of the layers' roles at a time: a crossfade, and the loads and stops that end one, take this lock
// (never the render thread). Under it a client's thread may wait on libmpv; app.lock is only taken for moments.
static pthread_mutex_t layer_lock = PTHREAD_MUTEX_INITIALIZER;

// A name of how the picture looks, set on the layer on the screen and then on the other one. The answer is the
// front's; the other layer's refusal is only logged (it is idle or on its way out, and gets the value again with
// the next set).
static int set_both(const char *name, const mpv_node *v)
{
    int f = front_layer();
    int r = mpv_set_property(app.layer[f], name, MPV_FORMAT_NODE, (void *)v);
    if (r >= 0) {
        int r2 = mpv_set_property(app.layer[1 - f], name, MPV_FORMAT_NODE, (void *)v);
        if (r2 < 0 && app.cfg->verbose) LOG("ipc: %s on the second layer: %s", name, mpv_error_string(r2));
        // the level reaches sources 2 to N too (D101): a Blackout darkens every picture the mapping shows
        for (int k = LAYERS; in_list(TO_SOURCES, name) && k < LAYERS + app.nsources - 1; k++) {
            int r3 = mpv_set_property(app.layer[k], name, MPV_FORMAT_NODE, (void *)v);
            if (r3 < 0 && app.cfg->verbose) LOG("ipc: %s on source %d: %s", name, k, mpv_error_string(r3));
        }
    }
    return r;
}

// The same for a command on the video filters (a flip), whose answer is the front's.
static int command_both(const mpv_node *cmd, mpv_node *res)
{
    int f = front_layer();
    int r = mpv_command_node(app.layer[f], (mpv_node *)cmd, res);
    mpv_node other;
    if (mpv_command_node(app.layer[1 - f], (mpv_node *)cmd, &other) >= 0) mpv_free_node_contents(&other);
    return r;
}

static int set_str(mpv_handle *h, const char *name, const char *v) { return mpv_set_property_string(h, name, v); }

// A crossfade that is on its way ends now (under layer_lock): its incoming clip is the picture from here on, the
// outgoing layer is stopped and idle. Called before anything that changes what the front plays (a load, a stop, a
// step in its list, a new crossfade), so nothing is ever mixed with a picture nobody asked for.
static void end_fade(void)
{
    pthread_mutex_lock(&app.lock);
    int was = app.fade.phase != FADE_OFF, back = 1 - app.front;
    if (was) {
        app.fade.phase = FADE_OFF;
        app.fades_cut++;
    }
    pthread_mutex_unlock(&app.lock);
    if (was) {
        const char *stop[] = { "stop", NULL };
        mpv_command(app.layer[back], stop);
        wake_render();
    }
}

// ["pvj-crossfade", URL, SECONDS, OPTIONS?]: the incoming clip is loaded into the idle layer, which is the one the
// socket speaks to from this moment (path, pause, time-pos, the loops, a seek: the new clip's, as after a loadfile);
// the outgoing clip plays on under it, silent (its sound stops at the tap, as D71's freeze stopped it: one sound
// device, one player on it at a time), and the render thread mixes the two from the incoming clip's first frame on,
// over SECONDS, then stops the outgoing one. OPTIONS: "keep-open=..,image-display-duration=.." for the incoming clip.
//
// With nothing loaded on the screen (after a Stop, at power-up) there is nothing to fade from: the clip is loaded into
// the front layer as a loadfile would load it, which is a cut, as D71 starts the clip "as with a cut" there.
//
// The sound is handed over in an order that never has two cores on the device at once: the incoming clip is loaded
// with no sound track, the outgoing layer's sound is taken off, this thread waits (at most SOUND_WAIT seconds) until
// that core has closed its audio output (`current-ao` unavailable), and only then is the incoming clip given its sound.
#define SOUND_WAIT 1.0

// "k=v,k=v" (already checked against FADE_KEYS) set on `h`; the first refusal is the answer
static int apply_options(mpv_handle *h, const char *opts)
{
    if (!opts) return 0;
    char *copy = strdup(opts), *save = NULL;
    if (!copy) return MPV_ERROR_NOMEM;
    int r = 0;
    for (char *part = strtok_r(copy, ",", &save); part && r >= 0; part = strtok_r(NULL, ",", &save)) {
        char *eq = strchr(part, '=');
        *eq = 0;
        r = set_str(h, part, eq + 1);
    }
    free(copy);
    return r;
}

static int has_audio_out(mpv_handle *h)
{
    char *ao = mpv_get_property_string(h, "current-ao");
    int open = ao != NULL;
    mpv_free(ao);
    return open;
}

static void crossfade(int fd, const mpv_node *id, const mpv_node *cmd)
{
    int nargs = cmd->u.list->num;
    const char *url = arg_str(cmd, 1);
    const mpv_node *secs = arg(cmd, 2);
    const char *opts = nargs == 4 ? arg_str(cmd, 3) : NULL;
    double s = is_num(secs) ? num(secs) : -1;
    if (!url || !*url || strlen(url) > 4096 || !(s >= 0 && s <= FADE_MAX_SECONDS) || (nargs == 4 && (!opts || !options_ok(opts, FADE_KEYS)))) {
        reply(fd, id, "invalid parameter", NULL);
        return;
    }
    const char *load[] = { "loadfile", url, "replace", NULL };
    pthread_mutex_lock(&layer_lock);
    end_fade();
    int out = front_layer(), in = 1 - out;
    mpv_handle *H = app.layer[in], *O = app.layer[out];
    int idle = 0;
    if (mpv_get_property(O, "idle-active", MPV_FORMAT_FLAG, &idle) >= 0 && idle) {
        // nothing on the screen to fade from: a cut, into the front layer
        set_str(O, "pause", "no");
        set_str(O, "keep-open", "no");
        int r = apply_options(O, opts);
        if (r >= 0) r = mpv_command(O, load);
        pthread_mutex_lock(&app.lock);
        if (r >= 0) app.fades_from_nothing++;
        pthread_mutex_unlock(&app.lock);
        pthread_mutex_unlock(&layer_lock);
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        return;
    }
    // the incoming layer as a fresh player for this clip: playing, no sound yet (below), and the file's own options
    set_str(H, "pause", "no");
    set_str(H, "keep-open", "no");
    set_str(H, "aid", "no");
    int r = apply_options(H, opts);
    int base = __atomic_load_n(&app.starts[in], __ATOMIC_ACQUIRE);
    char *kept = NULL;
    if (r >= 0) {
        // the outgoing clip keeps its last frame if it ends during the fade, instead of going idle and black; it is
        // stopped at the end anyway
        kept = mpv_get_property_string(O, "keep-open");
        set_str(O, "keep-open", "yes");
        r = mpv_command(H, load);
        if (r < 0 && kept) set_str(O, "keep-open", kept);      // refused: nothing changed on the screen
    }
    mpv_free(kept);
    if (r < 0) {
        pthread_mutex_unlock(&layer_lock);
        LOG("ipc: pvj-crossfade: the incoming clip was not loaded: %s", mpv_error_string(r));
        reply(fd, id, mpv_error_string(r), NULL);
        return;
    }
    // the sound: off the outgoing core, its output closed, then on the incoming one
    int ro = set_str(O, "aid", "no");
    if (ro < 0) LOG("ipc: pvj-crossfade: the outgoing clip's sound could not be taken off: %s", mpv_error_string(ro));
    double until = now_s() + SOUND_WAIT;
    while (has_audio_out(O) && now_s() < until) {
        struct timespec ts = { 0, 5 * 1000 * 1000 };
        nanosleep(&ts, NULL);
    }
    if (has_audio_out(O)) {
        LOG("ipc: pvj-crossfade: the outgoing clip still holds the sound device after %.1f s; the incoming clip may be silent", SOUND_WAIT);
        pthread_mutex_lock(&app.lock);
        app.sound_held++;
        pthread_mutex_unlock(&app.lock);
    }
    set_str(H, "aid", "auto");
    pthread_mutex_lock(&app.lock);
    app.fade.phase = FADE_WAITING;
    app.fade.seconds = s;
    app.fade.asked = now_s();
    app.fade.progress = 0.0;
    app.fade.base_starts = base;
    app.fades++;
    __atomic_store_n(&app.front, in, __ATOMIC_RELEASE);
    pthread_mutex_unlock(&app.lock);
    pthread_mutex_unlock(&layer_lock);
    wake_render();
    reply(fd, id, "success", NULL);
}

// A command that changes what the front plays: a crossfade on its way ends first (end_fade), then the command goes
// to the front.
// The answer is sent after the lock is let go: a client that does not read must not hold up the layers.
static void forward_front(int fd, const mpv_node *id, const mpv_node *cmd, int ends_fade)
{
    mpv_node res;
    pthread_mutex_lock(&layer_lock);
    if (ends_fade) end_fade();
    int r = mpv_command_node(front_handle(), (mpv_node *)cmd, &res);
    pthread_mutex_unlock(&layer_lock);
    if (r < 0) {
        reply(fd, id, mpv_error_string(r), NULL);
        return;
    }
    reply(fd, id, "success", res.format == MPV_FORMAT_NONE ? NULL : &res);
    mpv_free_node_contents(&res);
}

/* ---- sources 2 to N (D101) ------------------------------------------------------------------------------------------ */
// ["pvj-source", N, "loadfile", URL, FLAG?, OPTIONS?]  FLAG: replace (the default), append or append-play; OPTIONS:
//                                                      "keep-open=..,image-display-duration=.." set on that source
//                                                      before the load (it has nobody else's file to disturb)
// ["pvj-source", N, "stop"]
// ["pvj-source", N, "set_property", NAME, VALUE]       NAME from SOURCE_SET
// ["pvj-source", N, "get_property", NAME]              NAME from SOURCE_GET
// N is 2 to the number of sources this run has. Source 1 is spoken to by every other command, as before. A load
// here is a cut: the crossfade (D98) is source 1's alone in this step. The source plays with no sound (aid=no from
// its start, and aid is on no list). A source's core is its own: nothing here takes layer_lock, and the render
// thread is never waited on.
static int source_level_native(int s)
{
    pthread_mutex_lock(&app.lock);
    int on = app.level_native[s];
    pthread_mutex_unlock(&app.lock);
    return on;
}

static void source_command(int fd, const mpv_node *id, const mpv_node *cmd)
{
    int nargs = cmd->u.list->num;
    const mpv_node *n = arg(cmd, 1);
    const char *what = arg_str(cmd, 2);
    if (!n || n->format != MPV_FORMAT_INT64 || n->u.int64 < 2 || n->u.int64 > app.nsources || !what) {
        reply(fd, id, "invalid parameter", NULL);
        return;
    }
    int k = (int)n->u.int64;
    mpv_handle *h = app.layer[k];
    if (!strcmp(what, "loadfile") && nargs >= 4 && nargs <= 6) {
        static const char *const flags[] = { "replace", "append", "append-play", NULL };
        const char *url = arg_str(cmd, 3);
        const char *flag = nargs >= 5 ? arg_str(cmd, 4) : "replace";
        const char *opts = nargs == 6 ? arg_str(cmd, 5) : NULL;
        if (!url || !*url || strlen(url) > 4096 || !flag || !in_list(flags, flag) || (nargs == 6 && (!opts || !options_ok(opts, FADE_KEYS)))) {
            reply(fd, id, "invalid parameter", NULL);
            return;
        }
        int r = apply_options(h, opts);
        const char *load[] = { "loadfile", url, flag, NULL };
        if (r >= 0) r = mpv_command(h, load);
        if (r >= 0) __atomic_add_fetch(&app.source_loads[k], 1, __ATOMIC_RELAXED);
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
    } else if (!strcmp(what, "stop") && nargs == 3) {
        const char *stop[] = { "stop", NULL };
        int r = mpv_command(h, stop);
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
    } else if (!strcmp(what, "set_property") && nargs == 5 && arg_str(cmd, 3) && !strcmp(arg_str(cmd, 3), "opacity")) {
        // the source's mix (D101): read by the render loop once a frame
        const mpv_node *v = arg(cmd, 4);
        double o = is_num(v) ? num(v) : -1;
        if (!(o >= 0.0 && o <= 1.0)) { reply(fd, id, "invalid parameter", NULL); return; }
        pthread_mutex_lock(&app.lock);
        app.opacity[k] = (float)o;
        pthread_mutex_unlock(&app.lock);
        wake_render();
        reply(fd, id, "success", NULL);
    } else if (!strcmp(what, "set_property") && nargs == 5 && arg_str(cmd, 3) && !strcmp(arg_str(cmd, 3), "blend")) {
        const char *b = arg_str(cmd, 4);
        int found = -1;
        for (int i = 0; b && i < BLENDS; i++) if (!strcmp(b, BLEND_NAMES[i])) found = i;
        if (found < 0) { reply(fd, id, "invalid parameter", NULL); return; }
        pthread_mutex_lock(&app.lock);
        app.blend[k] = found;
        pthread_mutex_unlock(&app.lock);
        reply(fd, id, "success", NULL);
    } else if (!strcmp(what, "get_property") && nargs == 4 && arg_str(cmd, 3)
               && (!strcmp(arg_str(cmd, 3), "opacity") || !strcmp(arg_str(cmd, 3), "blend"))) {
        pthread_mutex_lock(&app.lock);
        double o = app.opacity[k];
        int b = app.blend[k];
        pthread_mutex_unlock(&app.lock);
        mpv_node d = !strcmp(arg_str(cmd, 3), "opacity") ? node_dbl(o) : node_str(BLEND_NAMES[b]);
        reply(fd, id, "success", &d);
    } else if (!strcmp(what, "set_property") && nargs == 5 && arg_str(cmd, 3) && !strcmp(arg_str(cmd, 3), "brightness")
               && is_num(arg(cmd, 4)) && num(arg(cmd, 4)) >= -100 && num(arg(cmd, 4)) <= 100) {
        // the source's own level (D101), kept (D106): while it has a program on, the renderer draws it after them
        pthread_mutex_lock(&layer_lock);
        pthread_mutex_lock(&app.lock);
        int native = app.level_native[k - 1];
        pthread_mutex_unlock(&app.lock);
        int r = native ? 0 : mpv_set_property(h, "brightness", MPV_FORMAT_NODE, (void *)arg(cmd, 4));
        if (r >= 0) {                       // kept only when mpv took it (a refused value changes nothing)
            pthread_mutex_lock(&app.lock);
            app.level[k - 1] = num(arg(cmd, 4));
            pthread_mutex_unlock(&app.lock);
        }
        pthread_mutex_unlock(&layer_lock);
        wake_render();
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
    } else if (!strcmp(what, "get_property") && nargs == 4 && arg_str(cmd, 3) && !strcmp(arg_str(cmd, 3), "brightness")
               && source_level_native(k - 1)) {
        pthread_mutex_lock(&app.lock);
        mpv_node d = node_dbl(app.level[k - 1]);
        pthread_mutex_unlock(&app.lock);
        reply(fd, id, "success", &d);
    } else if (!strcmp(what, "set_property") && nargs == 5 && arg_str(cmd, 3)) {
        if (!in_list(SOURCE_SET, arg_str(cmd, 3))) {
            LOG("ipc: pvj-source set_property %s is not on the list", arg_str(cmd, 3));
            reply(fd, id, "property not found", NULL);
            return;
        }
        int r = mpv_set_property(h, arg_str(cmd, 3), MPV_FORMAT_NODE, (void *)arg(cmd, 4));
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
    } else if (!strcmp(what, "get_property") && nargs == 4 && arg_str(cmd, 3)) {
        if (!in_list(SOURCE_GET, arg_str(cmd, 3))) { reply(fd, id, "property not found", NULL); return; }
        mpv_node res;
        int r = mpv_get_property(h, arg_str(cmd, 3), MPV_FORMAT_NODE, &res);
        if (r < 0) { reply(fd, id, mpv_error_string(r), NULL); return; }
        reply(fd, id, "success", &res);
        mpv_free_node_contents(&res);
    } else {
        reply(fd, id, "invalid parameter", NULL);
    }
}

// get_property pvj-sources: one map a source, from 1. What each plays, whether it is idle, the frames it converted
// and its picture's height (the panel's warning about load). Asks the cores (a client's thread may): never read to
// time anything.
static void sources_property(int fd, const mpv_node *id)
{
    static const char *const keys[] = { "source", "idle", "path", "drawn", "height", "pause", "loads", "opacity", "blend" };
    mpv_node maps[MAX_SOURCES], vals[MAX_SOURCES][9];
    float opacity[MAX_LAYERS];
    int blend[MAX_LAYERS];
    pthread_mutex_lock(&app.lock);
    memcpy(opacity, app.opacity, sizeof opacity);
    memcpy(blend, app.blend, sizeof blend);
    pthread_mutex_unlock(&app.lock);
    mpv_node_list lists[MAX_SOURCES];
    char *paths[MAX_SOURCES] = { 0 };
    int ns = app.nsources;
    for (int s = 1; s <= ns; s++) {
        int li = s == 1 ? front_layer() : s;
        mpv_handle *h = app.layer[li];
        int idle = __atomic_load_n(&app.idle[li], __ATOMIC_ACQUIRE);
        paths[s - 1] = idle ? NULL : mpv_get_property_string(h, "path");
        int64_t height = 0;
        int have_h = !idle && mpv_get_property(h, "height", MPV_FORMAT_INT64, &height) >= 0;
        int pause = 0;
        mpv_get_property(h, "pause", MPV_FORMAT_FLAG, &pause);
        long drawn = s == 1 ? __atomic_load_n(&app.layer_drawn[0], __ATOMIC_RELAXED) + __atomic_load_n(&app.layer_drawn[1], __ATOMIC_RELAXED)
                            : __atomic_load_n(&app.layer_drawn[li], __ATOMIC_RELAXED);
        mpv_node *v = vals[s - 1];
        v[0] = node_int(s);
        v[1] = (mpv_node){ .format = MPV_FORMAT_FLAG };
        v[1].u.flag = idle;
        v[2] = paths[s - 1] ? node_str(paths[s - 1]) : (mpv_node){ .format = MPV_FORMAT_NONE };
        v[3] = node_int(drawn);
        v[4] = have_h ? node_int(height) : (mpv_node){ .format = MPV_FORMAT_NONE };
        v[5] = (mpv_node){ .format = MPV_FORMAT_FLAG };
        v[5].u.flag = pause;
        v[6] = node_int(s == 1 ? 0 : __atomic_load_n(&app.source_loads[li], __ATOMIC_RELAXED));
        v[7] = node_dbl(s == 1 ? 1.0 : opacity[li]);         // source 1 is the program: its level is the Mix's
        v[8] = node_str(BLEND_NAMES[s == 1 ? BLEND_NORMAL : blend[li]]);
        lists[s - 1] = (mpv_node_list){ .num = 9, .values = v, .keys = (char **)keys };
        maps[s - 1].format = MPV_FORMAT_NODE_MAP;
        maps[s - 1].u.list = &lists[s - 1];
    }
    mpv_node_list all = { .num = ns, .values = maps };
    mpv_node d = { .format = MPV_FORMAT_NODE_ARRAY };
    d.u.list = &all;
    reply(fd, id, "success", &d);
    for (int s = 0; s < ns; s++) mpv_free(paths[s]);
}

/* ---- the render thread's parts ------------------------------------------------------------------------------------ */
static int wait_render(int (*done)(void), double seconds)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    long long ns = ts.tv_nsec + (long long)(seconds * 1e9);
    ts.tv_sec += (time_t)(ns / 1000000000LL);
    ts.tv_nsec = (long)(ns % 1000000000LL);
    while (!done() && !app.quit)
        if (pthread_cond_timedwait(&app.cond, &app.lock, &ts) == ETIMEDOUT) break;
    return done();
}

static int hook_taken(void) { return !app.hook_pending; }
static int capture_taken(void) { return app.capture_done_serial == app.capture_serial; }

static void set_shaders(int fd, const mpv_node *id, const mpv_node *val)
{
    const char *paths[MAX_SHADERS];
    int np = 0;
    if (val && val->format == MPV_FORMAT_STRING && !*val->u.string) {
        np = 0;
    } else if (val && val->format == MPV_FORMAT_NODE_ARRAY) {
        mpv_node_list *l = val->u.list;
        if (l->num > MAX_SHADERS) { reply(fd, id, "invalid parameter", NULL); return; }
        for (int i = 0; i < l->num; i++) {
            if (l->values[i].format != MPV_FORMAT_STRING || l->values[i].u.string[0] != '/') { reply(fd, id, "invalid parameter", NULL); return; }
            paths[np++] = l->values[i].u.string;
        }
    } else {
        reply(fd, id, "invalid parameter", NULL);
        return;
    }
    Hook *ours = NULL;
    mpv_node items[MAX_SHADERS];
    int nmpv = 0;
    for (int i = 0; i < np; i++) {
        Hook *h = NULL;
        char err[200] = "";
        int r = hook_load(paths[i], &h, err, sizeof err);
        if (r < 0 || (r == 1 && ours)) {
            LOG("glsl-shaders: %s refused: %s", paths[i], r < 0 ? err : "a second final pass");
            hook_free(h);
            hook_free(ours);
            reply(fd, id, "invalid parameter", NULL);
            return;
        }
        if (r == 1) ours = h;
        else items[nmpv++] = node_str(paths[i]);
    }
    mpv_node_list list = { .num = nmpv, .values = items };
    mpv_node arr = { .format = MPV_FORMAT_NODE_ARRAY };
    arr.u.list = &list;
    int r = set_both("glsl-shaders", &arr);
    if (r < 0) {
        hook_free(ours);
        reply(fd, id, mpv_error_string(r), NULL);
        return;
    }
    pthread_mutex_lock(&app.lock);
    for (int i = 0; i < app.nshaders; i++) free(app.shaders[i]);
    app.nshaders = 0;
    for (int i = 0; i < np; i++) {
        app.shaders[i] = strdup(paths[i]);
        if (app.shaders[i]) app.nshaders = i + 1;
    }
    if (app.hook_pending) hook_free(app.hook_next);
    app.hook_next = ours;
    app.hook_pending = 1;
    pthread_mutex_unlock(&app.lock);
    wake_render();
    pthread_mutex_lock(&app.lock);
    wait_render(hook_taken, 3.0);
    pthread_mutex_unlock(&app.lock);
    reply(fd, id, "success", NULL);
}

/* ---- the renderer's own programs (D106) ---------------------------------------------------------------------------- */
// ["pvj-shader", "load", SOURCE, SLOT, PATH]   a program file (fx.c) into a slot: 0 the generator, 1 the effect.
//                                              Answered once the render thread has built it (at most 3 s): "success",
//                                              or why the GPU refused it (the program before it then runs on)
// ["pvj-shader", "clear", SOURCE, SLOT]
// ["pvj-shader", "set", SOURCE, SLOT, {NAME: VALUE, ...}]   inputs by name, and @speed, @hue, @gain, @amount, @offset;
//                                              all of one set are taken in one frame. Nothing is built again.
// SOURCE is 1 to the number of sources; slot 0 (a generator) is source 1's only (generators as sources: step d),
// slots 1 to 3 are a source's effects, run in that order (step b).
static int fx_answered_s, fx_answered_k;
static long fx_wait_for;
static int fx_answered(void) { return app.fx[fx_answered_s][fx_answered_k].answered >= fx_wait_for; }

// The video layers of source index s: source 1's two, or source k's one (layer k).
static int source_layers(int s, int *first)
{
    *first = s == 0 ? 0 : s + 1;
    return s == 0 ? LAYERS : 1;
}

// A source's level goes with whether it has a program on: while it has, its layers' own brightness is 0 and the
// renderer draws the level after the programs; otherwise the layers have the level, as before (under layer_lock). The
// order keeps the level from ever being absent (the review of #167). The render thread draws a source's level while it
// has a program on OR while its layers still have 0 (`level_native`, main.c). A program that comes on is drawn with
// the level at once (for a moment the layers have it too: darker, never lighter), and the layers get 0 after. A
// program that goes off: the render thread goes on drawing the level until the layers have it back, and only then is
// level_native cleared (again a moment of both, never of none).
static void level_sync(int s)
{
    pthread_mutex_lock(&app.lock);
    int want = app.native[s], had = app.level_native[s];
    double level = app.level[s];
    if (want) app.level_native[s] = 1;
    pthread_mutex_unlock(&app.lock);
    if (want == had) return;
    double v = want ? 0.0 : level;
    int first, n = source_layers(s, &first);
    for (int i = first; i < first + n; i++) mpv_set_property(app.layer[i], "brightness", MPV_FORMAT_DOUBLE, &v);
    if (!want) {
        pthread_mutex_lock(&app.lock);
        app.level_native[s] = 0;
        pthread_mutex_unlock(&app.lock);
    }
    wake_render();
}

static pthread_mutex_t fx_lock = PTHREAD_MUTEX_INITIALIZER;     // one load or clear at a time (it waits on the render thread)

static void shader_command(int fd, const mpv_node *id, const mpv_node *cmd)
{
    int nargs = cmd->u.list->num;
    const char *what = arg_str(cmd, 1);
    const mpv_node *src = arg(cmd, 2), *slot = arg(cmd, 3);
    if (!what || !src || !slot || src->format != MPV_FORMAT_INT64 || slot->format != MPV_FORMAT_INT64 || src->u.int64 < 1
        || src->u.int64 > app.nsources || slot->u.int64 < 0 || slot->u.int64 >= FX_SLOTS || (slot->u.int64 == 0 && src->u.int64 != 1)) {
        reply(fd, id, "invalid parameter", NULL);
        return;
    }
    int si = (int)src->u.int64 - 1, k = (int)slot->u.int64;
    if (!strcmp(what, "set") && nargs == 5) {
        const mpv_node *m = arg(cmd, 4);
        if (m->format != MPV_FORMAT_NODE_MAP || m->u.list->num < 1 || m->u.list->num > FX_MAX_INPUTS + FXC_N) {
            reply(fd, id, "invalid parameter", NULL);
            return;
        }
        pthread_mutex_lock(&app.lock);
        FxSlot *s = &app.fx[si][k];
        // all or nothing: checked on a copy first
        FxSlot copy;
        memcpy(copy.val, s->val, sizeof copy.val);
        memcpy(copy.ctl, s->ctl, sizeof copy.ctl);
        copy.ninputs = s->ninputs;
        copy.reset_time = 0;
        int ok = s->on;
        for (int i = 0; ok && i < m->u.list->num; i++) ok = fx_set_value(&copy, m->u.list->keys[i], &m->u.list->values[i]) == 0;
        if (ok) {
            memcpy(s->val, copy.val, sizeof s->val);
            memcpy(s->ctl, copy.ctl, sizeof s->ctl);
            if (copy.reset_time) s->reset_time = 1;
            s->values_serial++;
        }
        pthread_mutex_unlock(&app.lock);
        if (ok) wake_render();
        reply(fd, id, ok ? "success" : "invalid parameter", NULL);
        return;
    }
    FxProg *p = NULL;
    if (!strcmp(what, "load") && nargs == 5 && arg_str(cmd, 4) && arg_str(cmd, 4)[0] == '/' && strlen(arg_str(cmd, 4)) < 4000) {
        char err[300] = "";
        if (fx_read(arg_str(cmd, 4), &p, err, sizeof err)) {
            LOG("pvj-shader: %s refused: %s", arg_str(cmd, 4), err);
            reply(fd, id, "invalid parameter", NULL);
            return;
        }
        if (fx_prog_kind(p) != (k == 0 ? FX_GENERATOR : FX_FILTER)) {
            fx_prog_free(p);
            reply(fd, id, "invalid parameter", NULL);       // a generator goes to slot 0, an effect to slot 1
            return;
        }
    } else if (!(!strcmp(what, "clear") && nargs == 4)) {
        reply(fd, id, "invalid parameter", NULL);
        return;
    }
    pthread_mutex_lock(&fx_lock);
    pthread_mutex_lock(&app.lock);
    FxSlot *s = &app.fx[si][k];
    fx_prog_free(s->pending);
    s->pending = p;
    s->pending_clear = p == NULL;
    long mine = ++s->asked;
    pthread_mutex_unlock(&app.lock);
    wake_render();
    pthread_mutex_lock(&app.lock);
    fx_answered_s = si;
    fx_answered_k = k;
    fx_wait_for = mine;
    int done = wait_render(fx_answered, 3.0);
    char answer[sizeof s->answer];
    memcpy(answer, s->answer, sizeof answer);
    pthread_mutex_unlock(&app.lock);
    pthread_mutex_lock(&layer_lock);
    level_sync(si);
    pthread_mutex_unlock(&layer_lock);
    pthread_mutex_unlock(&fx_lock);
    if (!done) reply(fd, id, "the renderer did not build the program in time", NULL);
    else reply(fd, id, answer[0] ? answer : "success", NULL);
}

// get_property pvj-shaders: one map a slot, source 1's first (slots 0 to 3), then each other source's.
static void shaders_property(int fd, const mpv_node *id)
{
    static const char *const keys[] = { "source", "slot", "kind", "state", "time", "runs", "drops", "inputs", "rebuilds", "width", "height", "path" };
    enum { NK = sizeof keys / sizeof keys[0] };
    enum { NI = MAX_SOURCES * FX_SLOTS };
    static __thread mpv_node vals[NI][NK];
    static __thread mpv_node_list lists[NI];
    static __thread mpv_node items[NI];
    static __thread char states[NI][160], paths[NI][1024];
    int ni = app.nsources * FX_SLOTS;
    pthread_mutex_lock(&app.lock);
    for (int i = 0; i < ni; i++) {
        int si = i / FX_SLOTS, k = i % FX_SLOTS;
        const FxSlot *s = &app.fx[si][k];
        snprintf(states[i], sizeof states[i], "%s", s->on ? s->state : "off");
        snprintf(paths[i], sizeof paths[i], "%s", s->on ? s->path : "");
        int w = s->w, h = s->h;
        mpv_node v[NK] = { node_int(si + 1), node_int(k), node_str(!s->on ? "" : s->kind == FX_GENERATOR ? "generator" : "filter"),
                           node_str(states[i]), node_dbl(s->time), node_int(s->runs), node_int(s->drops), node_int(s->on ? s->ninputs : 0),
                           node_int(s->rebuilds), node_int(s->on ? w : 0), node_int(s->on ? h : 0), node_str(paths[i]) };
        memcpy(vals[i], v, sizeof v);
        lists[i] = (mpv_node_list){ .num = NK, .values = vals[i], .keys = (char **)keys };
        items[i].format = MPV_FORMAT_NODE_MAP;
        items[i].u.list = &lists[i];
    }
    pthread_mutex_unlock(&app.lock);
    mpv_node_list list = { .num = ni, .values = items };
    mpv_node d = { .format = MPV_FORMAT_NODE_ARRAY };
    d.u.list = &list;
    reply(fd, id, "success", &d);
}

// screenshot-to-file while a program is on: the renderer's own picture. The render thread reads it back (main.c,
// native_shot); this client's thread encodes it and writes the file, so the screen never waits for an encoder (the
// review of #167). One screenshot at a time: two clients never take each other's picture.
static pthread_mutex_t shot_lock = PTHREAD_MUTEX_INITIALIZER;
static int shot_want;
static int shot_taken(void) { return app.shot.done_serial == shot_want; }

// The file the panel names (and has made, in its own folder): written over, never through a link, never a FIFO or a
// device (opened without waiting, checked to be a regular file, then emptied).
static int shot_write(const char *path, const unsigned char *rgba, int w, int h, int jpeg, int quality)
{
    unsigned char *rgb = malloc((size_t)w * (size_t)h * 3);
    if (!rgb) return -1;
    for (int y = 0; y < h; y++) {               // top row first: the pass drew the picture's top at the last GL row
        const unsigned char *s = rgba + (size_t)(h - 1 - y) * (size_t)w * 4;
        unsigned char *d = rgb + (size_t)y * (size_t)w * 3;
        for (int x = 0; x < w; x++) { d[x * 3] = s[x * 4]; d[x * 3 + 1] = s[x * 4 + 1]; d[x * 3 + 2] = s[x * 4 + 2]; }
    }
    int r = -1;
    int fd = open(path, O_WRONLY | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0640);
    struct stat st;
    if (fd < 0) LOG("screenshot: %s: %s", path, strerror(errno));
    else if (fstat(fd, &st) || !S_ISREG(st.st_mode)) LOG("screenshot: %s is not a file", path);
    else if (ftruncate(fd, 0)) LOG("screenshot: %s: %s", path, strerror(errno));
    else r = jpeg ? still_jpeg(fd, rgb, w, h, quality) : still_png(fd, rgb, w, h);
    if (fd >= 0 && close(fd)) r = -1;
    free(rgb);
    return r;
}

static int native_screenshot(int fd, const mpv_node *id, const char *path, const char *kind)
{
    pthread_mutex_lock(&app.lock);
    int on = app.native[0];
    pthread_mutex_unlock(&app.lock);
    if (!on) return 0;
    size_t n = strlen(path);
    int jpeg = (n > 4 && !strcasecmp(path + n - 4, ".jpg")) || (n > 5 && !strcasecmp(path + n - 5, ".jpeg"));
    int png = n > 4 && !strcasecmp(path + n - 4, ".png");
    if ((!jpeg && !png) || n > 4000) { reply(fd, id, "invalid parameter", NULL); return 1; }
    int64_t q = 90;
    if (mpv_get_property(front_handle(), "screenshot-jpeg-quality", MPV_FORMAT_INT64, &q) < 0) q = 90;
    pthread_mutex_lock(&shot_lock);
    pthread_mutex_lock(&app.lock);
    app.shot.kind = strcmp(kind, "video") != 0;
    shot_want = ++app.shot.serial;
    pthread_mutex_unlock(&app.lock);
    wake_render();
    pthread_mutex_lock(&app.lock);
    int done = wait_render(shot_taken, 5.0);
    unsigned char *rgba = done ? app.shot.rgba : NULL;
    int w = app.shot.w, h = app.shot.h;
    if (done) app.shot.rgba = NULL;
    pthread_mutex_unlock(&app.lock);
    int r = rgba ? shot_write(path, rgba, w, h, jpeg, (int)q) : -1;
    free(rgba);
    pthread_mutex_unlock(&shot_lock);
    reply(fd, id, r == 0 ? "success" : "error running command", NULL);
    return 1;
}

static void get_property(int fd, const mpv_node *id, const char *name)
{
    mpv_node d;
    if (!strcmp(name, "pid")) { d = node_int(app.pid); reply(fd, id, "success", &d); return; }
    if (!strcmp(name, "current-vo")) {
        d = node_str(app.screen_open ? "pvj-render" : "");
        if (!app.screen_open) { reply(fd, id, "property unavailable", NULL); return; }
        reply(fd, id, "success", &d);
        return;
    }
    if (!strcmp(name, "osd-width") || !strcmp(name, "display-width")) { d = node_int(app.out_w); reply(fd, id, "success", &d); return; }
    if (!strcmp(name, "osd-height") || !strcmp(name, "display-height")) { d = node_int(app.out_h); reply(fd, id, "success", &d); return; }
    if (!strcmp(name, "display-fps")) { d = node_dbl(app.out_hz); reply(fd, id, "success", &d); return; }
    if (!strcmp(name, "glsl-shaders")) {
        mpv_node items[MAX_SHADERS];
        pthread_mutex_lock(&app.lock);
        int nn = app.nshaders;
        char *copies[MAX_SHADERS];
        for (int i = 0; i < nn; i++) copies[i] = strdup(app.shaders[i] ? app.shaders[i] : "");
        pthread_mutex_unlock(&app.lock);
        for (int i = 0; i < nn; i++) items[i] = node_str(copies[i] ? copies[i] : "");
        mpv_node_list list = { .num = nn, .values = items };
        d.format = MPV_FORMAT_NODE_ARRAY;
        d.u.list = &list;
        reply(fd, id, "success", &d);
        for (int i = 0; i < nn; i++) free(copies[i]);
        return;
    }
    if (!strcmp(name, "osd-dimensions")) {
        // the output's size, with the video layer's margins taken from the internal size to the output's
        double sx = (double)app.out_w / app.in_w, sy = (double)app.out_h / app.in_h;
        mpv_node vid;
        int have = mpv_get_property(front_handle(), "osd-dimensions", MPV_FORMAT_NODE, &vid) >= 0;
        static const char *const keys[] = { "w", "h", "par", "aspect", "mt", "mb", "ml", "mr" };
        mpv_node vals[8];
        for (int i = 0; i < 8; i++) {
            const mpv_node *v = have ? node_map_get(&vid, keys[i]) : NULL;
            double x = v && is_num(v) ? num(v) : 0;
            if (i == 0) vals[i] = node_int(app.out_w);
            else if (i == 1) vals[i] = node_int(app.out_h);
            else if (i == 2) vals[i] = node_dbl(v ? x : 1.0);
            else if (i == 3) vals[i] = node_dbl((double)app.out_w / app.out_h);
            else vals[i] = node_int((int64_t)(x * (i < 6 ? sy : sx) + 0.5));
        }
        mpv_node_list list = { .num = 8, .values = vals, .keys = (char **)keys };
        d.format = MPV_FORMAT_NODE_MAP;
        d.u.list = &list;
        reply(fd, id, "success", &d);
        if (have) mpv_free_node_contents(&vid);
        return;
    }
    if (!strcmp(name, "pvj-sources")) { sources_property(fd, id); return; }
    if (!strcmp(name, "pvj-shaders")) { shaders_property(fd, id); return; }
    pthread_mutex_lock(&app.lock);
    int level_native = app.level_native[0];
    double level = app.level[0];
    long fx_drops = app.fx_drops;      // a total that only rises: no jump as programs go on and off
    pthread_mutex_unlock(&app.lock);
    if (level_native && !strcmp(name, "brightness")) { d = node_dbl(level); reply(fd, id, "success", &d); return; }
    if (fx_drops && !strcmp(name, "frame-drop-count")) {
        // a generator's late frames count as dropped frames while it is on (the guard reads this, D106)
        int64_t v = 0;
        mpv_get_property(front_handle(), name, MPV_FORMAT_INT64, &v);
        d = node_int(v + fx_drops);
        reply(fd, id, "success", &d);
        return;
    }
    if (!strcmp(name, "pvj-render")) {
        pthread_mutex_lock(&app.lock);
        char state[sizeof app.hook_state];
        memcpy(state, app.hook_state, sizeof state);
        // Nothing here asks libmpv: this is read to time the flips (tests/test_pvj_render.py, PaceTest), and a question
        // to a core sent after the counters were taken let the answer leave late with old counts (CI, mpv 0.37 and
        // 0.40: 63 flips a second measured on a clock of 60). The other layer's idleness comes from its event thread.
        long flips = app.flips, missed = app.missed, drawn = app.drawn, fences = app.fences, freed = app.fences_freed;
        long back = app.back_drawn, fades = app.fades, done = app.fades_done, cut = app.fades_cut;
        long nothing = app.fades_from_nothing, held = app.sound_held;
        double passes_rate = app.fx_passes_rate;
        int osd = app.overlays != 0 || now_s() < app.text_until;
        int phase = app.fade.phase, front = app.front;
        double progress = app.fade.progress, seconds = app.fade.seconds;
        int back_idle = __atomic_load_n(&app.idle[1 - front], __ATOMIC_ACQUIRE);      // nothing loaded: it costs nothing
        pthread_mutex_unlock(&app.lock);
        static const char *const PHASES[] = { "off", "waiting", "running" };
        static const char *const keys[] = { "version", "internal-width", "internal-height", "output-width", "output-height",
                                            "output-fps", "flips", "drawn", "missed-vblanks", "mapping", "text-layer", "hwdec",
                                            "gl-fences", "gl-fences-freed", "layers", "front", "fade", "fade-progress",
                                            "fade-seconds", "fades", "fades-done", "fades-cut", "back-drawn", "gl-objects",
                                            "back-idle", "fades-from-nothing", "sound-held", "sources", "shaders", "fx-fps",
                                            "fx-gl-objects", "fx-passes", "fx-passes-per-second" };
        mpv_node vals[33] = { node_str(PVJ_VERSION), node_int(app.in_w), node_int(app.in_h), node_int(app.out_w), node_int(app.out_h),
                              node_dbl(app.out_hz), node_int(flips), node_int(drawn), node_int(missed), node_str(state),
                              { .format = MPV_FORMAT_FLAG }, node_str(app.cfg->hwdec), node_int(fences), node_int(freed),
                              node_int(LAYERS), node_int(front), node_str(PHASES[phase]), node_dbl(progress), node_dbl(seconds),
                              node_int(fades), node_int(done), node_int(cut), node_int(back),
                              node_int(__atomic_load_n(&app.gl_objects, __ATOMIC_RELAXED)), { .format = MPV_FORMAT_FLAG },
                              node_int(nothing), node_int(held), node_int(app.nsources), node_int(FX_SLOTS),
                              node_int((int64_t)app.fx_fps), node_int(__atomic_load_n(&app.fx_gl, __ATOMIC_RELAXED)),
                              node_int(__atomic_load_n(&app.fx_passes, __ATOMIC_RELAXED)), node_dbl(passes_rate) };
        vals[10].u.flag = osd;
        vals[24].u.flag = back_idle;
        mpv_node_list list = { .num = 33, .values = vals, .keys = (char **)keys };
        d.format = MPV_FORMAT_NODE_MAP;
        d.u.list = &list;
        reply(fd, id, "success", &d);
        return;
    }
    // everything else is read from the video layer on the screen (a read changes nothing)
    mpv_node res;
    int r = mpv_get_property(front_handle(), name, MPV_FORMAT_NODE, &res);
    if (r < 0) { reply(fd, id, mpv_error_string(r), NULL); return; }
    reply(fd, id, "success", &res);
    mpv_free_node_contents(&res);
}

// An overlay file is mmapped by mpv for offset + h * stride bytes without a look at its size, and a file
// shorter than that kills this process with SIGBUS. So: a regular file (not a link, FIFO, directory or
// device), big enough, and a size of picture that is sane. Anything else is refused as mpv would refuse it.
#define OVERLAY_MAX_SIDE 8192.0
static int overlay_file_ok(const char *path, const mpv_node *off, const mpv_node *w, const mpv_node *h, const mpv_node *stride)
{
    double o = num(off), ww = num(w), hh = num(h), st = num(stride);
    if (!(o >= 0 && o <= 4294967296.0) || !(ww >= 1 && ww <= OVERLAY_MAX_SIDE) || !(hh >= 1 && hh <= OVERLAY_MAX_SIDE)
        || !(st >= ww * 4 && st <= OVERLAY_MAX_SIDE * 4))
        return 0;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return 0;
    struct stat sb;
    int ok = fstat(fd, &sb) == 0 && S_ISREG(sb.st_mode) && (double)sb.st_size >= o + hh * st;
    close(fd);
    return ok;
}

static void capture(int fd, const mpv_node *id, const char *path)
{
    if (!path || path[0] != '/' || strlen(path) > 4000) { reply(fd, id, "invalid parameter", NULL); return; }
    pthread_mutex_lock(&app.lock);
    free(app.capture_path);
    app.capture_path = strdup(path);
    app.capture_serial++;
    pthread_mutex_unlock(&app.lock);
    wake_render();
    pthread_mutex_lock(&app.lock);
    int done = wait_render(capture_taken, 5.0);
    int result = app.capture_result;
    pthread_mutex_unlock(&app.lock);
    reply(fd, id, done && result == 0 ? "success" : "error running command", NULL);
}

// set_property brightness: the panel's level (D95, D98, D101). Kept here as well (D106): while a program of the
// renderer's is on, source 1's layers keep brightness 0 and the renderer draws the level after the programs; the
// other sources get it as before.
static void set_level(int fd, const mpv_node *id, const mpv_node *v)
{
    if (!is_num(v) || !(num(v) >= -100 && num(v) <= 100)) {
        // mpv's own answer for a value it cannot take (a word, a number out of range)
        int r = mpv_set_property(front_handle(), "brightness", MPV_FORMAT_NODE, (void *)v);
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        return;
    }
    pthread_mutex_lock(&layer_lock);
    pthread_mutex_lock(&app.lock);
    int native[MAX_SOURCES];
    for (int s = 0; s < app.nsources; s++) native[s] = app.level_native[s];
    pthread_mutex_unlock(&app.lock);
    // Each source's layers get the level unless the source has a program on (its layers then keep 0 and the
    // renderer draws the level): set once, so a Blackout is never drawn twice for a moment (the review of #168).
    int r = 0;
    if (!native[0]) {
        int f = front_layer();
        r = mpv_set_property(app.layer[f], "brightness", MPV_FORMAT_NODE, (void *)v);
        if (r >= 0) mpv_set_property(app.layer[1 - f], "brightness", MPV_FORMAT_NODE, (void *)v);
    }
    if (r >= 0) {
        for (int s = 1; s < app.nsources; s++)
            if (!native[s]) mpv_set_property(app.layer[s + 1], "brightness", MPV_FORMAT_NODE, (void *)v);
        pthread_mutex_lock(&app.lock);
        for (int s = 0; s < app.nsources; s++) app.level[s] = num(v);     // the panel's level reaches every source (D101)
        pthread_mutex_unlock(&app.lock);
    }
    pthread_mutex_unlock(&layer_lock);
    wake_render();
    reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
}

static void handle(int fd, const char *line, size_t len)
{
    // a program came on or went off after its client stopped waiting (a slow build): the level follows it now
    if (__atomic_exchange_n(&app.level_resync, 0, __ATOMIC_ACQ_REL)) {
        pthread_mutex_lock(&layer_lock);
        for (int s = 0; s < app.nsources; s++) level_sync(s);
        pthread_mutex_unlock(&layer_lock);
    }
    mpv_node req;
    if (json_parse(line, len, &req) || req.format != MPV_FORMAT_NODE_MAP) {
        if (req.format != MPV_FORMAT_NONE) json_free(&req);
        reply(fd, NULL, "invalid parameter", NULL);
        return;
    }
    const mpv_node *id = node_map_get(&req, "request_id");
    const mpv_node *cmd = node_map_get(&req, "command");
    if (id && id->format != MPV_FORMAT_INT64) id = NULL;
    if (!cmd || cmd->format != MPV_FORMAT_NODE_ARRAY || cmd->u.list->num < 1 || !arg_str(cmd, 0)) {
        reply(fd, id, "invalid parameter", NULL);
        json_free(&req);
        return;
    }
    const char *name = arg_str(cmd, 0);
    int nargs = cmd->u.list->num;
    if (app.cfg->verbose) LOG("ipc: %s", name);

    if (!strcmp(name, "get_property") && nargs == 2 && arg_str(cmd, 1)) {
        get_property(fd, id, arg_str(cmd, 1));
    } else if (!strcmp(name, "set_property") && nargs == 3 && arg_str(cmd, 1)) {
        const char *prop = arg_str(cmd, 1);
        if (!strcmp(prop, "glsl-shaders")) set_shaders(fd, id, arg(cmd, 2));
        else if (!strcmp(prop, "brightness")) set_level(fd, id, arg(cmd, 2));
        else if (in_list(MIRRORED, prop)) {
            int r = set_both(prop, arg(cmd, 2));
            reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        } else if (in_list(SETTABLE, prop)) {
            int r = mpv_set_property(front_handle(), prop, MPV_FORMAT_NODE, (void *)arg(cmd, 2));
            reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        } else {
            LOG("ipc: set_property %s is not on the list", prop);
            reply(fd, id, "property not found", NULL);
        }
    } else if (!strcmp(name, "loadfile") && nargs >= 2 && nargs <= 5 && arg_str(cmd, 1)) {
        static const char *const flags[] = { "replace", "append", "append-play", "insert-next", "insert-next-play", NULL };
        int ok = 1;
        if (nargs >= 3) ok = arg_str(cmd, 2) && in_list(flags, arg_str(cmd, 2));
        if (ok && nargs >= 4) {
            const mpv_node *a = arg(cmd, 3);
            ok = a->format == MPV_FORMAT_INT64 || (a->format == MPV_FORMAT_STRING && load_options_ok(a->u.string));
        }
        if (ok && nargs == 5) ok = arg_str(cmd, 4) && load_options_ok(arg_str(cmd, 4));
        // a load that replaces what the front plays ends a crossfade first; one appended to its list does not
        int replaces = nargs < 3 || !arg_str(cmd, 2) || !strcmp(arg_str(cmd, 2), "replace");
        if (ok) forward_front(fd, id, cmd, replaces);
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "pvj-shader") && nargs >= 4 && nargs <= 5) {
        shader_command(fd, id, cmd);
    } else if (!strcmp(name, "pvj-source") && nargs >= 3) {
        source_command(fd, id, cmd);
    } else if (!strcmp(name, "pvj-crossfade") && (nargs == 3 || nargs == 4)) {
        crossfade(fd, id, cmd);
    } else if ((!strcmp(name, "stop") || !strcmp(name, "playlist-shuffle")) && nargs == 1) {
        forward_front(fd, id, cmd, !strcmp(name, "stop"));
    } else if ((!strcmp(name, "playlist-next") || !strcmp(name, "playlist-prev")) && nargs <= 2) {
        const char *f = nargs == 2 ? arg_str(cmd, 1) : "weak";
        if (f && (!strcmp(f, "weak") || !strcmp(f, "force"))) forward_front(fd, id, cmd, 1);
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "seek") && nargs >= 2 && nargs <= 3 && is_num(arg(cmd, 1))) {
        if (nargs == 2 || (arg_str(cmd, 2) && seek_flags_ok(arg_str(cmd, 2)))) forward(fd, id, front_handle(), cmd);
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "add") && nargs == 3 && arg_str(cmd, 1) && !strcmp(arg_str(cmd, 1), "volume") && is_num(arg(cmd, 2))) {
        // on the front, and the volume it gives is then the other layer's too (MIRRORED)
        mpv_node res;
        int r = mpv_command_node(front_handle(), (mpv_node *)cmd, &res);
        if (r >= 0) {
            mpv_free_node_contents(&res);
            double v;
            if (mpv_get_property(front_handle(), "volume", MPV_FORMAT_DOUBLE, &v) >= 0)
                mpv_set_property(app.layer[1 - front_layer()], "volume", MPV_FORMAT_DOUBLE, &v);
        }
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
    } else if (!strcmp(name, "vf") && nargs == 3 && arg_str(cmd, 1) && arg_str(cmd, 2)) {
        const char *op = arg_str(cmd, 1), *what = arg_str(cmd, 2);
        int ok = (!strcmp(op, "add") && (!strcmp(what, "@pvjfliph:hflip") || !strcmp(what, "@pvjflipv:vflip")))
                 || (!strcmp(op, "remove") && (!strcmp(what, "@pvjfliph") || !strcmp(what, "@pvjflipv")));
        if (ok) {
            mpv_node res;
            int r = command_both(cmd, &res);          // a flip is how the picture looks: both layers (MIRRORED)
            if (r >= 0) mpv_free_node_contents(&res);
            reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        } else {
            reply(fd, id, "invalid parameter", NULL);
        }
    } else if (!strcmp(name, "screenshot-to-file") && (nargs == 2 || nargs == 3) && arg_str(cmd, 1) && arg_str(cmd, 1)[0] == '/') {
        const char *f = nargs == 3 ? arg_str(cmd, 2) : "subtitles";
        if (f && (!strcmp(f, "video") || !strcmp(f, "window") || !strcmp(f, "subtitles"))) {
            if (!native_screenshot(fd, id, arg_str(cmd, 1), f)) forward(fd, id, front_handle(), cmd);
        }
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "overlay-add") && (nargs == 10 || nargs == 12)) {
        // id, x, y, file, offset, fmt, w, h, stride[, dw, dh]: a file by its path only (mpv's "@fd" and "&addr"
        // forms name memory or a descriptor of this process, never allowed here)
        const mpv_node *oid = arg(cmd, 1);
        const char *file = arg_str(cmd, 4), *fmt = arg_str(cmd, 6);
        int ok = oid->format == MPV_FORMAT_INT64 && oid->u.int64 >= 0 && oid->u.int64 < 64 && file && file[0] == '/' && fmt
                 && !strcmp(fmt, "bgra");
        for (int i = 2; ok && i < nargs; i++)
            if (i != 4 && i != 6) ok = is_num(arg(cmd, i));
        if (ok) ok = overlay_file_ok(file, arg(cmd, 5), arg(cmd, 7), arg(cmd, 8), arg(cmd, 9));
        if (!ok) {
            reply(fd, id, "invalid parameter", NULL);
        } else {
            mpv_node res;
            int r = mpv_command_node(app.osd, (mpv_node *)cmd, &res);
            if (r >= 0) {
                mpv_free_node_contents(&res);
                pthread_mutex_lock(&app.lock);
                app.overlays |= 1ull << oid->u.int64;
                pthread_mutex_unlock(&app.lock);
                wake_render();
            }
            reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        }
    } else if (!strcmp(name, "overlay-remove") && nargs == 2) {
        const mpv_node *oid = arg(cmd, 1);
        if (oid->format != MPV_FORMAT_INT64 || oid->u.int64 < 0 || oid->u.int64 >= 64) {
            reply(fd, id, "invalid parameter", NULL);
        } else {
            mpv_node res;
            int r = mpv_command_node(app.osd, (mpv_node *)cmd, &res);
            if (r >= 0) mpv_free_node_contents(&res);
            pthread_mutex_lock(&app.lock);
            app.overlays &= ~(1ull << oid->u.int64);
            pthread_mutex_unlock(&app.lock);
            wake_render();
            reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
        }
    } else if (!strcmp(name, "show-text") && nargs >= 2 && nargs <= 4 && arg_str(cmd, 1)) {
        double ms = nargs >= 3 && is_num(arg(cmd, 2)) ? num(arg(cmd, 2)) : -1;
        if (nargs >= 3 && !is_num(arg(cmd, 2))) { reply(fd, id, "invalid parameter", NULL); json_free(&req); return; }
        if (ms < 0) ms = 1000;                      // mpv's osd-duration
        mpv_node res;
        int r = mpv_command_node(app.osd, (mpv_node *)cmd, &res);
        if (r >= 0) {
            mpv_free_node_contents(&res);
            pthread_mutex_lock(&app.lock);
            app.text_until = arg_str(cmd, 1)[0] ? now_s() + ms / 1000.0 + 0.1 : now_s() + 0.1;
            pthread_mutex_unlock(&app.lock);
            wake_render();
        }
        reply(fd, id, r < 0 ? mpv_error_string(r) : "success", NULL);
    } else if (!strcmp(name, "pvj-capture") && nargs == 2) {
        capture(fd, id, arg_str(cmd, 1));
    } else if (!strcmp(name, "quit") && nargs <= 2) {
        reply(fd, id, "success", NULL);
        pthread_mutex_lock(&app.lock);
        app.quit = 1;
        app.exit_code = 0;
        pthread_cond_broadcast(&app.cond);
        pthread_mutex_unlock(&app.lock);
        wake_render();
    } else {
        LOG("ipc: %s is not on the list", name);
        reply(fd, id, "invalid parameter", NULL);
    }
    json_free(&req);
}

static int quitting(void)
{
    pthread_mutex_lock(&app.lock);
    int q = app.quit;
    pthread_mutex_unlock(&app.lock);
    return q;
}

// One client: its own line buffer, its own thread. It ends when the client goes, sends a line too long, sends
// nothing for IDLE_SECONDS, does not take an answer, or the program ends. Its socket is closed by the acceptor, which
// joins the thread first, so the number is never taken by another client while this one may still use it.
static void *client_thread(void *arg)
{
    Client *c = arg;
    char *buf = malloc(MAX_LINE + 1);
    size_t len = 0;
    double heard = now_s();
    while (buf && !t_send_failed && !quitting()) {
        struct pollfd pf = { c->fd, POLLIN, 0 };
        int r = poll(&pf, 1, 200);
        if (r < 0 && errno != EINTR) break;
        if (r <= 0) {
            if (now_s() - heard > IDLE_SECONDS) {
                if (app.cfg->verbose) LOG("ipc: a client sent nothing for %.0f seconds; it is dropped", IDLE_SECONDS);
                break;
            }
            continue;
        }
        ssize_t got = recv(c->fd, buf + len, MAX_LINE - len, 0);
        if (got == 0 || (got < 0 && errno != EINTR && errno != EAGAIN)) break;
        if (got < 0) continue;
        heard = now_s();
        len += (size_t)got;
        size_t start = 0;
        for (size_t i = 0; i < len && !t_send_failed; i++) {
            if (buf[i] != '\n') continue;
            if (i > start) handle(c->fd, buf + start, i - start);
            start = i + 1;
        }
        if (t_send_failed) break;
        if (start) {
            memmove(buf, buf + start, len - start);
            len -= start;
        }
        if (len >= MAX_LINE) {
            LOG("ipc: a line longer than %d bytes; the client is dropped", MAX_LINE);
            break;
        }
    }
    free(buf);
    shutdown(c->fd, SHUT_RDWR);         // the client hears the end now, not when the acceptor gets round to it
    __atomic_store_n(&c->finished, 1, __ATOMIC_RELEASE);
    return NULL;
}

static void reap(int all)
{
    for (int i = 0; i < MAX_CLIENTS; i++) {
        Client *c = &clients[i];
        if (c->fd < 0) continue;
        if (all) shutdown(c->fd, SHUT_RDWR);        // a thread in recv or send wakes at once
        else if (!__atomic_load_n(&c->finished, __ATOMIC_ACQUIRE)) continue;
        pthread_join(c->th, NULL);
        close(c->fd);
        c->fd = -1;
    }
}

// The acceptor: takes clients and gives each a thread; never runs a command itself.
void *ipc_thread(void *unused)
{
    (void)unused;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, CLIENT_STACK);
    double said_full = -1e9, said_fds = -1e9;
    while (!quitting()) {
        reap(0);
        struct pollfd pf = { listen_fd, POLLIN, 0 };
        if (poll(&pf, 1, 200) <= 0) continue;
        int fd = accept4(listen_fd, NULL, NULL, SOCK_CLOEXEC);
        if (fd < 0) {
            if (errno == EMFILE || errno == ENFILE || errno == ENOBUFS || errno == ENOMEM) {
                // the connection stays queued; without a pause the poll above would turn at once, forever
                if (now_s() - said_fds > 10) { LOG("ipc: cannot take a client (%s)", strerror(errno)); said_fds = now_s(); }
                struct timespec ts = { 0, 50 * 1000 * 1000 };
                nanosleep(&ts, NULL);
            }
            continue;
        }
        int slot = -1;
        for (int i = 0; i < MAX_CLIENTS; i++)
            if (clients[i].fd < 0) { slot = i; break; }
        if (slot < 0) {
            if (now_s() - said_full > 10) { LOG("ipc: %d clients at once; one more is turned away", MAX_CLIENTS); said_full = now_s(); }
            close(fd);
            continue;
        }
        struct timeval tv = { SEND_SECONDS, 0 };    // a client that does not read its answers is not waited on
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
        Client *c = &clients[slot];
        c->fd = fd;
        c->finished = 0;
        if (pthread_create(&c->th, &attr, client_thread, c)) {
            LOG("ipc: no thread for a client");
            close(fd);
            c->fd = -1;
        }
    }
    reap(1);
    pthread_attr_destroy(&attr);
    __atomic_store_n(&ipc_done, 1, __ATOMIC_RELEASE);
    return NULL;
}
