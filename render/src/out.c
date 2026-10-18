// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The output: the screen through EGL on GBM on KMS (page flips paced by vblank, the next frame drawn while the last
// flip is pending), or, headless, an EGL context without a surface drawing into a framebuffer of its own, paced by
// the clock. The card, connector and mode are taken as mpv 0.40 takes them (video/out/drm_common.c), because the
// player's service passes the arguments it chose for mpv (pvj/screenmode.py, D90 to D92).
#define _GNU_SOURCE
#include "pvj.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <gbm.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#ifndef EGL_PLATFORM_SURFACELESS_MESA
#define EGL_PLATFORM_SURFACELESS_MESA 0x31DD
#endif
#ifndef EGL_PLATFORM_GBM_KHR
#define EGL_PLATFORM_GBM_KHR 0x31D7
#endif
#ifndef EGL_OPENGL_ES3_BIT_KHR
#define EGL_OPENGL_ES3_BIT_KHR 0x0040
#endif

static int headless;
static EGLDisplay dpy = EGL_NO_DISPLAY;
static EGLContext ctx = EGL_NO_CONTEXT;
static EGLSurface surf = EGL_NO_SURFACE;
// headless
static GLuint h_fbo, h_tex;
static double h_next;
// KMS
static int drm_fd = -1;
static uint32_t conn_id, crtc_id;
static drmModeModeInfo mode;
static drmModeCrtc *saved_crtc;
static struct gbm_device *gbm;
static struct gbm_surface *gsurf;
static struct gbm_bo *bo_cur, *bo_pending;
static int flip_pending, modeset_done;
static unsigned last_seq, prev_seq;
static int have_seq;

static int fail(char *err, size_t n, const char *what)
{
    snprintf(err, n, "%s", what);
    return -1;
}

static double mode_hz(const drmModeModeInfo *m)
{
    // as mpv's mode_get_Hz: the pixel clock over the whole frame, doubled for an interlaced mode
    if (!m->htotal || !m->vtotal) return 0;
    double r = m->clock * 1000.0 / m->htotal / m->vtotal;
    if (m->flags & DRM_MODE_FLAG_INTERLACE) r *= 2.0;
    return r;
}

static void connector_name(const drmModeConnector *c, char *out, size_t n)
{
    const char *t = drmModeGetConnectorTypeName(c->connector_type);
    snprintf(out, n, "%s-%u", t ? t : "Unknown", c->connector_type_id);
}

// The connector: by its whole name if one was given (mpv: get_connector_by_name), else the first connected HDMI one,
// else the first connected one.
static drmModeConnector *pick_connector(int fd, drmModeRes *res, const char *want)
{
    drmModeConnector *first = NULL, *hdmi = NULL;
    for (int i = 0; i < res->count_connectors; i++) {
        drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
        if (!c) continue;
        char name[64];
        connector_name(c, name, sizeof name);
        if (want) {
            if (!strcmp(name, want)) {
                drmModeFreeConnector(first);
                drmModeFreeConnector(hdmi);
                return c;
            }
        } else if (c->connection == DRM_MODE_CONNECTED && c->count_modes > 0) {
            int is_hdmi = c->connector_type == DRM_MODE_CONNECTOR_HDMIA || c->connector_type == DRM_MODE_CONNECTOR_HDMIB;
            if (is_hdmi && !hdmi) { hdmi = c; continue; }
            if (!first) { first = c; continue; }
        }
        drmModeFreeConnector(c);
    }
    if (want) return NULL;
    if (hdmi) { drmModeFreeConnector(first); return hdmi; }
    return first;
}

// The mode as mpv's --drm-mode reads it: an index into the connector's list, "preferred", "highest", or WxH[@R] with
// R compared to 0.01 Hz (mode_match: round(R * 100) against round(rate * 100)). Without one: the first mode.
static int pick_mode(const drmModeConnector *c, const char *spec, drmModeModeInfo *out, char *err, size_t n)
{
    if (c->count_modes <= 0) return fail(err, n, "the screen lists no mode");
    if (!spec || !*spec || !strcmp(spec, "preferred")) {
        for (int i = 0; spec && i < c->count_modes; i++)
            if (c->modes[i].type & DRM_MODE_TYPE_PREFERRED) { *out = c->modes[i]; return 0; }
        *out = c->modes[0];
        return 0;
    }
    if (!strcmp(spec, "highest")) {
        int best = 0;
        for (int i = 1; i < c->count_modes; i++) {
            const drmModeModeInfo *a = &c->modes[i], *b = &c->modes[best];
            if (a->hdisplay * a->vdisplay > b->hdisplay * b->vdisplay
                || (a->hdisplay * a->vdisplay == b->hdisplay * b->vdisplay && mode_hz(a) > mode_hz(b)))
                best = i;
        }
        *out = c->modes[best];
        return 0;
    }
    char *end;
    long idx = strtol(spec, &end, 10);
    if (*end == 0 && end != spec) {
        if (idx < 0 || idx >= c->count_modes) return fail(err, n, "--drm-mode: no mode with that index");
        *out = c->modes[idx];
        return 0;
    }
    unsigned w = 0, h = 0;
    double hz = 0;
    int got = sscanf(spec, "%ux%u@%lf", &w, &h, &hz);
    if (got < 2) return fail(err, n, "--drm-mode: not a mode");
    for (int i = 0; i < c->count_modes; i++) {
        const drmModeModeInfo *m = &c->modes[i];
        if (m->hdisplay != w || m->vdisplay != h) continue;
        if (got == 3 && (int)lround(hz * 100) != (int)lround(mode_hz(m) * 100)) continue;
        *out = *m;
        return 0;
    }
    return fail(err, n, "--drm-mode: the screen offers no such mode");
}

static int open_card(const char *path)
{
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) return -1;
    drmModeRes *res = drmModeGetResources(fd);
    if (!res) { close(fd); return -1; }
    drmModeFreeResources(res);
    return fd;
}

static int kms_open(const Cfg *cfg, char *err, size_t n)
{
    char path[64];
    drmModeRes *res = NULL;
    drmModeConnector *c = NULL;
    if (cfg->drm_device) {
        drm_fd = open_card(cfg->drm_device);
        if (drm_fd < 0) return fail(err, n, "the card given with --drm-device cannot be opened for KMS");
        res = drmModeGetResources(drm_fd);
        c = res ? pick_connector(drm_fd, res, cfg->drm_connector) : NULL;
    } else {
        // as mpv: the first card that does KMS and has a connected output (or the named connector)
        for (int i = 0; i < 16 && !c; i++) {
            snprintf(path, sizeof path, "/dev/dri/card%d", i);
            int fd = open_card(path);
            if (fd < 0) continue;
            drmModeRes *r = drmModeGetResources(fd);
            drmModeConnector *k = r ? pick_connector(fd, r, cfg->drm_connector) : NULL;
            if (k && (cfg->drm_connector || k->connection == DRM_MODE_CONNECTED)) {
                drm_fd = fd; res = r; c = k;
                break;
            }
            drmModeFreeConnector(k);
            drmModeFreeResources(r);
            close(fd);
        }
    }
    if (!res || !c) {
        drmModeFreeResources(res);
        return fail(err, n, cfg->drm_connector ? "no connector with that name" : "no screen is connected");
    }
    if (c->connection != DRM_MODE_CONNECTED) {
        drmModeFreeConnector(c);
        drmModeFreeResources(res);
        return fail(err, n, "the connector has no screen connected");
    }
    if (pick_mode(c, cfg->drm_mode, &mode, err, n)) {
        drmModeFreeConnector(c);
        drmModeFreeResources(res);
        return -1;
    }
    conn_id = c->connector_id;
    drmModeEncoder *e = c->encoder_id ? drmModeGetEncoder(drm_fd, c->encoder_id) : NULL;
    if (e && e->crtc_id) crtc_id = e->crtc_id;
    drmModeFreeEncoder(e);
    for (int i = 0; i < c->count_encoders && !crtc_id; i++) {
        drmModeEncoder *en = drmModeGetEncoder(drm_fd, c->encoders[i]);
        for (int j = 0; en && j < res->count_crtcs; j++)
            if (en->possible_crtcs & (1u << j)) { crtc_id = res->crtcs[j]; break; }
        drmModeFreeEncoder(en);
    }
    drmModeFreeConnector(c);
    drmModeFreeResources(res);
    if (!crtc_id) return fail(err, n, "no display controller for the connector");
    saved_crtc = drmModeGetCrtc(drm_fd, crtc_id);
    app.out_w = mode.hdisplay;
    app.out_h = mode.vdisplay;
    app.out_hz = mode_hz(&mode);

    gbm = gbm_create_device(drm_fd);
    if (!gbm) return fail(err, n, "gbm_create_device failed");
    gsurf = gbm_surface_create(gbm, mode.hdisplay, mode.vdisplay, GBM_FORMAT_XRGB8888, GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
    if (!gsurf) return fail(err, n, "gbm_surface_create failed");
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    dpy = gpd ? gpd(EGL_PLATFORM_GBM_KHR, gbm, NULL) : eglGetDisplay((EGLNativeDisplayType)gbm);
    if (dpy == EGL_NO_DISPLAY) return fail(err, n, "no EGL display on GBM");
    EGLint maj, min;
    if (!eglInitialize(dpy, &maj, &min)) return fail(err, n, "eglInitialize failed");
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8,
                    EGL_ALPHA_SIZE, 0, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR, EGL_NONE };
    EGLConfig cfgs[64];
    EGLint got = 0, pick = -1;
    eglChooseConfig(dpy, ca, cfgs, 64, &got);
    for (int i = 0; i < got; i++) {
        EGLint id;
        eglGetConfigAttrib(dpy, cfgs[i], EGL_NATIVE_VISUAL_ID, &id);
        if (id == GBM_FORMAT_XRGB8888) { pick = i; break; }
    }
    if (pick < 0) return fail(err, n, "no EGL configuration for XRGB8888 with OpenGL ES 3");
    EGLint cta[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    ctx = eglCreateContext(dpy, cfgs[pick], EGL_NO_CONTEXT, cta);
    if (ctx == EGL_NO_CONTEXT) return fail(err, n, "no OpenGL ES 3 context (a Pi 3 has OpenGL ES 2 only)");
    surf = eglCreateWindowSurface(dpy, cfgs[pick], (EGLNativeWindowType)gsurf, NULL);
    if (surf == EGL_NO_SURFACE) return fail(err, n, "eglCreateWindowSurface failed");
    if (!eglMakeCurrent(dpy, surf, surf, ctx)) return fail(err, n, "eglMakeCurrent failed");
    eglSwapInterval(dpy, 0);
    LOG("screen: %ux%u at %.3f Hz, connector %u, crtc %u, %s", mode.hdisplay, mode.vdisplay, app.out_hz, conn_id, crtc_id,
        (const char *)glGetString(GL_RENDERER));
    return 0;
}

static int headless_open(const Cfg *cfg, char *err, size_t n)
{
    PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!gpd) return fail(err, n, "EGL has no eglGetPlatformDisplayEXT");
    dpy = gpd(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (dpy == EGL_NO_DISPLAY) return fail(err, n, "no surfaceless EGL display (Mesa's EGL_MESA_platform_surfaceless)");
    EGLint maj, min;
    if (!eglInitialize(dpy, &maj, &min)) return fail(err, n, "eglInitialize failed (surfaceless)");
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint ca[] = { EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT_KHR, EGL_NONE };
    EGLConfig cfgs[16];
    EGLint got = 0;
    eglChooseConfig(dpy, ca, cfgs, 16, &got);
    EGLint cta[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
    ctx = eglCreateContext(dpy, got > 0 ? cfgs[0] : EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, cta);
    if (ctx == EGL_NO_CONTEXT) return fail(err, n, "no OpenGL ES 3 context (surfaceless)");
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) return fail(err, n, "eglMakeCurrent without a surface failed");
    app.out_w = cfg->headless_w;
    app.out_h = cfg->headless_h;
    app.out_hz = 60.0;
    glGenTextures(1, &h_tex);
    glBindTexture(GL_TEXTURE_2D, h_tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, app.out_w, app.out_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &h_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, h_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, h_tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) return fail(err, n, "the headless framebuffer is incomplete");
    LOG("headless: %dx%d, %s", app.out_w, app.out_h, (const char *)glGetString(GL_RENDERER));
    h_next = now_s();
    return 0;
}

int out_open(const Cfg *cfg, char *err, size_t n)
{
    headless = cfg->headless_w > 0;
    return headless ? headless_open(cfg, err, n) : kms_open(cfg, err, n);
}

GLuint out_target(void) { return headless ? h_fbo : 0; }

static void fb_destroy(struct gbm_bo *bo, void *data)
{
    uint32_t fb = (uint32_t)(uintptr_t)data;
    drmModeRmFB(gbm_device_get_fd(gbm_bo_get_device(bo)), fb);
}

static uint32_t bo_fb(struct gbm_bo *bo)
{
    void *d = gbm_bo_get_user_data(bo);
    if (d) return (uint32_t)(uintptr_t)d;
    uint32_t fb = 0, hs[4] = { gbm_bo_get_handle(bo).u32 }, ss[4] = { gbm_bo_get_stride(bo) }, os[4] = { 0 };
    if (drmModeAddFB2(drm_fd, gbm_bo_get_width(bo), gbm_bo_get_height(bo), GBM_FORMAT_XRGB8888, hs, ss, os, &fb, 0))
        return 0;
    gbm_bo_set_user_data(bo, (void *)(uintptr_t)fb, fb_destroy);
    return fb;
}

static void on_flip(int fd, unsigned seq, unsigned sec, unsigned usec, void *data)
{
    (void)fd; (void)sec; (void)usec; (void)data;
    flip_pending = 0;
    last_seq = seq;
}

static void drain_flip(int timeout_ms)
{
    drmEventContext ev = { .version = 2, .page_flip_handler = on_flip };
    double end = now_s() + timeout_ms / 1000.0;
    while (flip_pending) {
        int left = (int)((end - now_s()) * 1000);
        if (left <= 0) { LOG("a page flip did not complete in time"); flip_pending = 0; break; }
        struct pollfd pf = { drm_fd, POLLIN, 0 };
        int r = poll(&pf, 1, left);
        if (r > 0) drmHandleEvent(drm_fd, &ev);
        else if (r < 0 && errno != EINTR) break;
    }
}

int out_present(void)
{
    if (headless) {
        // paced by the clock: a frame at most every 1/60 s
        glFlush();
        double now = now_s();
        if (h_next > now) {
            struct timespec ts = { (time_t)(h_next - now), (long)((h_next - now - (time_t)(h_next - now)) * 1e9) };
            nanosleep(&ts, NULL);
        }
        now = now_s();
        double step = 1.0 / app.out_hz;
        h_next = h_next + step < now ? now + step : h_next + step;
        app.flips++;
        return 0;
    }
    eglSwapBuffers(dpy, surf);
    struct gbm_bo *next = gbm_surface_lock_front_buffer(gsurf);
    if (!next) { LOG("gbm_surface_lock_front_buffer failed"); return -1; }
    uint32_t fb = bo_fb(next);
    if (!fb) { gbm_surface_release_buffer(gsurf, next); LOG("drmModeAddFB2 failed"); return -1; }
    if (!modeset_done) {
        if (drmModeSetCrtc(drm_fd, crtc_id, fb, 0, 0, &conn_id, 1, &mode)) {
            LOG("the mode could not be set (%s): another program may hold the screen", strerror(errno));
            gbm_surface_release_buffer(gsurf, next);
            return -1;
        }
        modeset_done = 1;
        bo_cur = next;
        app.screen_open = 1;
        app.flips++;
        return 0;
    }
    // pipelined: this frame was drawn while the last flip was pending; wait for it, then queue this one
    if (bo_pending) {
        drain_flip(500);
        if (bo_cur) gbm_surface_release_buffer(gsurf, bo_cur);
        bo_cur = bo_pending;
        bo_pending = NULL;
        // a picture goes out at every vblank, so a step of more than one is a vblank that had no new picture
        if (have_seq && last_seq > prev_seq + 1) app.missed += last_seq - prev_seq - 1;
        prev_seq = last_seq;
        have_seq = 1;
    }
    if (drmModePageFlip(drm_fd, crtc_id, fb, DRM_MODE_PAGE_FLIP_EVENT, NULL)) {
        int e = errno;
        LOG("drmModePageFlip failed (%s)", strerror(e));
        gbm_surface_release_buffer(gsurf, next);
        if (e != EBUSY) return -1;
        // nothing queued, so nothing paces the next turn: wait a frame's time here
        double t = 1.0 / (app.out_hz > 1 ? app.out_hz : 60.0);
        struct timespec ts = { 0, (long)(t * 1e9) };
        nanosleep(&ts, NULL);
        return 0;
    }
    flip_pending = 1;
    bo_pending = next;
    app.flips++;
    return 0;
}

// Wait until the next frame may be drawn, at most `seconds`, or until something wakes the loop.
void out_wait(double seconds)
{
    struct pollfd pf[2] = { { app.wake_fd[0], POLLIN, 0 }, { headless ? -1 : drm_fd, POLLIN, 0 } };
    double until = now_s() + seconds;
    int ms = (int)ceil((until - now_s()) * 1000);
    if (ms < 0) ms = 0;
    int r = poll(pf, 2, ms);
    if (r > 0 && (pf[0].revents & POLLIN)) {
        char b[64];
        while (read(app.wake_fd[0], b, sizeof b) > 0) {}
    }
    if (r > 0 && !headless && (pf[1].revents & POLLIN)) {
        drmEventContext ev = { .version = 2, .page_flip_handler = on_flip };
        drmHandleEvent(drm_fd, &ev);
    }
}

void out_close(void)
{
    if (!headless && drm_fd >= 0) {
        if (flip_pending) drain_flip(200);
        if (saved_crtc) {
            drmModeSetCrtc(drm_fd, saved_crtc->crtc_id, saved_crtc->buffer_id, saved_crtc->x, saved_crtc->y, &conn_id, 1, &saved_crtc->mode);
            drmModeFreeCrtc(saved_crtc);
            saved_crtc = NULL;
        }
    }
    if (dpy != EGL_NO_DISPLAY) {
        eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (surf != EGL_NO_SURFACE) eglDestroySurface(dpy, surf);
        if (ctx != EGL_NO_CONTEXT) eglDestroyContext(dpy, ctx);
        eglTerminate(dpy);
    }
    if (!headless) {
        if (bo_pending) gbm_surface_release_buffer(gsurf, bo_pending);
        if (bo_cur) gbm_surface_release_buffer(gsurf, bo_cur);
        if (gsurf) gbm_surface_destroy(gsurf);
        if (gbm) gbm_device_destroy(gbm);
        if (drm_fd >= 0) close(drm_fd);
    }
}
