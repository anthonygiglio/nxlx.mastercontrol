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

#define PVJ_VERSION "0.11.0"
#define MAX_SHADERS 16
#define LAYERS 2                    // source 1's video layers (phase 2, D98): the one on screen and the one a crossfade comes in on
#define MAX_SOURCES 8               // sources (D101): source 1 is LAYERS layers, each other source one layer
#define MAX_LAYERS (LAYERS + MAX_SOURCES - 1)   // every video layer: 0 and 1 are source 1's, layer k (2 to 8) is source k
#define FADE_MAX_SECONDS 60.0
#define FADE_FIRST_FRAME 10.0       // seconds a crossfade waits for its clip's first frame, then fades anyway (as D71)
#define STOP_REPLY 1                // the reply_userdata of the stop that ends a fade

#define FINAL_LINES 720             // the size the final pass draws at while the display's scaler brings it to the output (D108)
enum { FADE_OFF, FADE_WAITING, FADE_RUNNING };
enum { FINAL_OFF, FINAL_AUTO, FINAL_720 };
// The mixing stage (D101): how a layer is laid over what is under it. Only "normal" (an alpha mix by the layer's
// opacity) is built; the others (add, screen, multiply, difference, lighten, darken) are a line each in the stage's
// shaders when they come, and are refused until then.
enum { BLEND_NORMAL, BLENDS };
extern const char *const BLEND_NAMES[BLENDS];
// The wipes and the slides (D111): the mixing stage's other way of laying the incoming layer over the outgoing one.
// By the panel's names (pvj/transitions.py). `axis`: 0 the edge runs across the columns, 1 across the rows; `low`: the
// incoming picture is on the side of column 0 or of the top row; `shift`: the outgoing picture moves away (a slide)
// instead of standing where it is and being cut (a wipe).
#define MOVES 8
typedef struct { const char *name; int axis, low, shift; } Move;
extern const Move MOVE_LIST[MOVES];

typedef struct Hook Hook;

// The renderer's own shader programs (D106, fx.c): ISF generators and effects with their inputs as uniforms.
#define FX_SLOTS 4                  // per source: 0 the generator (source 1 only, until step d), 1 to 3 its effects in order (D106)
#define FX_MAX_INPUTS 24
#define FX_NAME 41                  // an input's name: 32 letters, and the panel's pvj_in_ before it
enum { FX_GENERATOR, FX_FILTER };
enum { FXT_FLOAT, FXT_BOOL, FXT_LONG, FXT_COLOR, FXT_POINT, FXT_N };
enum { FXC_SPEED, FXC_HUE, FXC_GAIN, FXC_AMOUNT, FXC_OFFSET, FXC_N };
typedef struct { char name[FX_NAME]; int type; float v[4]; } FxValue;
typedef struct FxProg FxProg;
// One slot as the socket's threads and the render thread share it (under app.lock).
typedef struct {
    FxProg *pending;                // a load the render thread has not taken yet
    int pending_clear;
    long asked, answered;           // loads and clears asked; the last the render thread has answered
    long dropped;                   // the last request a layer's end threw away unbuilt (a load waiting for it was never built)
    char answer[480];               // its answer: empty when it was done, else why not
    int on, constants, kind, ninputs;
    FxValue val[FX_MAX_INPUTS];     // the values now: the socket sets them, the render thread copies them each frame
    float ctl[FXC_N];
    long values_serial;
    int reset_time;                 // the offset was set: TIME starts again from it
    // the render thread's word
    double time;
    long runs, drops, rebuilds;
    int w, h;                       // the drawing size of its program
    char path[1024];                // the file it was built from (the panel tells a slow build from a refusal by it)
    char state[160];
} FxSlot;

// The graphics stage (D129, slice 3, gfx.c): text and logos drawn over the final picture, below the text layer. One slot
// a graphic, 1 to GFX_SLOTS (the order of the panel's list: a later slot is drawn over an earlier one). The socket's
// threads write a slot under app.lock; the render thread reads it once a frame (gfx_frame) and uploads a picture that
// was loaded (gfx_take). The picture is a premultiplied B G R A file the panel's rasteriser wrote.
#define GFX_SLOTS 8
#define GFX_LINES 6                 // lines of text a typewriter reveals one after the other
#define GFX_BYTES_MAX (48u << 20)   // all the pictures together, loaded or waiting (design 3.2)
#define GFX_SIDE_MAX 4096           // a picture's side, and never more than the GPU's own limit
#define GFX_SECONDS_MAX 60.0
enum { GM_NONE, GM_FADE, GM_TYPE, GM_SCROLL, GM_CRAWL, GM_N };
typedef struct {
    // the socket's side (under app.lock)
    uint8_t *pending;               // a picture waiting for the render thread: w x h x 4 bytes, B G R A
    int pw, ph, plines;
    float pext[GFX_LINES][2];       // each line's ink from left to right, as parts of the width
    int clear;                      // asked: the render thread deletes the texture
    int want_w, want_h;             // the newest picture's size, loaded or waiting (the budget)
    long loads;
    int motion, loop, on;
    float x, y, opacity;            // where the picture's top left corner is, in pixels of the screen, and a multiplier
    float seconds, out, speed;      // fade in or typewriter time; fade out time; scroll and crawl speed (percent of the screen a second)
    double shown_at, hidden_at;     // monotonic seconds: the last show or replay; the last hide (0: it is on)
    // the render thread's word (under app.lock)
    int loaded, w, h, lines;
    float ext[GFX_LINES][2];
    float alpha;                    // the last frame's
    long draws;
} GfxSlot;

typedef struct {
    const char *socket_path;
    const char *drm_device, *drm_connector, *drm_mode;
    int headless_w, headless_h;     // > 0: no screen, EGL surfaceless, paced by the clock
    int detail;                     // lines inside: 0 = the output's own height
    const char *hwdec;              // mpv's hwdec for the video layer ("no" by default)
    const char *audio_device;
    int fast;                       // mpv's cheap scalers in the video layer (a Pi)
    int skip_deblock;               // the decoders leave out the deblocking filter (a Pi, D120)
    int every_vblank;               // accepted for old command lines: a picture goes out at every vblank anyway
    int frames;                     // > 0: end after this many presented frames (tests)
    int sources;                    // sources 1 to N (D101): 1 by default, at most MAX_SOURCES
    int fx_fps;                     // a generator, and an effect that reads the clock, run at most this often (D106)
    int final_mode;                 // --final (D108): FINAL_OFF, FINAL_AUTO or FINAL_720
    int verbose;
} Cfg;

typedef struct {
    // fixed once the output is open (read by the socket thread without the lock)
    int out_w, out_h, in_w, in_h;
    double out_hz;
    pid_t pid;
    mpv_handle *layer[MAX_LAYERS], *osd;
    int nsources;                   // sources 1 to nsources; layers 0 to nsources are open (source k > 1 is layer k)
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

    // the final pass's size (D108). final_want: the lines asked (0: the output's own), set by the socket's thread or
    // at the start, taken by the render thread at the top of a frame (atomics). final_lines: what the frame on its way
    // to the screen was drawn at (atomics). final_why: why the scaler cannot be used, in words, under app.lock; empty
    // while it can. out_w and out_h never change: they are the screen, whichever way the final pass draws.
    int final_want, final_lines;
    long final_switches;
    char final_why[160];

    // a capture of the final picture, asked by the socket thread
    char *capture_path;
    int capture_serial, capture_done_serial, capture_result;
    int capture_buffer;             // tests only: the buffer as it goes out, not the picture at the screen's size

    // the crossfade (D98) and the wipes and slides (D111): the front layer is the incoming clip from the command on; the
    // other one goes out under it
    struct {
        int phase;                  // FADE_OFF, FADE_WAITING (for the incoming clip's first frame), FADE_RUNNING
        double seconds, asked, t0;  // its length; when it was asked; when it began to move (monotonic seconds)
        double progress;            // 0 to 1, the incoming layer's share of the picture (render thread writes it)
        int base_starts;            // the incoming layer's count of started files before its load (see Layer)
        const Move *move;           // a wipe or a slide; NULL: the crossfade's mix
        long steps;                 // pictures drawn with both layers in them, in this transition or the last
        int program;                // the incoming layer's picture is a generator (pvj-fade-to-program, D130): the clock starts at its first drawn frame
    } fade;
    long fades, fades_done, fades_cut;  // crossfades asked; run to their end; ended early (a load, a stop, a new fade)
    long fades_from_nothing;        // crossfades asked with nothing on the screen: loaded as a cut instead
    long program_fades;             // fades into a program asked (pvj-fade-to-program), whether they ran or were a cut from nothing
    long sound_held;                // crossfades whose outgoing core had not closed its sound output in time
    // per video layer, kept by its event thread (atomics): files started so far, and which of those starts the last
    // playback restart (a first frame ready, after a load or a seek) and the last failed load belong to. libmpv sends
    // a layer's events in order, so what an older file of that layer said is never taken for the incoming clip's.
    int starts[MAX_LAYERS], restart_of[MAX_LAYERS], failed_of[MAX_LAYERS];
    int stops_asked[MAX_LAYERS], stops_done[MAX_LAYERS];    // the stops that end a fade, and those libmpv has run (atomics)
    int idle[MAX_LAYERS];           // the layer's idle-active, as observed by its event thread (atomics)
    long layer_drawn[MAX_LAYERS];   // frames each layer converted (atomics: the render thread adds, the socket reads)
    long source_loads[MAX_LAYERS];  // loads asked of source k > 1 (pvj-source k loadfile), by its index
    // each source's mix (D101): its opacity (0 to 1) and blend, by layer index (source k > 1 is layer k; source 1's
    // is its own picture, the program, at 1 and normal). Set by the socket's threads under app.lock and read once a
    // frame in the render loop's one look under it, so a frame never mixes two settings.
    float opacity[MAX_LAYERS];
    int blend[MAX_LAYERS];

    // source 1's own programs (D106): the slots, and the level. While a program is on, source 1's level (the
    // panel's `brightness`) is drawn by the renderer after the programs, so an effect that adds light cannot undo a
    // Blackout; libmpv's own brightness on source 1's layers is then held at 0.
    FxSlot fx[MAX_SOURCES][FX_SLOTS];   // by source index (0 is source 1) and slot
    // Source 1's generators, one for each video layer (D130): slot 0 of source 1 on the socket means the front layer's
    // (app.gen[front]); app.fx[0][0] is never used. A generator belongs to its layer: it is that layer's picture, mixed
    // or wiped with the other layer's, and it is cleared with the layer when a fade ends.
    FxSlot gen[LAYERS];
    GfxSlot gfx[GFX_SLOTS];         // the graphics stage (D129, gfx.c)
    int native[MAX_SOURCES];        // the source has a program on (the render thread says, under app.lock)
    int level_native[MAX_SOURCES];  // the socket's side: the source's layers have brightness 0, its level is drawn here
    double level[MAX_SOURCES];      // each source's brightness as last set on the socket, -100 to 100 (D101: the panel's
                                    // level reaches every source; a source's own lasts until the next change of it)
    double fx_fps;
    long fx_drops;                  // generator frames that came late (always added to frame-drop-count: it only rises)
    int level_resync;               // the render thread turned a program on or off: the next client syncs the level
    long fx_passes;                 // the programs' passes drawn so far (atomics)
    double fx_passes_rate;          // ... a second, over the last second (the render thread, under app.lock)
    long fx_gl;                     // GL objects of the programs (atomics)
    int paused[MAX_LAYERS];         // each layer's pause, as observed (atomics)
    // a screenshot while a program is on: the renderer's own picture of source 1, not libmpv's layer. The render
    // thread only reads the pixels back (into a pixel buffer, mapped on the next frame); the client's thread that
    // asked encodes them and writes the file, so the screen never waits for an encoder (the review of #167).
    struct {
        int kind;                   // 0: video (source 1's picture at the inside size), 1: window (the screen's size, text),
                                    // 2: the whole final picture at the screen's size, mapping and text in it (a transition's still)
        int serial, done_serial;
        unsigned char *rgba;        // the pixels (GL's rows, bottom first), handed over with done_serial; NULL: failed
        int w, h;
    } shot;

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
    GLint loc_raw, loc_size, loc_pt, loc_target, loc_input, loc_level;
    int ntex;
    GLuint tex[8];
    GLint tex_loc[8], tex_size_loc[8];
    int tex_w[8], tex_h[8];
    // the other sources' pictures the pass reads (D101): `//!BIND SOURCEk`, k from 2 to MAX_SOURCES; pvj-render
    // supplies each as SOURCEk_raw (with SOURCEk_tex(pos), as HOOKED_tex), never the file
    int nsrc;
    int src[MAX_SOURCES];
    GLint src_loc[MAX_SOURCES];
    GLint src_opacity_loc[MAX_SOURCES];     // SOURCEk_opacity: the source's mix, set every frame
    GLint src_level_loc[MAX_SOURCES];       // SOURCEk_level: its level while it has a program on (D106), else 0
} HookGL;
int hookgl_build(const Hook *h, HookGL *g, char *err, size_t n);
void hookgl_free(HookGL *g);
GLuint gl_program(const char *vs, const char *fs, char *err, size_t n);

// the passes' shared GL state (main.c)
void gl_reset(void);

// the renderer's own programs (fx.c)
int fx_read(const char *path, FxProg **out, char *err, size_t n);
const char *fx_prog_path(const FxProg *p);
void fx_prog_free(FxProg *p);
int fx_prog_kind(const FxProg *p);
int fx_set_value(FxSlot *s, const char *name, const mpv_node *v);
void fx_init(void);
void fx_take(void);
GLuint fx_source(int s, GLuint picture, int fresh, int paused, double now);
int fx_has_generator(int s);
// Source 1's per-layer generators (D130, render thread only): the layer's generator texture (0: it has none), run when
// it is due; *ran is set when it drew a new picture this turn. fx_layer_end takes a layer's generator off (the end of a
// fade, on the outgoing layer).
GLuint fx_layer_picture(int layer, double now, int *ran);
int fx_layer_has_generator(int layer);
int fx_layer_started(int layer);     // the layer's generator has drawn a picture (a fade into a program starts its clock then)
void fx_layer_end(int layer);
void fx_free_all(void);

// the graphics stage (gfx.c)
void gfx_init(void);                // render thread, once the GL context is current
int gfx_side_max(void);
// socket threads (each takes app.lock itself). gfx_load answers NULL, or why not in words.
const char *gfx_load(int slot, const char *path, int w, int h, int lines);
typedef struct { unsigned mask; int motion, loop; double x, y, opacity, seconds, out, speed; } GfxSet;
#define GS_MOTION 1u
#define GS_LOOP 2u
#define GS_X 4u
#define GS_Y 8u
#define GS_OPACITY 16u
#define GS_SECONDS 32u
#define GS_OUT 64u
#define GS_SPEED 128u
void gfx_set(int slot, const GfxSet *s);
void gfx_show(int slot, int still); // on; a graphic that is on already is not restarted. still: a fade or typing is not played (it is all there)
void gfx_replay(int slot);          // on, and the motion starts again
void gfx_hide(int slot);
void gfx_clear(int slot);
// render thread
void gfx_take(void);                // upload the pictures that were loaded, delete the cleared
void gfx_frame(double now);         // this frame's look at every slot
void gfx_draw(void);                // over the final pass's picture, below the text layer
void gfx_free_all(void);
extern long gfx_draws_total, gfx_uploads_total, gfx_gl_objects;      // atomics
long gfx_slot_bytes(const GfxSlot *s);     // under app.lock: what a slot holds or is about to (the larger of the texture and the newest picture)
extern const char *const GFX_MOTIONS[GM_N];
void gl_quad(void);                 // the unit quad (main.c)

// pictures to files (still.c): rows of RGB, top row first
int still_png(int fd, const unsigned char *rgb, int w, int h);
int still_jpeg(int fd, const unsigned char *rgb, int w, int h, int quality);

// the output (out.c)
int out_open(const Cfg *cfg, char *err, size_t n);
// out_open's answer when the graphics are a software rasteriser (llvmpipe and the like) on a screen: main ends with
// EXIT_NO_GPU, which the player's supervisor tells apart from every other failure to start.
#define OUT_NO_GPU (-2)
#define EXIT_NO_GPU 5
GLuint out_target(void);
void out_draw_size(int *w, int *h); // the size this frame's final pass draws at, in the top left of out_target (D108)
int out_final_usable(void);         // the final pass may change size at run time
#ifdef PVJ_RENDER_TEST_HOOKS
void out_test_refuse(void);
#endif
int out_present(void);
void out_wait(double seconds);
void out_close(void);

#endif
