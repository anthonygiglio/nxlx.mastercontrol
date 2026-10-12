/* SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * A STAND-IN for the library the NDI helper loads. It is NOT NDI and holds nothing of NDI's: it was written from
 * what pvj/ndi.py calls (class CtypesLibrary and the ctypes structures above it) and from nothing else. It sends and
 * receives nothing on any network. It exists so that the whole road from the helper to the player can be run on a
 * machine that has no copy of NDI's library (tests/real_install_test.sh, tests/test_ndi_standin.py).
 *
 * What it gives: one "finder" that always reports the sources below, and a "receiver" that makes up frames by the
 * clock. What a receiver does is decided by WORDS IN THE NAME of the source it is opened with, because the
 * helper's environment is fixed by its unit and nothing else reaches it:
 *
 *   "one still"      ONE picture, then no picture ever again, and sound that goes on (NDI's Test Patterns does that)
 *   "with sound"     sound with the picture; without these words (and without "one still") never any sound
 *   "at once"        the first picture the moment the receiver is made (see below)
 *   "changes size"   four seconds at the first size, then a smaller one for good: every receiver made for that
 *                    source after the first, in the same process, is small from its start (a sender that changed
 *                    stays changed)
 *   "then still"     five seconds of the moving picture, then no picture ever again; its sound goes on
 *   "after a short gap" / "after a long gap"
 *                    the sound shows itself for a tenth of a second (so the helper knows the source has sound),
 *                    then there is none until 0.8 s (short) or 6 s (long) after the first picture, then it goes on.
 *                    So the first sound the player is given comes clearly AFTER the first picture, as it does from
 *                    a real sender (measured on the Pi, 2026-10-12: 11 ms, and 4.7 s after a switch of the sound);
 *                    without this the sound began at the picture's own time and no test saw a player that waits
 *                    for the picture to reach the sound's time before it plays any
 *   "big"            1280 x 720 (changing to 1264 x 704) in place of 320 x 180 (changing to 256 x 144). A big
 *                    picture is 1.8 MB, more than a pipe holds (1 MiB for an account without privileges), so the
 *                    helper's writer is in the middle of a picture most of the time, as it is with a real sender
 *
 * A picture that is not "one still" changes 30 times a second. The names are in SOURCES below.
 *
 * The picture is progressive, "UYVY" (two bytes a pixel), with lines padded by 16 bytes, so the helper's
 * copying of padded lines is used. Byte b of line y of picture n is (b + 3 * y + 5 * n) & 255; the padding is 0xEE.
 * The sound is 48000 samples a second, two channels, 32-bit floats, one plane a channel, 1600 samples a block
 * (a thirtieth of a second): a square wave at a tenth of full scale, 500 Hz on the left and 250 Hz on the right.
 *
 * Nothing comes in the first second after a receiver is made, as a connection to a real sender takes a moment;
 * except from the two "at once" sources, which are there for the hand-over from one source to the next: a helper
 * that has its first picture at once can open the pipe while the player still holds it open from the source
 * before (see pvj/NDI.md, "What the tests prove").
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
#define LESS_WIDTH 256 /* what "changes size" changes to */
#define LESS_HEIGHT 144
#define BIG_WIDTH 1280
#define BIG_HEIGHT 720
#define BIG_LESS_WIDTH 1264
#define BIG_LESS_HEIGHT 704
#define CHANGE_AFTER 120 /* pictures: four seconds */
#define STOP_AFTER 150   /* pictures: five seconds */
#define PAD 16
#define SOURCE_COUNT_MAX 32
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
	int64_t began;      /* the clock when the first picture is due: CONNECT_NS after the receiver was made, or at once */
	int64_t next_video; /* the number of the next picture */
	int64_t next_audio; /* the number of the next block of sound */
	int still_sent;
	int changes;        /* "changes size": the picture number from which it is the smaller size; -1 for never */
	int big;
	int64_t gap_from;   /* "after a ... gap": no block of sound whose number is from this one ... */
	int64_t gap_to;     /* ... up to and not including this one; both 0 for no gap */
	int64_t stops;      /* "then still": the number of the first picture that is never sent; -1 for never */
	pthread_mutex_t lock; /* the picture's caller and the sound's caller are two threads */
} standin_recv;

static const standin_source SOURCES[] = {
	{"PVJ-STANDIN (moving)", "10.255.255.1:5961"},
	{"PVJ-STANDIN (moving with sound)", "10.255.255.1:5962"},
	{"PVJ-STANDIN (one still then sound)", "10.255.255.1:5963"},
	{"PVJ-STANDIN (at once A)", "10.255.255.1:5964"},
	{"PVJ-STANDIN (at once B)", "10.255.255.1:5965"},
	{"PVJ-STANDIN (changes size)", "10.255.255.1:5966"},
	{"PVJ-STANDIN (big moving with sound)", "10.255.255.1:5967"},
	{"PVJ-STANDIN (big one still then sound)", "10.255.255.1:5968"},
	{"PVJ-STANDIN (big at once A)", "10.255.255.1:5969"},
	{"PVJ-STANDIN (big at once B)", "10.255.255.1:5970"},
	{"PVJ-STANDIN (big at once A with sound)", "10.255.255.1:5971"},
	{"PVJ-STANDIN (big at once B with sound)", "10.255.255.1:5972"},
	{"PVJ-STANDIN (big changes size)", "10.255.255.1:5973"},
	{"PVJ-STANDIN (big changes size with sound)", "10.255.255.1:5974"},
	{"PVJ-STANDIN (moving then still with sound)", "10.255.255.1:5975"},
	{"PVJ-STANDIN (one still then sound after a short gap)", "10.255.255.1:5976"},
	{"PVJ-STANDIN (one still then sound after a long gap)", "10.255.255.1:5977"},
	{"PVJ-STANDIN (moving then still with sound after a short gap)", "10.255.255.1:5978"},
};
#define SOURCE_COUNT ((uint32_t)(sizeof SOURCES / sizeof SOURCES[0]))
static int finder_token;
static pthread_mutex_t changed_lock = PTHREAD_MUTEX_INITIALIZER;
static char changed_before[SOURCE_COUNT_MAX]; /* by source: a receiver for it was made in this process already */

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

static int fill_video(standin_video *v, int64_t n, int64_t now, int big, int less)
{
	const int width = big ? (less ? BIG_LESS_WIDTH : BIG_WIDTH) : (less ? LESS_WIDTH : WIDTH);
	const int height = big ? (less ? BIG_LESS_HEIGHT : BIG_HEIGHT) : (less ? LESS_HEIGHT : HEIGHT);
	const int stride = 2 * width + PAD;
	uint8_t *data = malloc((size_t)stride * height);
	int y, b;
	if (data == NULL)
		return KIND_NONE;
	for (y = 0; y < height; y++) {
		uint8_t *line = data + (size_t)y * stride;
		for (b = 0; b < 2 * width; b++)
			line[b] = (uint8_t)((b + 3 * y + 5 * n) & 255);
		memset(line + 2 * width, 0xEE, PAD);
	}
	memset(v, 0, sizeof *v);
	v->xres = width;
	v->yres = height;
	v->fourcc = FOURCC_PICTURE;
	v->fps_n = 30;
	v->fps_d = 1;
	v->aspect = (float)width / (float)height;
	v->format = PROGRESSIVE;
	v->timecode = now / 100;
	v->data = data;
	v->stride = stride;
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
		int less = 0;
		pthread_mutex_lock(&r->lock);
		if (video != NULL) {
			if (r->mode == MODE_STILL) {
				if (!r->still_sent && now >= r->began) {
					r->still_sent = 1;
					n = 0;
				} else if (!r->still_sent && r->began < wake) {
					wake = r->began;
				}
			} else if (r->stops < 0 || r->next_video < r->stops) {
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
			if (r->next_audio >= r->gap_from && r->next_audio < r->gap_to) {
				r->next_audio = r->gap_to; /* the gap: these blocks are never sent */
				due = r->began + TICK(r->next_audio + 1);
			}
			if (now >= due)
				k = r->next_audio++;
			else if (due < wake)
				wake = due;
		}
		less = r->changes >= 0 && n >= r->changes;
		pthread_mutex_unlock(&r->lock);
		if (n >= 0)
			return fill_video(video, n, now, r->big, less);
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
		*count = SOURCE_COUNT;
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
	r->began = now_ns() + (strstr(name, "at once") != NULL ? 0 : CONNECT_NS);
	r->changes = -1;
	r->big = strstr(name, "big") != NULL;
	r->stops = strstr(name, "then still") != NULL ? STOP_AFTER : -1;
	if (strstr(name, "after a short gap") != NULL) {
		r->gap_from = 3; /* blocks are a thirtieth of a second: 0.1 s of sound, then none until 0.8 s */
		r->gap_to = 24;
	} else if (strstr(name, "after a long gap") != NULL) {
		r->gap_from = 3;
		r->gap_to = 180; /* until 6 s */
	}
	if (strstr(name, "changes size") != NULL) {
		uint32_t i, which = 0;
		for (i = 0; i < SOURCE_COUNT && i < SOURCE_COUNT_MAX; i++)
			if (strcmp(name, SOURCES[i].name) == 0)
				which = i;
		pthread_mutex_lock(&changed_lock);
		r->changes = changed_before[which] ? 0 : CHANGE_AFTER;
		changed_before[which] = 1;
		pthread_mutex_unlock(&changed_lock);
	}
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
