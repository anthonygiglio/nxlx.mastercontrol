// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvj-render: what the parts share. One render thread owns the GL context; one socket thread reads control input
// and talks to libmpv directly (its client API may be used from any thread) and to the render thread through `app`
// under `app.lock`.
#ifndef PVJ_H
#define PVJ_H

#include <pthread.h>
#include <stdint.h>
#include <sys/types.h>
#include <GLES3/gl3.h>
#include <mpv/client.h>

#define PVJ_VERSION "0.1.0"
#define MAX_SHADERS 16

typedef struct Hook Hook;

typedef struct {
    const char *socket_path;
    const char *drm_device, *drm_connector, *drm_mode;
    int headless_w, headless_h;     // > 0: no screen, EGL surfaceless, paced by the clock
    int detail;                     // lines inside: 0 = the output's own height
    const char *hwdec;              // mpv's hwdec for the video layer ("no" by default)
    const char *audio_device;
    int fast;                       // mpv's cheap scalers in the video layer (a Pi)
    int every_vblank;               // draw and flip at every vblank (for measuring), not only on a change
    int frames;                     // > 0: end after this many presented frames (tests)
    int verbose;
} Cfg;

typedef struct {
    // fixed once the output is open (read by the socket thread without the lock)
    int out_w, out_h, in_w, in_h;
    double out_hz;
    pid_t pid;
    mpv_handle *video, *osd;
    int screen_open;
    const Cfg *cfg;

    pthread_mutex_t lock;
    pthread_cond_t cond;
    int quit;
    int exit_code;
    int wake_fd[2];                 // a byte on [1] wakes the render loop

    // the shader list as last set (get_property glsl-shaders gives it back)
    char *shaders[MAX_SHADERS];
    int nshaders;
    // the final pass: set by the socket thread, built by the render thread
    Hook *hook_next;                // the new one (or NULL for none) once hook_pending
    int hook_pending;
    char hook_state[160];           // "off", "on", or why it failed

    // the text layer: drawn over the picture while an overlay or a text is on
    uint64_t overlays;              // bit n: overlay n is on
    double text_until;              // monotonic seconds

    // a capture of the final picture, asked by the socket thread
    char *capture_path;
    int capture_serial, capture_done_serial, capture_result;

    // counters
    long flips, missed, drawn;
} App;

extern App app;

double now_s(void);
void wake_render(void);
void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#define LOG(...) logf_(__VA_ARGS__)

// socket thread (ipc.c)
int ipc_open(const char *path, char *err, size_t n);
void *ipc_thread(void *arg);
void ipc_close(void);

// the final pass from an mpv user shader on the OUTPUT stage (hook.c)
int hook_load(const char *path, Hook **out, char *err, size_t n);   // 1: ours, 0: hand to libmpv, -1: refused
void hook_free(Hook *h);
typedef struct {
    GLuint prog;
    GLint loc_raw, loc_size, loc_pt, loc_target, loc_input;
    int ntex;
    GLuint tex[8];
    GLint tex_loc[8], tex_size_loc[8];
    int tex_w[8], tex_h[8];
} HookGL;
int hookgl_build(const Hook *h, HookGL *g, char *err, size_t n);
void hookgl_free(HookGL *g);
GLuint gl_program(const char *vs, const char *fs, char *err, size_t n);

// the output (out.c)
int out_open(const Cfg *cfg, char *err, size_t n);
GLuint out_target(void);
int out_present(void);
void out_wait(double seconds);
void out_close(void);

#endif
