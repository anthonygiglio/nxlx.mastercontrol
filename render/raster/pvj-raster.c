// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// pvj-raster (D129, design/text-and-logo-sources_2026-11-08.md section 7): the one program that decodes a picture a
// person uploaded. It reads ONE request on standard input, writes ONE answer on standard output and exits. It is run
// by systemd for each connection (install/pvj-raster.socket, install/pvj-raster@.service) as its own account with no
// network, no files of the box's, a syscall filter and hard limits; the panel never decodes an upload itself.
//
//   request   "RASTER1 IMG <length>\n"        then <length> bytes: a PNG, a JPEG or an SVG
//             "RASTER1 THUMB <side> <length>\n"  then a PNG (the normalised one): the same PNG, scaled to at most <side>
//   answer    "OK <width> <height> <alpha 0|1> <length>\n" then <length> bytes of a fresh PNG
//             "ERR <words for a person>\n"
//
// What it does to an upload: sniffs the type from the first bytes (never from a name: it is given none), checks the
// size and the dimensions in the header BEFORE it decodes, decodes with libpng, libjpeg or the vendored nanosvg,
// applies a JPEG's orientation, drops every metadata chunk (a new PNG is written from pixels, so nothing else the file
// carried survives), scales down so the long side is at most 2048, and writes the PNG. An SVG is rasterised: it is
// never kept, and a file that holds a script, an entity, an external reference or text is refused in plain words.
//
// It opens no file and makes no connection. ICC profiles are not applied (pictures are taken as sRGB); 16 bit
// becomes 8 bit; an animated PNG gives its first frame.

#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <setjmp.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jerror.h>
#include <jpeglib.h>
#include <png.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <unistd.h>

#define NANOSVG_IMPLEMENTATION
#include "nanosvg.h"
#define NANOSVGRAST_IMPLEMENTATION
#include "nanosvgrast.h"

#define MAX_PICTURE_BYTES (10u * 1024u * 1024u)   // a PNG or a JPEG
#define MAX_SVG_BYTES (1u * 1024u * 1024u)
#define MAX_SIDE 8192u                            // the header's width or height
#define MAX_PIXELS 40000000u                      // and width times height (the same limits as a mask, D100)
#define OUT_SIDE 2048u                            // the long side of what is stored
#define MAX_THUMB 512u
#define MAX_SVG_POINTS 20000
#define WALL_SECONDS 15
#define SVG_SECONDS 5

typedef struct {
	uint32_t w, h;
	uint8_t *px;    // w * h * 4, R G B A, not premultiplied
} Img;

static void refuse(const char *words) {
	// the one way out for a request that cannot be served: the words are for the person who uploaded
	char line[320];
	int n = snprintf(line, sizeof line, "ERR %s\n", words);
	if (n > 0) { ssize_t r = write(1, line, (size_t)n); (void)r; }
	_exit(0);
}

static void *must(void *p) {
	if (!p) refuse("the node ran out of memory for this picture; use a smaller one");
	return p;
}

static void on_alarm(int sig) {
	(void)sig;
	static const char m[] = "ERR this picture took too long to read; use a simpler or smaller one\n";
	ssize_t r = write(1, m, sizeof m - 1);
	(void)r;
	_exit(0);
}

// ---- input ---------------------------------------------------------------------------------------------------------
static int read_full(uint8_t *buf, size_t n) {
	size_t got = 0;
	while (got < n) {
		ssize_t r = read(0, buf + got, n - got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) return 0;
		got += (size_t)r;
	}
	return 1;
}

static int read_line(char *line, size_t cap) {
	size_t n = 0;
	while (n + 1 < cap) {
		char c;
		ssize_t r = read(0, &c, 1);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) return 0;
		if (c == '\n') { line[n] = 0; return 1; }
		line[n++] = c;
	}
	return 0;
}

static int write_full(const uint8_t *buf, size_t n) {
	size_t done = 0;
	while (done < n) {
		ssize_t r = write(1, buf + done, n - done);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) return 0;
		done += (size_t)r;
	}
	return 1;
}

// ---- checks that need no decoder ------------------------------------------------------------------------------------
static void check_size(uint32_t w, uint32_t h) {
	if (w == 0 || h == 0) refuse("this picture has no size");
	if (w > MAX_SIDE || h > MAX_SIDE) refuse("this picture is larger than 8192 pixels on a side");
	if ((uint64_t)w * h > MAX_PIXELS) refuse("this picture has more than 40 million pixels");
}

static uint32_t be32(const uint8_t *p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }

// ---- PNG -----------------------------------------------------------------------------------------------------------
typedef struct { const uint8_t *p; size_t n, at; } Mem;

static void png_fail(png_structp png, png_const_charp msg) { (void)msg; longjmp(png_jmpbuf(png), 1); }
static void png_warn(png_structp png, png_const_charp msg) { (void)png; (void)msg; }
static void png_mem_read(png_structp png, png_bytep out, size_t n) {
	Mem *m = png_get_io_ptr(png);
	if (m->n - m->at < n) png_error(png, "cut short");
	memcpy(out, m->p + m->at, n);
	m->at += n;
}

static Img decode_png(const uint8_t *data, size_t len) {
	Img img = {0, 0, NULL};
	if (len < 33 || memcmp(data + 12, "IHDR", 4) != 0) refuse("this PNG is damaged");
	check_size(be32(data + 16), be32(data + 20));
	png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, NULL, png_fail, png_warn);
	png_infop info = png ? png_create_info_struct(png) : NULL;
	if (!png || !info) refuse("the node ran out of memory for this picture");
	uint8_t **volatile rows = NULL;
	uint8_t *volatile pixels = NULL;
	if (setjmp(png_jmpbuf(png))) {
		free(rows);
		free(pixels);
		png_destroy_read_struct(&png, &info, NULL);
		refuse("this PNG is damaged or cut short");
	}
	Mem m = {data, len, 0};
	png_set_read_fn(png, &m, png_mem_read);
	png_set_user_limits(png, MAX_SIDE, MAX_SIDE);
#ifdef PNG_SET_CHUNK_MALLOC_LIMIT_SUPPORTED
	png_set_chunk_malloc_max(png, 1u << 20);
#endif
	png_set_chunk_cache_max(png, 64);
	png_read_info(png, info);
	uint32_t w = png_get_image_width(png, info), h = png_get_image_height(png, info);
	check_size(w, h);
	int depth = png_get_bit_depth(png, info), type = png_get_color_type(png, info);
	if (depth == 16) png_set_strip_16(png);
	if (type == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(png);
	if ((type == PNG_COLOR_TYPE_GRAY || type == PNG_COLOR_TYPE_GRAY_ALPHA)) {
		if (depth < 8) png_set_expand_gray_1_2_4_to_8(png);
		png_set_gray_to_rgb(png);
	}
	if (png_get_valid(png, info, PNG_INFO_tRNS)) png_set_tRNS_to_alpha(png);
	png_set_filler(png, 0xff, PNG_FILLER_AFTER);
	png_set_interlace_handling(png);
	png_read_update_info(png, info);
	if (png_get_rowbytes(png, info) != (size_t)w * 4) png_error(png, "unexpected layout");
	pixels = must(malloc((size_t)w * h * 4));
	img.px = pixels;
	rows = must(malloc((size_t)h * sizeof *rows));
	for (uint32_t y = 0; y < h; y++) rows[y] = pixels + (size_t)y * w * 4;
	png_read_image(png, (png_bytepp)rows);
	png_read_end(png, NULL);       // the closing chunks too: a PNG that is cut off there is not whole
	free(rows);
	png_destroy_read_struct(&png, &info, NULL);
	img.w = w;
	img.h = h;
	return img;
}

// ---- JPEG ----------------------------------------------------------------------------------------------------------
typedef struct { struct jpeg_error_mgr pub; jmp_buf jump; } JErr;

static void jpeg_fail(j_common_ptr c) { longjmp(((JErr *)c->err)->jump, 1); }
static void jpeg_quiet(j_common_ptr c) { (void)c; }
static void jpeg_say(j_common_ptr c, int level) {
	// a picture that is cut short or has broken data would be filled up with grey: refuse it instead
	if (level < 0) {
		int code = c->err->msg_code;
		if (code == JWRN_JPEG_EOF || code == JWRN_HIT_MARKER || code == JWRN_HUFF_BAD_CODE || code == JWRN_MUST_RESYNC)
			longjmp(((JErr *)c->err)->jump, 1);
	}
}

// 1 to 8 from the Exif block in an APP1 marker, else 1
static int exif_orientation(const uint8_t *d, size_t n) {
	if (n < 14 || memcmp(d, "Exif\0\0", 6) != 0) return 1;
	const uint8_t *t = d + 6;
	size_t tn = n - 6;
	int le;
	if (t[0] == 'I' && t[1] == 'I') le = 1;
	else if (t[0] == 'M' && t[1] == 'M') le = 0;
	else return 1;
#define U16(o) (le ? (unsigned)(t[o] | (t[(o) + 1] << 8)) : (unsigned)((t[o] << 8) | t[(o) + 1]))
#define U32(o) (le ? ((uint32_t)t[o] | ((uint32_t)t[(o) + 1] << 8) | ((uint32_t)t[(o) + 2] << 16) | ((uint32_t)t[(o) + 3] << 24)) : be32(t + (o)))
	uint32_t ifd = U32(4);
	if (ifd > tn || tn - ifd < 2) return 1;
	unsigned count = U16(ifd);
	for (unsigned i = 0; i < count; i++) {
		size_t e = (size_t)ifd + 2 + (size_t)i * 12;
		if (e + 12 > tn) return 1;
		if (U16(e) == 0x0112) {
			unsigned v = U16(e + 8);
			return v >= 1 && v <= 8 ? (int)v : 1;
		}
	}
	return 1;
#undef U16
#undef U32
}

static Img decode_jpeg(const uint8_t *data, size_t len, int *orient) {
	Img img = {0, 0, NULL};
	struct jpeg_decompress_struct cinfo;
	JErr err;
	uint8_t *volatile row = NULL;
	uint8_t *volatile pixels = NULL;     // what the error path frees: set after setjmp, so not kept in a register
	cinfo.err = jpeg_std_error(&err.pub);
	err.pub.error_exit = jpeg_fail;
	err.pub.emit_message = jpeg_say;
	err.pub.output_message = jpeg_quiet;
	if (setjmp(err.jump)) {
		jpeg_destroy_decompress(&cinfo);
		free(row);
		free(pixels);
		refuse("this JPEG is damaged or cut short, or is of a kind that is not supported");
	}
	jpeg_create_decompress(&cinfo);
	jpeg_mem_src(&cinfo, data, (unsigned long)len);
	jpeg_save_markers(&cinfo, JPEG_APP0 + 1, 0xFFFF);
	jpeg_read_header(&cinfo, TRUE);
	if (cinfo.num_components == 4 || cinfo.jpeg_color_space == JCS_CMYK || cinfo.jpeg_color_space == JCS_YCCK)
		{ jpeg_destroy_decompress(&cinfo); refuse("CMYK JPEG files are not supported; save it as an RGB picture"); }
	if (cinfo.data_precision != 8) { jpeg_destroy_decompress(&cinfo); refuse("only 8-bit JPEG files are supported"); }
	check_size(cinfo.image_width, cinfo.image_height);
	*orient = 1;
	for (jpeg_saved_marker_ptr mk = cinfo.marker_list; mk; mk = mk->next)
		if (mk->marker == JPEG_APP0 + 1) { *orient = exif_orientation(mk->data, mk->data_length); break; }
	cinfo.out_color_space = JCS_RGB;
	// let the decoder drop 1/2, 1/4 or 1/8 of the size while it works when the result is still larger than we keep
	uint32_t longest = cinfo.image_width > cinfo.image_height ? cinfo.image_width : cinfo.image_height;
	cinfo.scale_num = 1;
	cinfo.scale_denom = 1;
	for (unsigned d = 8; d >= 2; d /= 2)
		if (longest / d >= OUT_SIDE) { cinfo.scale_denom = d; break; }
	jpeg_start_decompress(&cinfo);
	uint32_t w = cinfo.output_width, h = cinfo.output_height;
	check_size(w, h);
	if (cinfo.output_components != 3) longjmp(err.jump, 1);
	pixels = must(malloc((size_t)w * h * 4));
	img.px = pixels;
	row = must(malloc((size_t)w * 3));
	for (uint32_t y = 0; y < h; y++) {
		JSAMPROW rp = row;
		if (jpeg_read_scanlines(&cinfo, &rp, 1) != 1) longjmp(err.jump, 1);
		uint8_t *o = img.px + (size_t)y * w * 4;
		for (uint32_t x = 0; x < w; x++) { o[4 * x] = row[3 * x]; o[4 * x + 1] = row[3 * x + 1]; o[4 * x + 2] = row[3 * x + 2]; o[4 * x + 3] = 255; }
	}
	jpeg_finish_decompress(&cinfo);
	jpeg_destroy_decompress(&cinfo);
	free(row);
	img.w = w;
	img.h = h;
	return img;
}

// ---- SVG -----------------------------------------------------------------------------------------------------------
static const char *memfind(const char *hay, size_t n, const char *needle) {
	size_t k = strlen(needle);
	if (k == 0 || n < k) return NULL;
	for (size_t i = 0; i + k <= n; i++)
		if (hay[i] == needle[0] && memcmp(hay + i, needle, k) == 0) return hay + i;
	return NULL;
}

// The words for what an SVG holds that a rasteriser must never meet. `low` is the file in lower case.
static void check_svg_text(const char *low, size_t n) {
	if (memfind(low, n, "<!entity")) refuse("this SVG defines entities, which are not allowed");
	const char *dt = memfind(low, n, "<!doctype");
	if (dt) {
		for (const char *p = dt; p < low + n && *p != '>'; p++)
			if (*p == '[') refuse("this SVG has an internal DTD (entities), which is not allowed");
	}
	if (memfind(low, n, "<script")) refuse("this SVG contains a script, which is not allowed");
	if (memfind(low, n, "<foreignobject")) refuse("this SVG contains a foreignObject, which is not allowed");
	if (memfind(low, n, "<image")) refuse("this SVG contains an embedded or linked picture, which is not allowed");
	if (memfind(low, n, "<text")) refuse("This SVG uses text. Convert the text to outlines (paths) and try again.");
	if (memfind(low, n, "<?xml-stylesheet") || memfind(low, n, "@import"))
		refuse("this SVG loads a style sheet from elsewhere, which is not allowed");
	// every href and src must point inside the file ("#name") or hold a PNG; every url() must be a "#name"
	static const char *attrs[] = {"href", "src"};
	for (int a = 0; a < 2; a++) {
		const char *p = low, *end = low + n;
		size_t al = strlen(attrs[a]);
		while ((p = memfind(p, (size_t)(end - p), attrs[a])) != NULL) {
			const char *q = p + al;
			p = q;
			while (q < end && isspace((unsigned char)*q)) q++;
			if (q >= end || *q != '=') continue;
			q++;
			while (q < end && isspace((unsigned char)*q)) q++;
			if (q < end && (*q == '"' || *q == '\'')) q++;
			if (q >= end || *q == '#' || ((size_t)(end - q) >= 15 && memcmp(q, "data:image/png;", 15) == 0)) continue;
			refuse("this SVG refers to something outside the file, which is not allowed");
		}
	}
	const char *p = low, *end = low + n;
	while ((p = memfind(p, (size_t)(end - p), "url(")) != NULL) {
		const char *q = p + 4;
		p = q;
		while (q < end && (isspace((unsigned char)*q) || *q == '"' || *q == '\'')) q++;
		if (q >= end || *q != '#') refuse("this SVG refers to something outside the file, which is not allowed");
	}
}

static Img decode_svg(const uint8_t *data, size_t len) {
	Img img = {0, 0, NULL};
	char *text = must(malloc(len + 1));
	char *low = must(malloc(len + 1));
	memcpy(text, data, len);
	text[len] = 0;
	for (size_t k = 0; k < len; k++) {
		if (text[k] == 0) refuse("this SVG file is not text");
		low[k] = (char)tolower((unsigned char)text[k]);
	}
	low[len] = 0;
	check_svg_text(low, len);
	free(low);
	alarm(SVG_SECONDS);
	NSVGimage *svg = nsvgParse(text, "px", 96.0f);
	free(text);
	if (!svg) refuse("this SVG could not be read");
	float sw = svg->width, sh = svg->height;
	if (!(sw >= 1.0f) || !(sh >= 1.0f) || sw > 1.0e7f || sh > 1.0e7f) { nsvgDelete(svg); refuse("this SVG has no usable size"); }
	long points = 0;
	for (NSVGshape *s = svg->shapes; s; s = s->next)
		for (NSVGpath *p = s->paths; p; p = p->next) {
			points += p->npts;
			if (points > MAX_SVG_POINTS) { nsvgDelete(svg); refuse("this SVG is too complicated (more than 20000 points)"); }
		}
	float scale = (float)OUT_SIDE / (sw > sh ? sw : sh);
	uint32_t w = (uint32_t)(sw * scale + 0.5f), h = (uint32_t)(sh * scale + 0.5f);
	if (w < 1) w = 1;
	if (h < 1) h = 1;
	if (w > OUT_SIDE) w = OUT_SIDE;
	if (h > OUT_SIDE) h = OUT_SIDE;
	NSVGrasterizer *rast = nsvgCreateRasterizer();
	img.px = calloc((size_t)w * h, 4);
	if (!rast || !img.px) { nsvgDelete(svg); refuse("the node ran out of memory for this picture"); }
	nsvgRasterize(rast, svg, 0, 0, scale, img.px, (int)w, (int)h, (int)(w * 4));
	nsvgDeleteRasterizer(rast);
	nsvgDelete(svg);
	alarm(WALL_SECONDS);
	img.w = w;
	img.h = h;
	return img;
}

// ---- scale and turn ------------------------------------------------------------------------------------------------
// Scale so that the long side is at most `limit`, averaging the source pixels under each new one (alpha-weighted, so a
// transparent edge does not bleed its colour). Never makes a picture larger.
static void shrink(Img *img, uint32_t limit) {
	uint32_t longest = img->w > img->h ? img->w : img->h;
	if (longest <= limit) return;
	uint32_t dw = (uint32_t)(((uint64_t)img->w * limit + longest / 2) / longest), dh = (uint32_t)(((uint64_t)img->h * limit + longest / 2) / longest);
	if (dw < 1) dw = 1;
	if (dh < 1) dh = 1;
	uint8_t *out = must(malloc((size_t)dw * dh * 4));
	for (uint32_t y = 0; y < dh; y++) {
		uint32_t y0 = (uint32_t)((uint64_t)y * img->h / dh), y1 = (uint32_t)((uint64_t)(y + 1) * img->h / dh);
		if (y1 <= y0) y1 = y0 + 1;
		for (uint32_t x = 0; x < dw; x++) {
			uint32_t x0 = (uint32_t)((uint64_t)x * img->w / dw), x1 = (uint32_t)((uint64_t)(x + 1) * img->w / dw);
			if (x1 <= x0) x1 = x0 + 1;
			uint64_t r = 0, g = 0, b = 0, a = 0, n = 0;
			for (uint32_t sy = y0; sy < y1; sy++) {
				const uint8_t *s = img->px + ((size_t)sy * img->w + x0) * 4;
				for (uint32_t sx = x0; sx < x1; sx++, s += 4) {
					r += (uint64_t)s[0] * s[3]; g += (uint64_t)s[1] * s[3]; b += (uint64_t)s[2] * s[3]; a += s[3]; n++;
				}
			}
			uint8_t *o = out + ((size_t)y * dw + x) * 4;
			if (a == 0) { o[0] = o[1] = o[2] = o[3] = 0; }
			else { o[0] = (uint8_t)(r / a); o[1] = (uint8_t)(g / a); o[2] = (uint8_t)(b / a); o[3] = (uint8_t)((a + n / 2) / n); }
		}
	}
	free(img->px);
	img->px = out;
	img->w = dw;
	img->h = dh;
}

// Exif orientation 2 to 8 applied to the pixels
static void orient(Img *img, int o) {
	if (o < 2 || o > 8) return;
	uint32_t sw = img->w, sh = img->h, dw = o >= 5 ? sh : sw, dh = o >= 5 ? sw : sh;
	uint8_t *out = must(malloc((size_t)dw * dh * 4));
	for (uint32_t y = 0; y < dh; y++)
		for (uint32_t x = 0; x < dw; x++) {
			uint32_t sx, sy;
			switch (o) {
			case 2: sx = sw - 1 - x; sy = y; break;
			case 3: sx = sw - 1 - x; sy = sh - 1 - y; break;
			case 4: sx = x; sy = sh - 1 - y; break;
			case 5: sx = y; sy = x; break;
			case 6: sx = y; sy = sh - 1 - x; break;
			case 7: sx = sw - 1 - y; sy = sh - 1 - x; break;
			default: sx = sw - 1 - y; sy = x; break;
			}
			memcpy(out + ((size_t)y * dw + x) * 4, img->px + ((size_t)sy * sw + sx) * 4, 4);
		}
	free(img->px);
	img->px = out;
	img->w = dw;
	img->h = dh;
}

// ---- output --------------------------------------------------------------------------------------------------------
typedef struct { uint8_t *p; size_t n, cap; } Out;

static void png_mem_write(png_structp png, png_bytep d, size_t n) {
	Out *o = png_get_io_ptr(png);
	if (o->n + n > o->cap) {
		size_t cap = o->cap ? o->cap : 65536;
		while (cap < o->n + n) cap *= 2;
		if (cap > (64u << 20)) png_error(png, "too large");
		uint8_t *q = realloc(o->p, cap);
		if (!q) png_error(png, "memory");
		o->p = q;
		o->cap = cap;
	}
	memcpy(o->p + o->n, d, n);
	o->n += n;
}
static void png_flush_none(png_structp png) { (void)png; }

static int has_alpha(const Img *img) {
	for (size_t i = 3, n = (size_t)img->w * img->h * 4; i < n; i += 4)
		if (img->px[i] != 255) return 1;
	return 0;
}

static void answer(const Img *img) {
	const volatile int alpha = has_alpha(img);
	Out out = {NULL, 0, 0};
	png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, png_fail, png_warn);
	png_infop info = png ? png_create_info_struct(png) : NULL;
	if (!png || !info) refuse("the node ran out of memory for this picture");
	uint8_t *volatile rgb = NULL;
	if (setjmp(png_jmpbuf(png))) {
		free(out.p);
		free(rgb);
		png_destroy_write_struct(&png, &info);
		refuse("the picture could not be written");
	}
	png_set_write_fn(png, &out, png_mem_write, png_flush_none);
	png_set_compression_level(png, 6);
	png_set_IHDR(png, info, img->w, img->h, 8, alpha ? PNG_COLOR_TYPE_RGB_ALPHA : PNG_COLOR_TYPE_RGB, PNG_INTERLACE_NONE,
	             PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
	png_write_info(png, info);
	if (alpha) {
		for (uint32_t y = 0; y < img->h; y++) png_write_row(png, img->px + (size_t)y * img->w * 4);
	} else {
		rgb = must(malloc((size_t)img->w * 3));
		for (uint32_t y = 0; y < img->h; y++) {
			const uint8_t *s = img->px + (size_t)y * img->w * 4;
			for (uint32_t x = 0; x < img->w; x++) { rgb[3 * x] = s[4 * x]; rgb[3 * x + 1] = s[4 * x + 1]; rgb[3 * x + 2] = s[4 * x + 2]; }
			png_write_row(png, rgb);
		}
	}
	png_write_end(png, NULL);
	png_destroy_write_struct(&png, &info);
	free(rgb);
	char head[96];
	int k = snprintf(head, sizeof head, "OK %u %u %d %zu\n", img->w, img->h, alpha, out.n);
	if (!write_full((const uint8_t *)head, (size_t)k) || !write_full(out.p, out.n)) _exit(1);
	free(out.p);
}

// ---- main ----------------------------------------------------------------------------------------------------------
int main(int argc, char **argv) {
	signal(SIGPIPE, SIG_IGN);
	signal(SIGALRM, on_alarm);
	alarm(WALL_SECONDS);
	prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
	prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);
	if (argc > 1 && strcmp(argv[1], "--self-limit") == 0) {
		// run by hand or by a test (not by the unit, whose properties set these): the same ceilings
		struct rlimit as = {512u << 20, 512u << 20}, cpu = {10, 10}, core = {0, 0}, nof = {16, 16};
		setrlimit(RLIMIT_AS, &as);
		setrlimit(RLIMIT_CPU, &cpu);
		setrlimit(RLIMIT_CORE, &core);
		setrlimit(RLIMIT_NOFILE, &nof);
	}
	char line[96];
	if (!read_line(line, sizeof line)) refuse("no request");
	char mode[8] = "";
	unsigned long side = 0, length = 0;
	int ok = 0;
	if (sscanf(line, "RASTER1 %7s", mode) == 1) {
		if (strcmp(mode, "IMG") == 0) ok = sscanf(line, "RASTER1 IMG %lu", &length) == 1;
		else if (strcmp(mode, "THUMB") == 0) ok = sscanf(line, "RASTER1 THUMB %lu %lu", &side, &length) == 2 && side >= 16 && side <= MAX_THUMB;
	}
	if (!ok) refuse("the request is not understood");
	if (length < 8) refuse("this file is empty or too short to be a picture");
	if (length > MAX_PICTURE_BYTES) refuse("this file is larger than 10 MB");
	uint8_t *data = must(malloc(length + 1));
	if (!read_full(data, length)) refuse("the file was cut short");
	data[length] = 0;

	Img img;
	int turn = 1;
	int is_png = length >= 8 && memcmp(data, "\x89PNG\r\n\x1a\n", 8) == 0;
	int is_jpeg = length >= 3 && data[0] == 0xFF && data[1] == 0xD8 && data[2] == 0xFF;
	size_t b = 0;
	if (length >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF) b = 3;
	while (b < length && isspace(data[b])) b++;
	int is_svg = !is_png && !is_jpeg && b + 4 <= length && (memcmp(data + b, "<?xml", 5) == 0 || memcmp(data + b, "<svg", 4) == 0);
	if (strcmp(mode, "THUMB") == 0) {
		if (!is_png) refuse("a thumbnail is made from a stored PNG only");
		img = decode_png(data, length);
		free(data);
		shrink(&img, (uint32_t)side);
		answer(&img);
		return 0;
	}
	if (is_png) img = decode_png(data, length);
	else if (is_jpeg) img = decode_jpeg(data, length, &turn);
	else if (is_svg) {
		if (length > MAX_SVG_BYTES) refuse("an SVG file may be at most 1 MB");
		img = decode_svg(data, length);
	} else refuse("only PNG, JPEG and SVG pictures are accepted");
	free(data);
	shrink(&img, OUT_SIDE);
	orient(&img, turn);
	answer(&img);
	free(img.px);
	return 0;
}
