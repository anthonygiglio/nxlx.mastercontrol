/* SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * A STAND-IN for the library the NDI helper loads. It is NOT NDI and holds nothing of NDI's: it was written from
 * what pvj/ndi.py calls (class CtypesLibrary and the ctypes structures above it) and from nothing else. It sends and
 * receives nothing on any network. It exists so that the whole road from the helper to the player can be run on a
 * machine that has no copy of NDI's library (tests/real_install_test.sh, tests/test_ndi_standin.py).
 *
 * What it gives: one "finder" that always reports the three sources below, and a "receiver" that makes up frames
 * by the clock. Which of the three behaviours a receiver has is decided by the NAME of the source it is opened
 * with, because the helper's environment is fixed by its unit and nothing else reaches it:
 *
 *   "PVJ-STANDIN (moving)"                  a picture that changes, 30 times a second, and never any sound
 *   "PVJ-STANDIN (moving with sound)"       the same picture, and sound
 *   "PVJ-STANDIN (one still then sound)"    ONE picture, then no picture ever again, and sound that goes on
 *
 * The picture is 320 x 180, progressive, "UYVY" (two bytes a pixel), with lines padded by 16 bytes, so the helper's
 * copying of padded lines is used. Byte b of line y of picture n is (b + 3 * y + 5 * n) & 255; the padding is 0xEE.
 * The sound is 48000 samples a second, two channels, 32-bit floats, one plane a channel, 1600 samples a block
 * (a thirtieth of a second): a square wave at a tenth of full scale, 500 Hz on the left and 250 Hz on the right.
 *
 * Nothing comes in the first second after a receiver is made, as a connection to a real sender takes a moment. It
 * also keeps the tests away from something they are not about: a helper that has its first picture at once can
 * open the pipe while the player still holds it open from the source before (see pvj/NDI.md, "What the tests
 * prove"), and what happens then is a question of its own.
 *
 * Build:  cc -shared -fPIC -pthread -O2 -o libndi.so.6 standin.c
 */
#define _POSIX_C_SOURCE 200809L
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ---- the structures, field for field as pvj/ndi.py declares them (_Source, _FindCreate, _RecvCreate, _Video,
 * _Audio, _Perf). The unit test compares every size and offset with ctypes (pvj_standin_layout below). */
typedef struct {
	const char *name;
	const char *url;
} standin_source;

typedef struct {
	bool show_local;
	const char *groups;
	const char *extra_ips;
} standin_find_create;

typedef struct {
	standin_source source;
	int color;
	int bandwidth;
	bool fields;
	const char *name;
} standin_recv_create;

typedef struct {
	int xres;
	int yres;
	uint32_t fourcc;
	int fps_n;
	int fps_d;
	float aspect;
	int format;
	int64_t timecode;
	uint8_t *data;
	int stride;
	const char *metadata;
	int64_t timestamp;
} standin_video;

typedef struct {
	int rate;
	int channels;
	int samples;
	int64_t timecode;
	uint32_t fourcc;
	uint8_t *data;
	int stride;
	const char *metadata;
	int64_t timestamp;
} standin_audio;

typedef struct {
	int64_t video;
	int64_t audio;
	int64_t metadata;
} standin_perf;

/* ---- what is sent */
#define WIDTH 320
#define HEIGHT 180
#define PAD 16
#define STRIDE (2 * WIDTH + PAD)
#define RATE 48000
#define CHANNELS 2
#define SAMPLES 1600
#define FOURCC_PICTURE 0x59565955u /* the letters U Y V Y, as ndi.FOURCC_UYVY */
#define FOURCC_SOUND 0x70544C46u   /* the letters F L T p, as ndi.AUDIO_FLTP */
#define KIND_NONE 0
#define KIND_VIDEO 1
#define KIND_AUDIO 2
#define PROGRESSIVE 1
/* one picture and one block of sound both last a thirtieth of a second: number k begins at k * 1e9 / 30 ns */
#define TICK(k) ((int64_t)(k) * 100000000 / 3)
#define CONNECT_NS 1000000000 /* nothing comes before a receiver is this old */
#define RESYNC_NS 500000000 /* sound more than this late is not caught up with: the count starts again at the clock */

enum { MODE_MOVING, MODE_SOUND, MODE_STILL };

typedef struct {
	int mode;
	int64_t began;      /* the clock when the first picture is due: CONNECT_NS after the receiver was made */
	int64_t next_video; /* the number of the next picture */
	int64_t next_audio; /* the number of the next block of sound */
	int still_sent;
	pthread_mutex_t lock; /* the picture's caller and the sound's caller are two threads */
} standin_recv;

static const standin_source SOURCES[3] = {
	{"PVJ-STANDIN (moving)", "10.255.255.1:5961"},
	{"PVJ-STANDIN (moving with sound)", "10.255.255.1:5962"},
	{"PVJ-STANDIN (one still then sound)", "10.255.255.1:5963"},
};
static int finder_token;

static int64_t now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000000000 + ts.tv_nsec;
}

static void sleep_ns(int64_t ns)
{
	struct timespec ts;
	if (ns <= 0)
		return;
	ts.tv_sec = (time_t)(ns / 1000000000);
	ts.tv_nsec = (long)(ns % 1000000000);
	nanosleep(&ts, NULL);
}

static int fill_video(standin_video *v, int64_t n, int64_t now)
{
	uint8_t *data = malloc((size_t)STRIDE * HEIGHT);
	int y, b;
	if (data == NULL)
		return KIND_NONE;
	for (y = 0; y < HEIGHT; y++) {
		uint8_t *line = data + (size_t)y * STRIDE;
		for (b = 0; b < 2 * WIDTH; b++)
			line[b] = (uint8_t)((b + 3 * y + 5 * n) & 255);
		memset(line + 2 * WIDTH, 0xEE, PAD);
	}
	memset(v, 0, sizeof *v);
	v->xres = WIDTH;
	v->yres = HEIGHT;
	v->fourcc = FOURCC_PICTURE;
	v->fps_n = 30;
	v->fps_d = 1;
	v->aspect = (float)WIDTH / (float)HEIGHT;
	v->format = PROGRESSIVE;
	v->timecode = now / 100;
	v->data = data;
	v->stride = STRIDE;
	v->metadata = NULL;
	v->timestamp = now / 100;
	return KIND_VIDEO;
}

static int fill_audio(standin_audio *a, int64_t k, int64_t now)
{
	float *data = malloc(sizeof(float) * SAMPLES * CHANNELS);
	int64_t first = k * SAMPLES;
	int i;
	if (data == NULL)
		return KIND_NONE;
	for (i = 0; i < SAMPLES; i++) {
		int64_t s = first + i;
		data[i] = ((s / 48) & 1) ? 0.1f : -0.1f;           /* the left plane: 500 Hz */
		data[SAMPLES + i] = ((s / 96) & 1) ? 0.1f : -0.1f; /* the right plane: 250 Hz */
	}
	memset(a, 0, sizeof *a);
	a->rate = RATE;
	a->channels = CHANNELS;
	a->samples = SAMPLES;
	a->timecode = now / 100;
	a->fourcc = FOURCC_SOUND;
	a->data = (uint8_t *)data;
	a->stride = (int)(sizeof(float) * SAMPLES);
	a->metadata = NULL;
	a->timestamp = now / 100;
	return KIND_AUDIO;
}

/* One turn for either caller: whatever was asked for and is due is handed out; else it waits, at most timeout_ms. */
static int capture(standin_recv *r, standin_video *video, standin_audio *audio, uint32_t timeout_ms)
{
	int64_t deadline;
	if (r == NULL)
		return KIND_NONE;
	deadline = now_ns() + (int64_t)timeout_ms * 1000000;
	for (;;) {
		int64_t now = now_ns(), wake = deadline, n = -1, k = -1;
		pthread_mutex_lock(&r->lock);
		if (video != NULL) {
			if (r->mode == MODE_STILL) {
				if (!r->still_sent && now >= r->began) {
					r->still_sent = 1;
					n = 0;
				} else if (!r->still_sent && r->began < wake) {
					wake = r->began;
				}
			} else {
				int64_t due = r->began + TICK(r->next_video);
				if (now >= due) {
					n = (now - r->began) * 3 / 100000000; /* the picture of this moment: a late caller skips */
					if (n < r->next_video)
						n = r->next_video;
					r->next_video = n + 1;
				} else if (due < wake) {
					wake = due;
				}
			}
		}
		if (n < 0 && audio != NULL && r->mode != MODE_MOVING) {
			int64_t due = r->began + TICK(r->next_audio + 1); /* a block is ready when its last sample exists */
			if (now - due > RESYNC_NS) {
				r->next_audio = (now - r->began) * 3 / 100000000 - 1;
				due = r->began + TICK(r->next_audio + 1);
			}
			if (now >= due)
				k = r->next_audio++;
			else if (due < wake)
				wake = due;
		}
		pthread_mutex_unlock(&r->lock);
		if (n >= 0)
			return fill_video(video, n, now);
		if (k >= 0)
			return fill_audio(audio, k, now);
		if (now >= deadline)
			return KIND_NONE;
		sleep_ns(wake - now);
	}
}

/* ---- the entry points pvj/ndi.py declares (CtypesLibrary._declare), and no others */
bool NDIlib_initialize(void)
{
	return true;
}

const char *NDIlib_version(void)
{
	return "PVJ STAND-IN 1 (not NDI; tests/ndi_standin)";
}

void *NDIlib_find_create_v2(const standin_find_create *settings)
{
	(void)settings;
	return &finder_token;
}

void NDIlib_find_destroy(void *finder)
{
	(void)finder;
}

const standin_source *NDIlib_find_get_current_sources(void *finder, uint32_t *count)
{
	if (finder != &finder_token) {
		if (count != NULL)
			*count = 0;
		return NULL;
	}
	if (count != NULL)
		*count = 3;
	return SOURCES;
}

void *NDIlib_recv_create_v3(const standin_recv_create *settings)
{
	standin_recv *r;
	const char *name;
	if (settings == NULL || settings->source.name == NULL)
		return NULL;
	name = settings->source.name;
	r = calloc(1, sizeof *r);
	if (r == NULL)
		return NULL;
	if (strstr(name, "one still") != NULL)
		r->mode = MODE_STILL;
	else if (strstr(name, "with sound") != NULL)
		r->mode = MODE_SOUND;
	else
		r->mode = MODE_MOVING;
	r->began = now_ns() + CONNECT_NS;
	pthread_mutex_init(&r->lock, NULL);
	return r;
}

void NDIlib_recv_destroy(void *recv)
{
	standin_recv *r = recv;
	if (r == NULL)
		return;
	pthread_mutex_destroy(&r->lock);
	free(r);
}

int NDIlib_recv_capture_v2(void *recv, standin_video *video, void *audio, void *metadata, uint32_t timeout_ms)
{
	(void)audio; /* pvj/ndi.py asks this call for the picture only */
	(void)metadata;
	return capture(recv, video, NULL, timeout_ms);
}

int NDIlib_recv_capture_v3(void *recv, standin_video *video, standin_audio *audio, void *metadata, uint32_t timeout_ms)
{
	(void)metadata;
	return capture(recv, video, audio, timeout_ms);
}

void NDIlib_recv_free_video_v2(void *recv, const standin_video *video)
{
	(void)recv;
	if (video != NULL)
		free(video->data);
}

void NDIlib_recv_free_audio_v3(void *recv, const standin_audio *audio)
{
	(void)recv;
	if (audio != NULL)
		free(audio->data);
}

void NDIlib_recv_get_performance(void *recv, standin_perf *total, standin_perf *dropped)
{
	(void)recv;
	if (total != NULL)
		memset(total, 0, sizeof *total);
	if (dropped != NULL)
		memset(dropped, 0, sizeof *dropped);
}

/* ---- for the unit test only: the size of each structure and the place of each field, in the order of the fields
 * above, so that the test can compare them with what ctypes makes of pvj/ndi.py's declarations. Returns how many
 * numbers there are; writes at most `room` of them. */
int pvj_standin_layout(int *out, int room)
{
	const int numbers[] = {
		(int)sizeof(standin_source), (int)offsetof(standin_source, name), (int)offsetof(standin_source, url),
		(int)sizeof(standin_find_create), (int)offsetof(standin_find_create, show_local),
		(int)offsetof(standin_find_create, groups), (int)offsetof(standin_find_create, extra_ips),
		(int)sizeof(standin_recv_create), (int)offsetof(standin_recv_create, source), (int)offsetof(standin_recv_create, color),
		(int)offsetof(standin_recv_create, bandwidth), (int)offsetof(standin_recv_create, fields),
		(int)offsetof(standin_recv_create, name),
		(int)sizeof(standin_video), (int)offsetof(standin_video, xres), (int)offsetof(standin_video, yres),
		(int)offsetof(standin_video, fourcc), (int)offsetof(standin_video, fps_n), (int)offsetof(standin_video, fps_d),
		(int)offsetof(standin_video, aspect), (int)offsetof(standin_video, format), (int)offsetof(standin_video, timecode),
		(int)offsetof(standin_video, data), (int)offsetof(standin_video, stride), (int)offsetof(standin_video, metadata),
		(int)offsetof(standin_video, timestamp),
		(int)sizeof(standin_audio), (int)offsetof(standin_audio, rate), (int)offsetof(standin_audio, channels),
		(int)offsetof(standin_audio, samples), (int)offsetof(standin_audio, timecode), (int)offsetof(standin_audio, fourcc),
		(int)offsetof(standin_audio, data), (int)offsetof(standin_audio, stride), (int)offsetof(standin_audio, metadata),
		(int)offsetof(standin_audio, timestamp),
		(int)sizeof(standin_perf), (int)offsetof(standin_perf, video), (int)offsetof(standin_perf, audio),
		(int)offsetof(standin_perf, metadata),
	};
	const int count = (int)(sizeof numbers / sizeof numbers[0]);
	int i;
	for (i = 0; i < count && i < room; i++)
		out[i] = numbers[i];
	return count;
}
