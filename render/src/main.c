// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvj-render, phase 1 (project-log/DECISIONS.md, D95): the box's own renderer behind the socket the one mpv had.
//
//   the video layer   libmpv (render API) draws the clip, with its brightness, zoom, pan, rotation, flips and its
//                     user shaders (effects, generators), into a texture at the internal size
//   the final pass    scales that texture to the screen; with a mapping it is the mapping's own OUTPUT shader
//   the text layer    a second libmpv that plays nothing draws overlays and show-text at the screen's size, laid
//                     over the final picture while something is on it
//
// Usage: pvj-render --socket=PATH [--drm-device=/dev/dri/cardN] [--drm-connector=NAME] [--drm-mode=WxH@R|N]
//                   [--detail=LINES|output] [--hwdec=NAME] [--audio-device=NAME] [--fast] [--every-vblank]
//                   [--headless=WxH] [--frames=N] [--verbose]
#define _GNU_SOURCE
#include "pvj.h"
#include "json.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <mpv/render_gl.h>

App app = { .lock = PTHREAD_MUTEX_INITIALIZER, .cond = PTHREAD_COND_INITIALIZER, .wake_fd = { -1, -1 } };
static volatile sig_atomic_t g_signal;

double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec * 1e-9;
}

void logf_(const char *fmt, ...)
{
    char line[600];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    fprintf(stderr, "pvj-render: %s\n", line);
}

void wake_render(void)
{
    if (app.wake_fd[1] >= 0) {
        char c = 1;
        ssize_t r = write(app.wake_fd[1], &c, 1);
        (void)r;
    }
}

static void on_signal(int s)
{
    g_signal = s;
    wake_render();
}

/* ---- options ------------------------------------------------------------------------------------------------------- */
static int opt(const char *a, const char *name, const char **val)
{
    size_t n = strlen(name);
    if (strncmp(a, name, n) || a[n] != '=') return 0;
    *val = a + n + 1;
    return 1;
}

static int parse_args(int argc, char **argv, Cfg *c)
{
    *c = (Cfg){ .hwdec = "no", .detail = 0 };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v;
        if (opt(a, "--socket", &v)) c->socket_path = v;
        else if (opt(a, "--drm-device", &v)) c->drm_device = v;
        else if (opt(a, "--drm-connector", &v)) c->drm_connector = v;
        else if (opt(a, "--drm-mode", &v)) c->drm_mode = v;
        else if (opt(a, "--detail", &v)) {
            if (!strcmp(v, "output")) c->detail = 0;
            else {
                char *e;
                long d = strtol(v, &e, 10);
                if (*e || d < 16 || d > 4320) { LOG("--detail is a number of lines from 16 to 4320, or output"); return -1; }
                c->detail = (int)d;
            }
        } else if (opt(a, "--hwdec", &v)) {
            for (const char *p = v; *p; p++)
                if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '-' || *p == ',')) { LOG("--hwdec: not a name"); return -1; }
            c->hwdec = v;
        } else if (opt(a, "--audio-device", &v)) c->audio_device = v;
        else if (opt(a, "--headless", &v)) {
            if (sscanf(v, "%dx%d", &c->headless_w, &c->headless_h) != 2 || c->headless_w < 16 || c->headless_h < 16
                || c->headless_w > 7680 || c->headless_h > 4320) { LOG("--headless=WxH"); return -1; }
        } else if (opt(a, "--frames", &v)) c->frames = atoi(v);
        else if (!strcmp(a, "--fast")) c->fast = 1;
        else if (!strcmp(a, "--every-vblank")) c->every_vblank = 1;
        else if (!strcmp(a, "--verbose")) c->verbose = 1;
        else if (!strcmp(a, "--version")) { printf("pvj-render %s\n", PVJ_VERSION); exit(0); }
        else { LOG("unknown argument: %s", a); return -1; }
    }
    if (!c->socket_path) { LOG("--socket=PATH is needed"); return -1; }
    return 0;
}

/* ---- the libmpv instances ------------------------------------------------------------------------------------------- */
typedef struct {
    mpv_handle *h;
    mpv_render_context *rc;
    GLuint tex, fbo;
    int w, h_;
    int dirty;
    pthread_t events;
    int have_events;
} Layer;

static Layer video, text;

static void *get_proc(void *ctx, const char *name) { (void)ctx; return (void *)eglGetProcAddress(name); }
static void on_update(void *ctx) { (void)ctx; wake_render(); }

static volatile int events_stop;

static void *event_loop(void *arg)
{
    mpv_handle *h = arg;
    while (!events_stop) {
        mpv_event *ev = mpv_wait_event(h, 0.5);
        if (ev->event_id == MPV_EVENT_SHUTDOWN) break;
        if (ev->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *m = ev->data;
            size_t n = strlen(m->text);
            LOG("%s %s: %.*s", h == app.osd ? "text" : "video", m->prefix, (int)(n && m->text[n - 1] == '\n' ? n - 1 : n), m->text);
        }
    }
    return NULL;
}

static int set(mpv_handle *h, const char *k, const char *v)
{
    int r = mpv_set_option_string(h, k, v);
    if (r < 0) LOG("mpv option %s=%s: %s", k, v, mpv_error_string(r));
    return r;
}

static int target(Layer *l, int w, int h)
{
    l->w = w;
    l->h_ = h;
    glGenTextures(1, &l->tex);
    glBindTexture(GL_TEXTURE_2D, l->tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glGenFramebuffers(1, &l->fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, l->fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, l->tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) return -1;
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    return 0;
}

static int layer_open(Layer *l, int is_text, const Cfg *c, int w, int h)
{
    l->h = mpv_create();
    if (!l->h) return -1;
    // as classic mpv is started (pvj/player.py, mpv_command): media only, nothing a file can point elsewhere
    set(l->h, "config", "no");
    set(l->h, "vo", "libmpv");
    set(l->h, "idle", "yes");
    set(l->h, "force-window", "yes");
    set(l->h, "keep-open", "no");
    set(l->h, "osd-level", "0");
    set(l->h, "terminal", "no");
    set(l->h, "input-default-bindings", "no");
    set(l->h, "input-vo-keyboard", "no");
    set(l->h, "load-unsafe-playlists", "no");
    set(l->h, "access-references", "no");
    set(l->h, "sub-auto", "no");
    set(l->h, "audio-file-auto", "no");
    set(l->h, "ytdl", "no");
    set(l->h, "load-scripts", "no");
    set(l->h, "load-auto-profiles", "no");
    if (is_text) {
        // plays nothing: only its on-screen layer, over a clear background
        set(l->h, "ao", "null");
        set(l->h, "aid", "no");
        // a clear background: mpv 0.38 on calls the colour background-color; 0.37 (Ubuntu 24.04) calls it background
        if (mpv_set_option_string(l->h, "background-color", "#00000000") < 0) set(l->h, "background", "#00000000");
        set(l->h, "osd-bar", "no");
    } else {
        set(l->h, "hwdec", c->hwdec);
        if (c->fast) set(l->h, "profile", "fast");
        if (c->audio_device) set(l->h, "audio-device", c->audio_device);
    }
    if (mpv_initialize(l->h) < 0) return -1;
    mpv_request_log_messages(l->h, c->verbose ? "info" : "warn");
    int adv = 0;
    mpv_opengl_init_params gl = { .get_proc_address = get_proc };
    mpv_render_param params[] = {
        { MPV_RENDER_PARAM_API_TYPE, (void *)MPV_RENDER_API_TYPE_OPENGL },
        { MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl },
        { MPV_RENDER_PARAM_ADVANCED_CONTROL, &adv },
        { 0 } };
    if (mpv_render_context_create(&l->rc, l->h, params) < 0) return -1;
    mpv_render_context_set_update_callback(l->rc, on_update, NULL);
    if (target(l, w, h)) return -1;
    l->dirty = 1;
    if (pthread_create(&l->events, NULL, event_loop, l->h) == 0) l->have_events = 1;
    return 0;
}

static void layer_render(Layer *l)
{
    int flip = 0, block = 0;
    mpv_opengl_fbo fbo = { .fbo = (int)l->fbo, .w = l->w, .h = l->h_, .internal_format = GL_RGBA8 };
    mpv_render_param rp[] = { { MPV_RENDER_PARAM_OPENGL_FBO, &fbo }, { MPV_RENDER_PARAM_FLIP_Y, &flip },
                              { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, { 0 } };
    mpv_render_context_render(l->rc, rp);
}

static void layer_close(Layer *l)
{
    // the event thread lets go of the handle first: it must never wait on one that is gone
    events_stop = 1;
    if (l->have_events) {
        mpv_wakeup(l->h);
        pthread_join(l->events, NULL);
        l->have_events = 0;
    }
    if (l->rc) mpv_render_context_free(l->rc);
    l->rc = NULL;
    if (l->h) mpv_terminate_destroy(l->h);
    l->h = NULL;
    if (l->fbo) glDeleteFramebuffers(1, &l->fbo);
    if (l->tex) glDeleteTextures(1, &l->tex);
}

/* ---- the passes ----------------------------------------------------------------------------------------------------- */
static const char *VS_PLAIN =
    "#version 300 es\n"
    "in vec2 pvj_vert;\n"
    "out vec2 pvj_pos;\n"
    "void main() { pvj_pos = vec2(pvj_vert.x * 0.5 + 0.5, 0.5 - pvj_vert.y * 0.5); gl_Position = vec4(pvj_vert, 0.0, 1.0); }\n";
static const char *FS_SCALE =
    "#version 300 es\n"
    "precision mediump float;\n"
    "in vec2 pvj_pos;\n"
    "out vec4 pvj_out;\n"
    "uniform sampler2D tex;\n"
    "void main() { pvj_out = vec4(texture(tex, pvj_pos).rgb, 1.0); }\n";
// the text layer is premultiplied (mpv draws its on-screen layer that way): laid over with ONE, ONE_MINUS_SRC_ALPHA
static const char *FS_OVER =
    "#version 300 es\n"
    "precision mediump float;\n"
    "in vec2 pvj_pos;\n"
    "out vec4 pvj_out;\n"
    "uniform sampler2D tex;\n"
    "void main() { pvj_out = texture(tex, pvj_pos); }\n";

static GLuint vbo, vao, p_scale, p_over;
static HookGL hook_gl;
static int have_hook;

static void quad(void)
{
    glBindVertexArray(vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glBindVertexArray(0);
}

static void reset_gl(void)
{
    glDisable(GL_BLEND);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_STENCIL_TEST);
    glDisable(GL_CULL_FACE);
    glColorMask(1, 1, 1, 1);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glUseProgram(0);
    glActiveTexture(GL_TEXTURE0);
}

static void take_hook(void)
{
    pthread_mutex_lock(&app.lock);
    if (!app.hook_pending) { pthread_mutex_unlock(&app.lock); return; }
    Hook *h = app.hook_next;
    app.hook_next = NULL;
    pthread_mutex_unlock(&app.lock);
    char state[sizeof app.hook_state] = "off";
    if (have_hook) hookgl_free(&hook_gl);
    have_hook = 0;
    if (h) {
        char err[150] = "";
        if (hookgl_build(h, &hook_gl, err, sizeof err) == 0) {
            have_hook = 1;
            snprintf(state, sizeof state, "on");
        } else {
            snprintf(state, sizeof state, "failed: %s", err);
            LOG("the mapping is not drawn: %s", err);
        }
        hook_free(h);
    }
    pthread_mutex_lock(&app.lock);
    memcpy(app.hook_state, state, sizeof state);
    app.hook_pending = 0;
    pthread_cond_broadcast(&app.cond);
    pthread_mutex_unlock(&app.lock);
}

static void final_pass(int text_on)
{
    reset_gl();
    glBindFramebuffer(GL_FRAMEBUFFER, out_target());
    glViewport(0, 0, app.out_w, app.out_h);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, video.tex);
    if (have_hook) {
        glUseProgram(hook_gl.prog);
        glUniform1i(hook_gl.loc_raw, 0);
        glUniform2f(hook_gl.loc_size, (float)app.out_w, (float)app.out_h);
        glUniform2f(hook_gl.loc_pt, 1.0f / app.out_w, 1.0f / app.out_h);
        glUniform2f(hook_gl.loc_target, (float)app.out_w, (float)app.out_h);
        glUniform2f(hook_gl.loc_input, (float)app.in_w, (float)app.in_h);
        for (int i = 0; i < hook_gl.ntex; i++) {
            glActiveTexture(GL_TEXTURE1 + i);
            glBindTexture(GL_TEXTURE_2D, hook_gl.tex[i]);
            glUniform1i(hook_gl.tex_loc[i], 1 + i);
            glUniform2f(hook_gl.tex_size_loc[i], (float)hook_gl.tex_w[i], (float)hook_gl.tex_h[i]);
        }
    } else {
        glUseProgram(p_scale);
    }
    quad();
    if (text_on) {
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, text.tex);
        glUseProgram(p_over);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
        quad();
        glDisable(GL_BLEND);
    }
}

// The final picture as a PPM file (P6, top row first), never through a link.
static int write_capture(const char *path)
{
    size_t row = (size_t)app.out_w * 4;
    unsigned char *px = malloc(row * (size_t)app.out_h);
    if (!px) return -1;
    glBindFramebuffer(GL_FRAMEBUFFER, out_target());
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glReadPixels(0, 0, app.out_w, app.out_h, GL_RGBA, GL_UNSIGNED_BYTE, px);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0640);
    if (fd < 0) { LOG("capture: %s: %s", path, strerror(errno)); free(px); return -1; }
    FILE *f = fdopen(fd, "wb");
    if (!f) { close(fd); free(px); return -1; }
    fprintf(f, "P6\n%d %d\n255\n", app.out_w, app.out_h);
    unsigned char *rgb = malloc((size_t)app.out_w * 3);
    int ok = rgb != NULL;
    for (int y = app.out_h - 1; ok && y >= 0; y--) {
        const unsigned char *s = px + row * (size_t)y;
        for (int x = 0; x < app.out_w; x++) {
            rgb[x * 3] = s[x * 4];
            rgb[x * 3 + 1] = s[x * 4 + 1];
            rgb[x * 3 + 2] = s[x * 4 + 2];
        }
        ok = fwrite(rgb, 3, (size_t)app.out_w, f) == (size_t)app.out_w;
    }
    free(rgb);
    free(px);
    if (fclose(f)) ok = 0;
    return ok ? 0 : -1;
}

/* ---- main ---------------------------------------------------------------------------------------------------------- */
int main(int argc, char **argv)
{
    Cfg cfg;
    if (parse_args(argc, argv, &cfg)) return 64;
    app.cfg = &cfg;
    app.pid = getpid();
    snprintf(app.hook_state, sizeof app.hook_state, "off");
    if (pipe2(app.wake_fd, O_CLOEXEC | O_NONBLOCK)) { LOG("pipe: %s", strerror(errno)); return 1; }
    struct sigaction sa = { .sa_handler = on_signal };
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    char err[200] = "";
    if (out_open(&cfg, err, sizeof err)) { LOG("no output: %s", err); out_close(); return 2; }
    // the internal size: `detail` lines at the output's shape (even numbers), never more than the output
    if (cfg.detail <= 0 || cfg.detail >= app.out_h) {
        app.in_w = app.out_w;
        app.in_h = app.out_h;
    } else {
        app.in_h = cfg.detail & ~1;
        app.in_w = ((int)lround((double)app.out_w * app.in_h / app.out_h)) & ~1;
    }
    if (cfg.headless_w > 0) app.screen_open = 1;

    static const float q[] = { -1, -1, 1, -1, -1, 1, 1, 1 };
    glGenVertexArrays(1, &vao);
    glBindVertexArray(vao);
    glGenBuffers(1, &vbo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof q, q, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, 0);
    glBindVertexArray(0);
    p_scale = gl_program(VS_PLAIN, FS_SCALE, err, sizeof err);
    p_over = gl_program(VS_PLAIN, FS_OVER, err, sizeof err);
    if (!p_scale || !p_over) { LOG("%s", err); out_close(); return 2; }
    glUseProgram(p_scale);
    glUniform1i(glGetUniformLocation(p_scale, "tex"), 0);
    glUseProgram(p_over);
    glUniform1i(glGetUniformLocation(p_over, "tex"), 0);

    if (layer_open(&video, 0, &cfg, app.in_w, app.in_h) || layer_open(&text, 1, &cfg, app.out_w, app.out_h)) {
        LOG("libmpv could not be started");
        layer_close(&text);
        layer_close(&video);
        out_close();
        return 2;
    }
    app.video = video.h;
    app.osd = text.h;
    if (ipc_open(cfg.socket_path, err, sizeof err)) {
        LOG("%s", err);
        layer_close(&text);
        layer_close(&video);
        out_close();
        return 2;
    }
    pthread_t ipc;
    if (pthread_create(&ipc, NULL, ipc_thread, NULL)) { LOG("no socket thread"); return 2; }
    LOG("drawing %dx%d inside, %dx%d at %.2f Hz out, hwdec %s, socket %s", app.in_w, app.in_h, app.out_w, app.out_h, app.out_hz,
        cfg.hwdec, cfg.socket_path);

    int failed = 0, first = 1, text_was = 0;
    long presented = 0;
    while (!g_signal) {
        pthread_mutex_lock(&app.lock);
        int quit = app.quit, cap = app.capture_serial != app.capture_done_serial;
        int text_on = app.overlays != 0 || now_s() < app.text_until;
        double text_until = app.text_until;
        pthread_mutex_unlock(&app.lock);
        if (quit) break;
        int changed = first || cap || cfg.every_vblank;
        if (app.hook_pending) { take_hook(); changed = 1; }
        uint64_t uv = mpv_render_context_update(video.rc);
        if (uv & MPV_RENDER_UPDATE_FRAME) video.dirty = 1;
        uint64_t ut = mpv_render_context_update(text.rc);
        if (ut & MPV_RENDER_UPDATE_FRAME) text.dirty = 1;
        if (text_on != text_was) changed = 1;
        if (text_on && !text_was) text.dirty = 1;          // never a stale text for a frame
        if (video.dirty) {
            reset_gl();
            layer_render(&video);
            video.dirty = 0;
            changed = 1;
            app.drawn++;
        }
        if (text_on && text.dirty) {
            reset_gl();
            layer_render(&text);
            text.dirty = 0;
            changed = 1;
        }
        text_was = text_on;
        if (changed) {
            final_pass(text_on);
            if (cap) {
                pthread_mutex_lock(&app.lock);
                char *path = app.capture_path ? strdup(app.capture_path) : NULL;
                int serial = app.capture_serial;
                pthread_mutex_unlock(&app.lock);
                int r = path ? write_capture(path) : -1;
                free(path);
                pthread_mutex_lock(&app.lock);
                app.capture_result = r;
                app.capture_done_serial = serial;
                pthread_cond_broadcast(&app.cond);
                pthread_mutex_unlock(&app.lock);
            }
            if (out_present()) { failed = 1; break; }
            mpv_render_context_report_swap(video.rc);
            if (text_on) mpv_render_context_report_swap(text.rc);
            first = 0;
            presented++;
            if (cfg.frames > 0 && presented >= cfg.frames) break;
        }
        // until the next vblank (or the next tick headless); a change of a layer wakes the loop at once
        double wait = 1.0 / (app.out_hz > 1 ? app.out_hz : 60.0);
        if (!changed && !cfg.every_vblank) {
            wait = 0.5;
            if (text_on && text_until > now_s()) wait = fmin(wait, text_until - now_s() + 0.01);
        }
        out_wait(wait);
    }
    pthread_mutex_lock(&app.lock);
    app.quit = 1;
    pthread_cond_broadcast(&app.cond);
    pthread_mutex_unlock(&app.lock);
    pthread_join(ipc, NULL);
    ipc_close();
    if (have_hook) hookgl_free(&hook_gl);
    layer_close(&text);
    layer_close(&video);
    out_close();
    LOG("ended: %ld flips, %ld video frames drawn%s", app.flips, app.drawn, failed ? ", the screen failed" : "");
    return failed ? 3 : app.exit_code;
}
