// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvj-render: what the parts share. One render thread owns the GL context and presents a picture at every vblank;
// the socket has a thread that takes clients and one thread per client, which talk to libmpv directly (its client
// API may be used from any thread) and to the render thread through `app` under `app.lock`.
#ifndef PVJ_H
#define PVJ_H

#include <pthread.h>
#include <stdint.h>
#include <sys/types.h>
#include <GLES3/gl3.h>
#include <mpv/client.h>

#define PVJ_VERSION "0.2.0"
#define MAX_SHADERS 16
#define LAYERS 2                    // video layers (phase 2, D98): the one on screen and the one a crossfade comes in on
#define FADE_MAX_SECONDS 60.0
#define FADE_FIRST_FRAME 10.0       // seconds a crossfade waits for its clip's first frame, then fades anyway (as D71)

enum { FADE_OFF, FADE_WAITING, FADE_RUNNING };

typedef struct Hook Hook;

typedef struct {
    const char *socket_path;
    const char *drm_device, *drm_connector, *drm_mode;
    int headless_w, headless_h;     // > 0: no screen, EGL surfaceless, paced by the clock
    int detail;                     // lines inside: 0 = the output's own height
    const char *hwdec;              // mpv's hwdec for the video layer ("no" by default)
    const char *audio_device;
    int fast;                       // mpv's cheap scalers in the video layer (a Pi)
    int every_vblank;               // accepted for old command lines: a picture goes out at every vblank anyway
    int frames;                     // > 0: end after this many presented frames (tests)
    int verbose;
} Cfg;

typedef struct {
    // fixed once the output is open (read by the socket thread without the lock)
    int out_w, out_h, in_w, in_h;
    double out_hz;
    pid_t pid;
    mpv_handle *layer[LAYERS], *osd;
    int screen_open;
    // which video layer the socket speaks to and the screen shows (the other is idle, or fading out); changed by the
    // socket's thread under app.lock and layer_lock, read anywhere with an atomic load (front_layer, front_handle)
    int front;
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

    // the crossfade (D98): the front layer is the incoming clip from the command on; the other one fades out under it
    struct {
        int phase;                  // FADE_OFF, FADE_WAITING (for the incoming clip's first frame), FADE_RUNNING
        double seconds, asked, t0;  // its length; when it was asked; when it began to move (monotonic seconds)
        double progress;            // 0 to 1, the incoming layer's share of the picture (render thread writes it)
        int base_starts;            // the incoming layer's count of started files before its load (see Layer)
    } fade;
    long fades, fades_done, fades_cut;  // crossfades asked; run to their end; ended early (a load, a stop, a new fade)
    // per video layer, kept by its event thread (atomics): files started so far, and which of those starts the last
    // playback restart (a first frame ready, after a load or a seek) and the last failed load belong to. libmpv sends
    // a layer's events in order, so what an older file of that layer said is never taken for the incoming clip's.
    int starts[LAYERS], restart_of[LAYERS], failed_of[LAYERS];

    // counters
    long flips, missed, drawn, back_drawn;  // drawn: frames of the front layer converted; back_drawn: of the other
    long fences, fences_freed;      // GL fences libmpv made and has not deleted yet; the ones it left that we deleted
    long gl_objects;                // textures, framebuffers, renderbuffers and buffers libmpv holds now (atomics)
} App;

extern App app;

static inline int front_layer(void) { return __atomic_load_n(&app.front, __ATOMIC_ACQUIRE); }
static inline mpv_handle *front_handle(void) { return app.layer[front_layer()]; }

double now_s(void);
void wake_render(void);
void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#define LOG(...) logf_(__VA_ARGS__)

// socket thread (ipc.c)
int ipc_open(const char *path, char *err, size_t n);
void *ipc_thread(void *arg);
int ipc_finished(void);             // ipc_thread has ended, and every client's thread with it
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
