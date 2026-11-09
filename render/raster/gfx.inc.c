// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The graphics jobs of pvj-raster (D129, slice 2): text and logos made into one small premultiplied picture each.
// This file is part of pvj-raster.c (it is included there, so it is one program and one set of limits) and uses its
// helpers: refuse, must, decode_png, Img, Out and the PNG writer.
//
//   request   "RASTER1 GFX <paramlen> <paylen>\n"   then <paramlen> bytes of parameters, then <paylen> bytes (a stored
//             PNG for a logo; none for text)
//   answer    "OKRAW <w> <h> <missing> <len>\n" then <len> bytes of premultiplied B G R A, or
//             "OKPNG <w> <h> <missing> <len>\n" then a PNG (straight alpha) for the panel's preview
//             "ERR <words for a person>\n"
//
// The parameters are lines of "word value ...", ending with a line "TEXT" and then the text itself (UTF-8, lines
// separated by newlines). They are never trusted: every number is clamped, every colour is six hex digits, a font is
// a name of lower-case letters, digits and hyphens that must be a file of the folder given with --fonts. Text is
// drawn as it is: there is no markup. A font is read from that one folder (the bundled fonts, read-only); nothing a
// person typed or uploaded names a file.
//
// Layout is plain left to right, one glyph after another, with the font's own kerning table if it has one. A letter
// the chosen font lacks comes from the fallback font (Noto Sans); one that has none there is drawn as that font's
// box and counted in <missing>.

#include <math.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_MULTIPLE_MASTERS_H

#define GFX_MAX_PARAMS 4096u
#define GFX_MAX_TEXT 1400u            // 280 characters of up to 4 bytes, and the newlines
#define GFX_MAX_LINES 6
#define GFX_MAX_PIXELS 9000000u       // one finished picture: 36 MB of B G R A
#define GFX_MAX_FONT_BYTES (4u << 20)
#define GFX_MIN_SIZE 6.0

typedef struct { uint32_t w, h; uint8_t *p; } Pic;       // premultiplied R G B A

typedef struct {
	int logo, png;
	char font[40];
	double weight, size, leading;
	uint32_t colour;
	int align;                      // 0 left, 1 centre, 2 right
	double ol_w; uint32_t ol_c;     // outline: a share of the size, colour
	double sh_x, sh_y, sh_blur; uint32_t sh_c; int sh_a;
	double pl_pad; uint32_t pl_c; int pl_a;
	int maxw, maxh, opacity;
	int nofit;                      // refuse a text that would have to be made smaller to fit (a ticker, D129 slice 3)
	int canvas, cw, ch, cx, cy;
	double width, rotate; int mirror;
	char *text; size_t text_len;
	int have_text;
} Gfx;

/* NaN compares false with everything, so it would pass a plain min/max: it gives `lo`, on every limit and every alpha. */
static double clampd(double v, double lo, double hi) { if (v != v) return lo; return v < lo ? lo : (v > hi ? hi : v); }

static int hex6(const char *s, uint32_t *out) {
	if (strlen(s) != 6) return 0;
	uint32_t v = 0;
	for (int i = 0; i < 6; i++) {
		char c = s[i];
		int d = c >= '0' && c <= '9' ? c - '0' : (c >= 'a' && c <= 'f' ? c - 'a' + 10 : (c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1));
		if (d < 0) return 0;
		v = v * 16 + (uint32_t)d;
	}
	*out = v;
	return 1;
}

// ---- parameters ----------------------------------------------------------------------------------------------------
static void gfx_parse(char *buf, size_t n, Gfx *g) {
	memset(g, 0, sizeof *g);
	strcpy(g->font, "archivo");
	g->weight = 400; g->size = 64; g->leading = 110; g->colour = 0xffffff; g->ol_c = 0; g->sh_c = 0; g->pl_c = 0;
	g->maxw = 1920; g->maxh = 1080; g->opacity = 100;
	buf[n] = 0;
	char *p = buf, *end = buf + n;
	while (p < end) {
		char *nl = memchr(p, '\n', (size_t)(end - p));
		size_t len = nl ? (size_t)(nl - p) : (size_t)(end - p);
		char line[200];
		if (len >= sizeof line) refuse("the request is not understood");
		memcpy(line, p, len);
		line[len] = 0;
		char *next = nl ? nl + 1 : end;
		if (strcmp(line, "TEXT") == 0) {
			g->text = next;
			g->text_len = (size_t)(end - next);
			g->have_text = 1;
			return;
		}
		char w[16] = "", a[48] = "", b[24] = "", c[24] = "";
		double x1 = 0, x2 = 0, x3 = 0, x4 = 0;
		int k;
		if (sscanf(line, "%15s", w) != 1) { p = next; continue; }
		if (!strcmp(w, "kind")) { if (sscanf(line, "kind %15s", a) == 1) g->logo = !strcmp(a, "logo"); }
		else if (!strcmp(w, "out")) { if (sscanf(line, "out %15s", a) == 1) g->png = !strcmp(a, "png"); }
		else if (!strcmp(w, "font")) {
			if (sscanf(line, "font %47s", a) != 1 || strlen(a) >= sizeof g->font) refuse("that font is not on this node");
			for (const char *q = a; *q; q++)
				if (!((*q >= 'a' && *q <= 'z') || (*q >= '0' && *q <= '9') || *q == '-')) refuse("that font is not on this node");
			strcpy(g->font, a);
		}
		else if (!strcmp(w, "weight")) { if (sscanf(line, "weight %lf", &x1) == 1) g->weight = clampd(x1, 1, 1000); }
		else if (!strcmp(w, "size")) { if (sscanf(line, "size %lf", &x1) == 1) g->size = clampd(x1, 1, 4000); }
		else if (!strcmp(w, "leading")) { if (sscanf(line, "leading %lf", &x1) == 1) g->leading = clampd(x1, 80, 250); }
		else if (!strcmp(w, "colour")) { if (sscanf(line, "colour %23s", b) != 1 || !hex6(b, &g->colour)) refuse("that colour is not understood"); }
		else if (!strcmp(w, "align")) { if (sscanf(line, "align %15s", a) == 1) g->align = !strcmp(a, "center") ? 1 : (!strcmp(a, "right") ? 2 : 0); }
		else if (!strcmp(w, "outline")) {
			if (sscanf(line, "outline %lf %23s", &x1, b) != 2 || !hex6(b, &g->ol_c)) refuse("that outline is not understood");
			g->ol_w = clampd(x1, 0, 0.3);
		}
		else if (!strcmp(w, "shadow")) {
			k = sscanf(line, "shadow %lf %lf %lf %23s %lf", &x1, &x2, &x3, c, &x4);
			if (k != 5 || !hex6(c, &g->sh_c)) refuse("that shadow is not understood");
			g->sh_x = clampd(x1, -0.5, 0.5); g->sh_y = clampd(x2, -0.5, 0.5); g->sh_blur = clampd(x3, 0, 0.5); g->sh_a = (int)clampd(x4, 0, 100);
		}
		else if (!strcmp(w, "plate")) {
			k = sscanf(line, "plate %lf %23s %lf", &x1, c, &x2);
			if (k != 3 || !hex6(c, &g->pl_c)) refuse("that back plate is not understood");
			g->pl_pad = clampd(x1, 0, 2); g->pl_a = (int)clampd(x2, 0, 100);
		}
		else if (!strcmp(w, "limit")) { if (sscanf(line, "limit %lf %lf", &x1, &x2) == 2) { g->maxw = (int)clampd(x1, 16, 4096); g->maxh = (int)clampd(x2, 16, 4096); } }
		else if (!strcmp(w, "nofit")) { if (sscanf(line, "nofit %lf", &x1) == 1) g->nofit = x1 != 0; }
		else if (!strcmp(w, "opacity")) { if (sscanf(line, "opacity %lf", &x1) == 1) g->opacity = (int)clampd(x1, 0, 100); }
		else if (!strcmp(w, "canvas")) {
			if (sscanf(line, "canvas %lf %lf %lf %lf", &x1, &x2, &x3, &x4) == 4) {
				g->canvas = 1; g->cw = (int)clampd(x1, 1, 4096); g->ch = (int)clampd(x2, 1, 4096); g->cx = (int)clampd(x3, -8192, 8192); g->cy = (int)clampd(x4, -8192, 8192);
			}
		}
		else if (!strcmp(w, "width")) { if (sscanf(line, "width %lf", &x1) == 1) g->width = clampd(x1, 1, 4096); }
		else if (!strcmp(w, "rotate")) { if (sscanf(line, "rotate %lf", &x1) == 1) g->rotate = clampd(x1, -360, 360); }
		else if (!strcmp(w, "mirror")) { if (sscanf(line, "mirror %lf", &x1) == 1) g->mirror = x1 != 0; }
		p = next;
	}
}

// ---- fonts -----------------------------------------------------------------------------------------------------------
static FT_Library ft_lib;

typedef struct { FT_Face face; uint8_t *mem; } Face;

static Face face_open(const char *dir, const char *id, double weight) {
	char path[512];
	if (snprintf(path, sizeof path, "%s/%s.ttf", dir, id) >= (int)sizeof path) refuse("that font is not on this node");
	FILE *f = fopen(path, "rb");
	if (!f) refuse("that font is not on this node");
	if (fseek(f, 0, SEEK_END) != 0) { fclose(f); refuse("that font could not be read"); }
	long size = ftell(f);
	if (size < 100 || size > (long)GFX_MAX_FONT_BYTES || fseek(f, 0, SEEK_SET) != 0) { fclose(f); refuse("that font could not be read"); }
	Face out;
	out.mem = must(malloc((size_t)size));
	if (fread(out.mem, 1, (size_t)size, f) != (size_t)size) { fclose(f); refuse("that font could not be read"); }
	fclose(f);
	if (FT_New_Memory_Face(ft_lib, out.mem, size, 0, &out.face) != 0) refuse("that font could not be read");
	if (FT_HAS_MULTIPLE_MASTERS(out.face)) {
		FT_MM_Var *mm = NULL;
		if (FT_Get_MM_Var(out.face, &mm) == 0 && mm) {
			FT_Fixed coords[16];
			FT_UInt n = mm->num_axis > 16 ? 16 : mm->num_axis;
			for (FT_UInt i = 0; i < n; i++) {
				coords[i] = mm->axis[i].def;
				if (mm->axis[i].tag == FT_MAKE_TAG('w', 'g', 'h', 't')) {
					double v = clampd(weight, (double)mm->axis[i].minimum / 65536.0, (double)mm->axis[i].maximum / 65536.0);
					coords[i] = (FT_Fixed)(v * 65536.0);
				}
			}
			FT_Set_Var_Design_Coordinates(out.face, n, coords);
			FT_Done_MM_Var(ft_lib, mm);
		}
	}
	return out;
}

static void face_size(FT_Face f, double px) {
	if (FT_Set_Char_Size(f, 0, (FT_F26Dot6)(px * 64.0), 72, 72) != 0) refuse("that size cannot be drawn");
}

// ---- text layout -----------------------------------------------------------------------------------------------------
typedef struct { FT_Face face; FT_UInt gi; long adv; } Glyph;      // adv in 26.6
typedef struct { Glyph *g; int n; long width; } Line;

static uint32_t utf8_next(const uint8_t **pp, const uint8_t *end) {
	const uint8_t *p = *pp;
	uint32_t c = *p++;
	int extra = 0;
	if (c < 0x80) { *pp = p; return c; }
	if ((c & 0xE0) == 0xC0) { c &= 0x1F; extra = 1; }
	else if ((c & 0xF0) == 0xE0) { c &= 0x0F; extra = 2; }
	else if ((c & 0xF8) == 0xF0) { c &= 0x07; extra = 3; }
	else { *pp = p; return 0xFFFD; }
	for (int i = 0; i < extra; i++) {
		if (p >= end || (*p & 0xC0) != 0x80) { *pp = p; return 0xFFFD; }
		c = (c << 6) | (*p++ & 0x3F);
	}
	*pp = p;
	if (c > 0x10FFFF || (c >= 0xD800 && c < 0xE000) || (extra == 1 && c < 0x80) || (extra == 2 && c < 0x800) || (extra == 3 && c < 0x10000)) return 0xFFFD;
	return c;
}

typedef struct { Line lines[GFX_MAX_LINES]; int nlines; int missing; long asc, lh; long width; } Layout;

static void layout_free(Layout *L) {
	for (int i = 0; i < L->nlines; i++) free(L->lines[i].g);
	L->nlines = 0;
}

static void layout(Layout *L, const Gfx *g, FT_Face main, FT_Face fall, double px) {
	memset(L, 0, sizeof *L);
	face_size(main, px);
	face_size(fall, px);
	L->asc = main->size->metrics.ascender;
	L->lh = (long)((double)main->size->metrics.height * g->leading / 100.0);
	if (L->lh < 64) L->lh = 64;
	const uint8_t *p = (const uint8_t *)g->text, *end = p + g->text_len;
	size_t cap = g->text_len + 1;
	L->nlines = 1;
	L->lines[0].g = must(calloc(cap, sizeof(Glyph)));
	FT_UInt prev = 0; FT_Face prev_face = NULL;
	while (p < end) {
		uint32_t cp = utf8_next(&p, end);
		if (cp == '\n') {
			if (L->nlines >= GFX_MAX_LINES) refuse("a text may have 6 lines at most");
			L->lines[L->nlines].g = must(calloc(cap, sizeof(Glyph)));
			L->nlines++;
			prev = 0; prev_face = NULL;
			continue;
		}
		if (cp == '\t') cp = ' ';
		if (cp < 0x20 || (cp >= 0x7F && cp < 0xA0) || cp == 0x200B || cp == 0xFEFF) continue;
		FT_Face use = main;
		FT_UInt gi = FT_Get_Char_Index(main, cp);
		if (gi == 0) {
			gi = FT_Get_Char_Index(fall, cp);
			use = fall;
			if (gi == 0) L->missing++;
		}
		if (FT_Load_Glyph(use, gi, FT_LOAD_NO_HINTING | FT_LOAD_NO_BITMAP) != 0) continue;
		Line *ln = &L->lines[L->nlines - 1];
		long adv = use->glyph->advance.x;
		if (prev_face == use && prev && FT_HAS_KERNING(use)) {
			FT_Vector k;
			if (FT_Get_Kerning(use, prev, gi, FT_KERNING_UNFITTED, &k) == 0) ln->width += k.x;
		}
		ln->g[ln->n].face = use; ln->g[ln->n].gi = gi; ln->g[ln->n].adv = adv;
		ln->n++;
		ln->width += adv;
		prev = gi; prev_face = use;
	}
	for (int i = 0; i < L->nlines; i++)
		if (L->lines[i].width > L->width) L->width = L->lines[i].width;
}

// ---- pixels -----------------------------------------------------------------------------------------------------------
static void over(uint8_t *d, uint32_t rgb, uint32_t a) {
	if (!a) return;
	uint32_t r = (rgb >> 16) & 255, gg = (rgb >> 8) & 255, b = rgb & 255, inv = 255 - a;
	d[0] = (uint8_t)((r * a + 127) / 255 + (d[0] * inv + 127) / 255);
	d[1] = (uint8_t)((gg * a + 127) / 255 + (d[1] * inv + 127) / 255);
	d[2] = (uint8_t)((b * a + 127) / 255 + (d[2] * inv + 127) / 255);
	d[3] = (uint8_t)(a + (d[3] * inv + 127) / 255);
}

static void edt_line(const double *f, double *d, int n, int *v, double *z) {
	int k = 0;
	v[0] = 0; z[0] = -1e30; z[1] = 1e30;
	for (int q = 1; q < n; q++) {
		double s;
		for (;;) {
			s = ((f[q] + (double)q * q) - (f[v[k]] + (double)v[k] * v[k])) / (2.0 * q - 2.0 * v[k]);
			if (s <= z[k] && k > 0) k--; else break;
		}
		k++;
		v[k] = q; z[k] = s; z[k + 1] = 1e30;
	}
	k = 0;
	for (int q = 0; q < n; q++) {
		while (z[k + 1] < q) k++;
		d[q] = ((double)q - v[k]) * ((double)q - v[k]) + f[v[k]];
	}
}

// A copy of the coverage, grown by r pixels all round with a round edge (the distance to the nearest ink, so a corner
// stays round and a thin letter does not close up).
static uint8_t *grow(const uint8_t *cov, int w, int h, double r) {
	uint8_t *out = must(malloc((size_t)w * h));
	int m = w > h ? w : h;
	double *grid = must(malloc(sizeof(double) * (size_t)w * h));
	double *f = must(malloc(sizeof(double) * (size_t)m)), *d = must(malloc(sizeof(double) * (size_t)m));
	int *v = must(malloc(sizeof(int) * (size_t)m));
	double *z = must(malloc(sizeof(double) * ((size_t)m + 1)));
	for (int x = 0; x < w; x++) {
		for (int y = 0; y < h; y++) f[y] = cov[(size_t)y * w + x] >= 128 ? 0 : 1e12;
		edt_line(f, d, h, v, z);
		for (int y = 0; y < h; y++) grid[(size_t)y * w + x] = d[y];
	}
	for (int y = 0; y < h; y++) {
		edt_line(grid + (size_t)y * w, d, w, v, z);
		for (int x = 0; x < w; x++) {
			double dist = sqrt(d[x]);
			double a = clampd(r + 0.5 - dist, 0, 1);
			uint8_t c = cov[(size_t)y * w + x];
			uint8_t o = (uint8_t)(a * 255.0 + 0.5);
			out[(size_t)y * w + x] = o > c ? o : c;
		}
	}
	free(grid); free(f); free(d); free(v); free(z);
	return out;
}

static void box_blur(uint8_t *a, int w, int h, int r) {
	if (r < 1) return;
	uint8_t *tmp = must(malloc((size_t)w * h));
	int span = 2 * r + 1;
	for (int pass = 0; pass < 3; pass++) {
		for (int y = 0; y < h; y++) {
			const uint8_t *s = a + (size_t)y * w; uint8_t *o = tmp + (size_t)y * w;
			long sum = 0;
			for (int x = -r; x <= r; x++) sum += (x >= 0 && x < w) ? s[x] : 0;
			for (int x = 0; x < w; x++) {
				o[x] = (uint8_t)(sum / span);
				int add = x + r + 1, sub = x - r;
				sum += (add < w ? s[add] : 0) - (sub >= 0 ? s[sub] : 0);
			}
		}
		for (int x = 0; x < w; x++) {
			long sum = 0;
			for (int y = -r; y <= r; y++) sum += (y >= 0 && y < h) ? tmp[(size_t)y * w + x] : 0;
			for (int y = 0; y < h; y++) {
				a[(size_t)y * w + x] = (uint8_t)(sum / span);
				int add = y + r + 1, sub = y - r;
				sum += (add < h ? tmp[(size_t)add * w + x] : 0) - (sub >= 0 ? tmp[(size_t)sub * w + x] : 0);
			}
		}
	}
	free(tmp);
}

// Cut a picture to the box that holds every pixel that is not transparent.
static void crop_to_ink(Pic *pic) {
	uint32_t x0 = pic->w, y0 = pic->h, x1 = 0, y1 = 0;
	for (uint32_t y = 0; y < pic->h; y++)
		for (uint32_t x = 0; x < pic->w; x++)
			if (pic->p[((size_t)y * pic->w + x) * 4 + 3]) {
				if (x < x0) x0 = x;
				if (x > x1) x1 = x;
				if (y < y0) y0 = y;
				if (y > y1) y1 = y;
			}
	if (x1 < x0) { // nothing drawn at all (a text of spaces): one transparent pixel
		free(pic->p); pic->w = pic->h = 1; pic->p = must(calloc(4, 1)); return;
	}
	uint32_t nw = x1 - x0 + 1, nh = y1 - y0 + 1;
	uint8_t *np = must(malloc((size_t)nw * nh * 4));
	for (uint32_t y = 0; y < nh; y++) memcpy(np + (size_t)y * nw * 4, pic->p + ((size_t)(y + y0) * pic->w + x0) * 4, (size_t)nw * 4);
	free(pic->p); pic->p = np; pic->w = nw; pic->h = nh;
}

static void blit_cov(uint8_t *cov, int cw, int ch, const FT_Bitmap *bm, int x, int y) {
	for (unsigned row = 0; row < bm->rows; row++) {
		int dy = y + (int)row;
		if (dy < 0 || dy >= ch) continue;
		for (unsigned col = 0; col < bm->width; col++) {
			int dx = x + (int)col;
			if (dx < 0 || dx >= cw) continue;
			unsigned v = bm->buffer[row * (unsigned)bm->pitch + col] + cov[(size_t)dy * cw + dx];
			cov[(size_t)dy * cw + dx] = (uint8_t)(v > 255 ? 255 : v);
		}
	}
}

// ---- a text ------------------------------------------------------------------------------------------------------------
static Pic draw_text(const Gfx *g, const char *fonts, int *missing) {
	if (!g->have_text || g->text_len == 0 || g->text_len > GFX_MAX_TEXT) refuse("there is no text to draw, or it is too long");
	Face main = face_open(fonts, g->font, g->weight);
	Face fall = face_open(fonts, "noto-sans", g->weight);
	double px = g->size;
	Layout L;
	double M = 0;
	long tw = 0, th = 0;
	for (int attempt = 0; attempt < 4; attempt++) {
		layout(&L, g, main.face, fall.face, px);
		double pad = g->pl_a ? g->pl_pad * px : 0, ol = g->ol_w * px;
		double sh = g->sh_a ? fabs(g->sh_x) * px + g->sh_blur * px * 1.5 : 0, shy = g->sh_a ? fabs(g->sh_y) * px + g->sh_blur * px * 1.5 : 0;
		double mx = (pad > ol ? pad : ol) + sh + 2, my = (pad > ol ? pad : ol) + shy + 2;
		M = mx > my ? mx : my;
		tw = (L.width + 63) / 64 + 2;
		th = (L.lh * L.nlines + 63) / 64 + 2;
		double fw = (double)tw + 2 * M, fh = (double)th + 2 * M;
		double scale = 1.0;
		if (fw > g->maxw) scale = (double)g->maxw / fw;
		if (fh * scale > g->maxh) scale = (double)g->maxh / fh;
		if (fw * fh * scale * scale > (double)GFX_MAX_PIXELS) scale = sqrt((double)GFX_MAX_PIXELS / (fw * fh));
		if (scale >= 0.9999) break;
		if (g->nofit) refuse("Too long for this node's ticker at this size: shorten it or lower the size.");
		layout_free(&L);
		px *= scale * 0.995;
		if (px < GFX_MIN_SIZE) refuse("there is too much text to fit the screen at a size that can be read; use fewer words");
		if (attempt == 3) refuse("this text does not fit the screen; use fewer words or a smaller size");
	}
	*missing = L.missing;
	int ipad = (int)ceil(M);
	long cw = tw + 2L * ipad, ch = th + 2L * ipad;
	if (cw < 1 || ch < 1 || (uint64_t)cw * (uint64_t)ch > GFX_MAX_PIXELS) refuse("this text is too large to draw at this size; lower the size");
	uint8_t *cov = must(calloc((size_t)cw * ch, 1));
	double lh_px = (double)L.lh / 64.0, asc_px = (double)L.asc / 64.0;
	for (int li = 0; li < L.nlines; li++) {
		Line *ln = &L.lines[li];
		long start = g->align == 1 ? (L.width - ln->width) / 2 : (g->align == 2 ? L.width - ln->width : 0);
		long pen = start;
		FT_Face last = NULL; FT_UInt lastgi = 0;
		for (int i = 0; i < ln->n; i++) {
			Glyph *gl = &ln->g[i];
			if (last == gl->face && lastgi && FT_HAS_KERNING(gl->face)) {
				FT_Vector k;
				if (FT_Get_Kerning(gl->face, lastgi, gl->gi, FT_KERNING_UNFITTED, &k) == 0) pen += k.x;
			}
			if (FT_Load_Glyph(gl->face, gl->gi, FT_LOAD_NO_HINTING | FT_LOAD_NO_BITMAP | FT_LOAD_RENDER) == 0) {
				FT_GlyphSlot s = gl->face->glyph;
				int gx = ipad + 1 + (int)((pen + 32) >> 6) + s->bitmap_left;
				int gy = ipad + 1 + (int)(asc_px + li * lh_px + 0.5) - s->bitmap_top;
				if (s->bitmap.pixel_mode == FT_PIXEL_MODE_GRAY) blit_cov(cov, (int)cw, (int)ch, &s->bitmap, gx, gy);
			}
			pen += gl->adv;
			last = gl->face; lastgi = gl->gi;
		}
	}
	layout_free(&L);
	FT_Done_Face(main.face); FT_Done_Face(fall.face);
	free(main.mem); free(fall.mem);

	Pic pic = {(uint32_t)cw, (uint32_t)ch, must(calloc((size_t)cw * ch, 4))};
	if (g->pl_a) {
		long p = (long)(g->pl_pad * px + 0.5);
		long x0 = ipad + 1 - p, y0 = ipad + 1 - p, x1 = ipad + 1 + tw - 2 + p, y1 = ipad + 1 + th - 2 + p;
		for (long y = y0 < 0 ? 0 : y0; y < y1 && y < ch; y++)
			for (long x = x0 < 0 ? 0 : x0; x < x1 && x < cw; x++) over(pic.p + ((size_t)y * cw + x) * 4, g->pl_c, (uint32_t)((g->pl_a * 255 + 50) / 100));
	}
	uint8_t *shape = cov, *ring = NULL;
	if (g->ol_w > 0.0005) { ring = grow(cov, (int)cw, (int)ch, g->ol_w * px); shape = ring; }
	if (g->sh_a) {
		uint8_t *sh = must(calloc((size_t)cw * ch, 1));
		int dx = (int)lround(g->sh_x * px), dy = (int)lround(g->sh_y * px);
		for (long y = 0; y < ch; y++) {
			long sy = y - dy;
			if (sy < 0 || sy >= ch) continue;
			for (long x = 0; x < cw; x++) {
				long sx = x - dx;
				if (sx >= 0 && sx < cw) sh[(size_t)y * cw + x] = shape[(size_t)sy * cw + sx];
			}
		}
		box_blur(sh, (int)cw, (int)ch, (int)lround(g->sh_blur * px / 2.0));
		for (size_t i = 0; i < (size_t)cw * ch; i++)
			if (sh[i]) over(pic.p + i * 4, g->sh_c, (uint32_t)(sh[i] * g->sh_a / 100));
		free(sh);
	}
	if (ring) {
		for (size_t i = 0; i < (size_t)cw * ch; i++)
			if (ring[i]) over(pic.p + i * 4, g->ol_c, ring[i]);
		free(ring);
	}
	for (size_t i = 0; i < (size_t)cw * ch; i++)
		if (cov[i]) over(pic.p + i * 4, g->colour, cov[i]);
	free(cov);
	crop_to_ink(&pic);
	return pic;
}

// ---- a logo ------------------------------------------------------------------------------------------------------------
// Straight R G B A (from the PNG) to premultiplied, scaled to dw x dh: averaged over the covered pixels when it gets
// smaller, bilinear when it gets bigger.
static Pic scale_pic(const Img *img, uint32_t dw, uint32_t dh) {
	size_t n = (size_t)img->w * img->h;
	uint8_t *pm = must(malloc(n * 4));
	for (size_t i = 0; i < n; i++) {
		const uint8_t *s = img->px + i * 4;
		uint32_t a = s[3];
		pm[i * 4] = (uint8_t)((s[0] * a + 127) / 255); pm[i * 4 + 1] = (uint8_t)((s[1] * a + 127) / 255); pm[i * 4 + 2] = (uint8_t)((s[2] * a + 127) / 255); pm[i * 4 + 3] = (uint8_t)a;
	}
	Pic out = {dw, dh, must(malloc((size_t)dw * dh * 4))};
	for (uint32_t y = 0; y < dh; y++)
		for (uint32_t x = 0; x < dw; x++) {
			uint8_t *o = out.p + ((size_t)y * dw + x) * 4;
			if (dw <= img->w && dh <= img->h) {
				uint32_t x0 = (uint32_t)((uint64_t)x * img->w / dw), x1 = (uint32_t)((uint64_t)(x + 1) * img->w / dw);
				uint32_t y0 = (uint32_t)((uint64_t)y * img->h / dh), y1 = (uint32_t)((uint64_t)(y + 1) * img->h / dh);
				if (x1 <= x0) x1 = x0 + 1;
				if (y1 <= y0) y1 = y0 + 1;
				uint64_t s0 = 0, s1 = 0, s2 = 0, s3 = 0, c = 0;
				for (uint32_t sy = y0; sy < y1; sy++)
					for (uint32_t sx = x0; sx < x1; sx++) {
						const uint8_t *s = pm + ((size_t)sy * img->w + sx) * 4;
						s0 += s[0]; s1 += s[1]; s2 += s[2]; s3 += s[3]; c++;
					}
				o[0] = (uint8_t)(s0 / c); o[1] = (uint8_t)(s1 / c); o[2] = (uint8_t)(s2 / c); o[3] = (uint8_t)(s3 / c);
			} else {
				double fx = ((double)x + 0.5) * img->w / dw - 0.5, fy = ((double)y + 0.5) * img->h / dh - 0.5;
				int ix = (int)floor(fx), iy = (int)floor(fy);
				double tx = fx - ix, ty = fy - iy;
				for (int ch = 0; ch < 4; ch++) {
					double v = 0;
					for (int j = 0; j < 2; j++)
						for (int i = 0; i < 2; i++) {
							int sx = ix + i, sy = iy + j;
							sx = sx < 0 ? 0 : (sx >= (int)img->w ? (int)img->w - 1 : sx);
							sy = sy < 0 ? 0 : (sy >= (int)img->h ? (int)img->h - 1 : sy);
							v += pm[((size_t)sy * img->w + sx) * 4 + ch] * (i ? tx : 1 - tx) * (j ? ty : 1 - ty);
						}
					o[ch] = (uint8_t)(v + 0.5);
				}
			}
		}
	free(pm);
	return out;
}

static Pic draw_logo(const Gfx *g, const uint8_t *png, size_t len) {
	if (len < 8 || memcmp(png, "\x89PNG\r\n\x1a\n", 8) != 0) refuse("a logo is made from a stored PNG only");
	Img img = decode_png(png, len);
	double w = g->width > 0 ? g->width : img.w;
	double h = w * img.h / img.w;
	double rad = g->rotate * M_PI / 180.0, c = fabs(cos(rad)), s = fabs(sin(rad));
	double bw = w * c + h * s, bh = w * s + h * c;
	double fit = 1.0;
	if (bw > g->maxw) fit = (double)g->maxw / bw;
	if (bh * fit > g->maxh) fit = (double)g->maxh / bh;
	w *= fit; h *= fit;
	uint32_t dw = (uint32_t)(fit < 1.0 ? w : w + 0.5), dh = (uint32_t)(fit < 1.0 ? h : h + 0.5);      // (rounded down when it was made smaller to fit: never a pixel over the limit)
	if (dw < 1) dw = 1;
	if (dh < 1) dh = 1;
	if ((uint64_t)dw * dh > GFX_MAX_PIXELS) refuse("this logo is too large to draw at this size; lower the size");
	Pic sc = scale_pic(&img, dw, dh);
	free(img.px);
	if (g->mirror)
		for (uint32_t y = 0; y < dh; y++)
			for (uint32_t x = 0; x < dw / 2; x++) {
				uint8_t t[4], *a = sc.p + ((size_t)y * dw + x) * 4, *b = sc.p + ((size_t)y * dw + (dw - 1 - x)) * 4;
				memcpy(t, a, 4); memcpy(a, b, 4); memcpy(b, t, 4);
			}
	if (fabs(g->rotate) < 0.05) return sc;
	double ow = (double)dw * c + (double)dh * s, oh = (double)dw * s + (double)dh * c;
	uint32_t rw = (uint32_t)ceil(ow - 1e-6), rh = (uint32_t)ceil(oh - 1e-6);
	if (rw < 1) rw = 1;
	if (rh < 1) rh = 1;
	if ((uint64_t)rw * rh > GFX_MAX_PIXELS) refuse("this logo is too large to draw at this size; lower the size");
	Pic rot = {rw, rh, must(calloc((size_t)rw * rh, 4))};
	double cr = cos(rad), sr = sin(rad);
	for (uint32_t y = 0; y < rh; y++)
		for (uint32_t x = 0; x < rw; x++) {
			double dx = (double)x + 0.5 - rw / 2.0, dy = (double)y + 0.5 - rh / 2.0;
			double sx = dx * cr + dy * sr + dw / 2.0 - 0.5, sy = -dx * sr + dy * cr + dh / 2.0 - 0.5;
			int ix = (int)floor(sx), iy = (int)floor(sy);
			double tx = sx - ix, ty = sy - iy;
			uint8_t *o = rot.p + ((size_t)y * rw + x) * 4;
			for (int ch = 0; ch < 4; ch++) {
				double v = 0;
				for (int j = 0; j < 2; j++)
					for (int i = 0; i < 2; i++) {
						int px = ix + i, py = iy + j;
						if (px < 0 || py < 0 || px >= (int)dw || py >= (int)dh) continue;
						v += sc.p[((size_t)py * dw + px) * 4 + ch] * (i ? tx : 1 - tx) * (j ? ty : 1 - ty);
					}
				o[ch] = (uint8_t)(v + 0.5);
			}
		}
	free(sc.p);
	return rot;
}

// ---- the answer ----------------------------------------------------------------------------------------------------------
static void gfx_answer(const Gfx *g, Pic pic, int missing) {
	if (g->opacity < 100)
		for (size_t i = 0; i < (size_t)pic.w * pic.h * 4; i++) pic.p[i] = (uint8_t)((pic.p[i] * g->opacity + 50) / 100);
	char head[96];
	if (g->png) {
		uint32_t cw = pic.w, ch = pic.h;
		int ox = 0, oy = 0;
		if (g->canvas) { cw = (uint32_t)g->cw; ch = (uint32_t)g->ch; ox = g->cx; oy = g->cy; }
		if ((uint64_t)cw * ch > GFX_MAX_PIXELS) refuse("this preview is too large");
		Img img = {cw, ch, must(calloc((size_t)cw * ch, 4))};
		for (uint32_t y = 0; y < pic.h; y++) {
			long dy = (long)y + oy;
			if (dy < 0 || dy >= (long)ch) continue;
			for (uint32_t x = 0; x < pic.w; x++) {
				long dx = (long)x + ox;
				if (dx < 0 || dx >= (long)cw) continue;
				const uint8_t *s = pic.p + ((size_t)y * pic.w + x) * 4;
				uint8_t *d = img.px + ((size_t)dy * cw + dx) * 4;
				uint32_t a = s[3];
				if (a == 0) continue;
				d[0] = (uint8_t)(s[0] >= a ? 255 : (s[0] * 255 + a / 2) / a);
				d[1] = (uint8_t)(s[1] >= a ? 255 : (s[1] * 255 + a / 2) / a);
				d[2] = (uint8_t)(s[2] >= a ? 255 : (s[2] * 255 + a / 2) / a);
				d[3] = (uint8_t)a;
			}
		}
		int alpha = 1;
		Out out = encode_png(&img, &alpha);
		int k = snprintf(head, sizeof head, "OKPNG %u %u %d %zu\n", cw, ch, missing, out.n);
		if (!write_full((const uint8_t *)head, (size_t)k) || !write_full(out.p, out.n)) _exit(1);
		return;
	}
	for (size_t i = 0; i < (size_t)pic.w * pic.h; i++) { uint8_t t = pic.p[i * 4]; pic.p[i * 4] = pic.p[i * 4 + 2]; pic.p[i * 4 + 2] = t; }
	int k = snprintf(head, sizeof head, "OKRAW %u %u %d %zu\n", pic.w, pic.h, missing, (size_t)pic.w * pic.h * 4);
	if (!write_full((const uint8_t *)head, (size_t)k) || !write_full(pic.p, (size_t)pic.w * pic.h * 4)) _exit(1);
}

static void gfx_job(unsigned long paramlen, unsigned long paylen, const char *fonts) {
	if (paramlen < 4 || paramlen > GFX_MAX_PARAMS + GFX_MAX_TEXT || paylen > MAX_PICTURE_BYTES) refuse("the request is not understood");
	char *params = must(malloc(paramlen + 1));
	if (!read_full((uint8_t *)params, paramlen)) refuse("the request was cut short");
	uint8_t *pay = NULL;
	if (paylen) {
		pay = must(malloc(paylen));
		if (!read_full(pay, paylen)) refuse("the file was cut short");
	}
	Gfx g;
	gfx_parse(params, paramlen, &g);
	Pic pic;
	int missing = 0;
	if (g.logo) {
		if (!pay) refuse("there is no logo to draw");
		pic = draw_logo(&g, pay, paylen);
	} else {
		if (!fonts || !*fonts) refuse("this node has no fonts for text");
		if (FT_Init_FreeType(&ft_lib) != 0) refuse("the font engine did not start");
		pic = draw_text(&g, fonts, &missing);
	}
	gfx_answer(&g, pic, missing);
}
