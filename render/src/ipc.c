// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The control socket: mpv's JSON IPC protocol, a fixed subset (project-log/DECISIONS.md, D95). One JSON object a
// line, {"command": [...], "request_id": n}, answered {"request_id": n, "error": "success", "data": ...}. Anything
// outside the subset is answered with mpv's own words ("invalid parameter", "property not found"), so the panel's
// code treats it as it treats mpv refusing something.
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
// Names the panel may set, all on the video layer (glsl-shaders is handled apart).
static const char *const SETTABLE[] = {
    "pause", "speed", "volume", "mute", "loop-file", "loop-playlist", "keep-open", "image-display-duration",
    "audio-device", "brightness", "video-zoom", "video-pan-x", "video-pan-y", "video-rotate", "video-crop",
    "keepaspect", "fbo-format", "screenshot-format", "screenshot-jpeg-quality", "screenshot-png-compression",
    "screenshot-png-filter", "screenshot-high-bit-depth",
    // a live input's pipe (pvj/player.py, _play_pipe, MKV_ONLY, PIPE_DEFAULTS)
    "demuxer", "demuxer-rawvideo-w", "demuxer-rawvideo-h", "demuxer-rawvideo-mp-format", "demuxer-rawvideo-fps",
    "cache", "cache-pause", "initial-audio-sync", "demuxer-readahead-secs", "demuxer-max-bytes", "vd", "ad", "sid",
    "ordered-chapters", "cover-art-auto", "embeddedfonts", "audio-delay", NULL };

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
static int load_options_ok(const char *s)
{
    if (strlen(s) > 1024) return 0;
    char *copy = strdup(s), *save = NULL;
    if (!copy) return 0;
    int ok = 1;
    for (char *part = strtok_r(copy, ",", &save); part && ok; part = strtok_r(NULL, ",", &save)) {
        char *eq = strchr(part, '=');
        if (!eq) { ok = 0; break; }
        *eq = 0;
        ok = in_list(LOAD_KEYS, part) && plain_value(eq + 1);
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
    int r = mpv_set_property(app.video, "glsl-shaders", MPV_FORMAT_NODE, &arr);
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
        int have = mpv_get_property(app.video, "osd-dimensions", MPV_FORMAT_NODE, &vid) >= 0;
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
    if (!strcmp(name, "pvj-render")) {
        pthread_mutex_lock(&app.lock);
        char state[sizeof app.hook_state];
        memcpy(state, app.hook_state, sizeof state);
        long flips = app.flips, missed = app.missed, drawn = app.drawn, fences = app.fences, freed = app.fences_freed;
        int osd = app.overlays != 0 || now_s() < app.text_until;
        pthread_mutex_unlock(&app.lock);
        static const char *const keys[] = { "version", "internal-width", "internal-height", "output-width", "output-height",
                                            "output-fps", "flips", "drawn", "missed-vblanks", "mapping", "text-layer", "hwdec",
                                            "gl-fences", "gl-fences-freed" };
        mpv_node vals[14] = { node_str(PVJ_VERSION), node_int(app.in_w), node_int(app.in_h), node_int(app.out_w), node_int(app.out_h),
                              node_dbl(app.out_hz), node_int(flips), node_int(drawn), node_int(missed), node_str(state),
                              { .format = MPV_FORMAT_FLAG }, node_str(app.cfg->hwdec), node_int(fences), node_int(freed) };
        vals[10].u.flag = osd;
        mpv_node_list list = { .num = 14, .values = vals, .keys = (char **)keys };
        d.format = MPV_FORMAT_NODE_MAP;
        d.u.list = &list;
        reply(fd, id, "success", &d);
        return;
    }
    // everything else is read from the video layer (a read changes nothing)
    mpv_node res;
    int r = mpv_get_property(app.video, name, MPV_FORMAT_NODE, &res);
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

static void handle(int fd, const char *line, size_t len)
{
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
        else if (in_list(SETTABLE, prop)) {
            int r = mpv_set_property(app.video, prop, MPV_FORMAT_NODE, (void *)arg(cmd, 2));
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
        if (ok) forward(fd, id, app.video, cmd);
        else reply(fd, id, "invalid parameter", NULL);
    } else if ((!strcmp(name, "stop") || !strcmp(name, "playlist-shuffle")) && nargs == 1) {
        forward(fd, id, app.video, cmd);
    } else if ((!strcmp(name, "playlist-next") || !strcmp(name, "playlist-prev")) && nargs <= 2) {
        const char *f = nargs == 2 ? arg_str(cmd, 1) : "weak";
        if (f && (!strcmp(f, "weak") || !strcmp(f, "force"))) forward(fd, id, app.video, cmd);
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "seek") && nargs >= 2 && nargs <= 3 && is_num(arg(cmd, 1))) {
        if (nargs == 2 || (arg_str(cmd, 2) && seek_flags_ok(arg_str(cmd, 2)))) forward(fd, id, app.video, cmd);
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "add") && nargs == 3 && arg_str(cmd, 1) && !strcmp(arg_str(cmd, 1), "volume") && is_num(arg(cmd, 2))) {
        forward(fd, id, app.video, cmd);
    } else if (!strcmp(name, "vf") && nargs == 3 && arg_str(cmd, 1) && arg_str(cmd, 2)) {
        const char *op = arg_str(cmd, 1), *what = arg_str(cmd, 2);
        int ok = (!strcmp(op, "add") && (!strcmp(what, "@pvjfliph:hflip") || !strcmp(what, "@pvjflipv:vflip")))
                 || (!strcmp(op, "remove") && (!strcmp(what, "@pvjfliph") || !strcmp(what, "@pvjflipv")));
        if (ok) forward(fd, id, app.video, cmd);
        else reply(fd, id, "invalid parameter", NULL);
    } else if (!strcmp(name, "screenshot-to-file") && (nargs == 2 || nargs == 3) && arg_str(cmd, 1) && arg_str(cmd, 1)[0] == '/') {
        const char *f = nargs == 3 ? arg_str(cmd, 2) : "subtitles";
        if (f && (!strcmp(f, "video") || !strcmp(f, "window") || !strcmp(f, "subtitles"))) forward(fd, id, app.video, cmd);
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
