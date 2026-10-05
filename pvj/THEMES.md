# Themes and styles (the panel's look)

The owner chooses the look under **System > Look**. It applies when tapped, is kept in the box's settings (`theme`: a name and an optional accent), and travels in a settings export. Two things make a look:

- A **theme** is a small JSON file of colours. Anyone can add one.
- A **style** is a block of the panel's own stylesheet (`pvj/web/app.css`): type, shapes, sizes, how a switch or a chip is drawn. There is a small fixed set of them, and only this project's code adds one. A theme names the style it is made for.

A theme file never carries CSS. It holds names from fixed lists and colours as `#rrggbb`, and nothing else from it reaches the page (D54).

## A theme

Built-in themes are in `pvj/themes.d`; your own go in `<state>/addons/themes` (on a box `/var/lib/pvj/addons/themes`), which updates never touch. A broken add-on theme is skipped; an add-on cannot replace a built-in one.

```json
{
  "id": "my-theme",
  "name": "My theme",
  "tokens": { "bg": "#121214", "cd": "#1c1c20", "fg": "#f2f1ec", "ln": "#4a4a54", "mu": "#a9a8a3", "ac": "#f59e0b", "on": "#1a1300" }
}
```

| Token | What it colours |
| --- | --- |
| `bg` | the page |
| `cd` | a card, a field, a sheet (the surface) |
| `fg` | text |
| `ln` | lines and borders |
| `mu` | muted text: hints, state lines |
| `ac` | the accent: what is chosen or active |
| `on` | text on the accent |

All seven are required, and no other token is allowed. Keep `fg` on `bg` and `on` on `ac` at 4.5 to 1 or better (the tests hold the built-in themes to that). The accent swatches under Look replace `ac` for the chosen theme, and `on` is then black or white, whichever reads better.

Three optional keys:

| Key | Values | What it does |
| --- | --- | --- |
| `style` | `default` or `signal` | which block of `app.css` draws the panel. Left out: `default`. A well-formed name this version does not know is not an error: the theme keeps its colours and gets the default look (so a theme written for a later version still loads). |
| `areas` | any of `room`, `shaders`, `clips`, `mix`, `system`, each `#rrggbb` | one colour per part of the panel, for a style that uses them. A theme with `areas` has no single accent, so Look hides the accent swatches for it and a stored accent is ignored. |
| `states` | any of `off`, `setup`, `active`, `problem`, `error`, each `#rrggbb` | the fills of the Off, Set up, Active and Problem chips, and the colour of an error line of text (`error` must read on `bg` and on `cd`). |

From these the box works out the rest and serves it as `/theme.css` (custom properties on `:root`, nothing else): for each area the fill `--ar-<name>`, the text that reads on it `--ar-<name>-on` (the theme's `bg` or `fg`, whichever contrasts more) and `--ar-<name>-ink` (the area colour where it reads at 4.5 to 1 as text or a thin line on both `bg` and `cd`, else `fg`); for each state `--st-<name>` and `--st-<name>-on`.

## A style

`pvj/themes.py` lists the styles (`STYLES`). The default one has no block: it is `app.css` as it always was. Every other style is one marked block at the end of `app.css` (`/* ==== STYLE: <name> ...`), and every selector in it starts with `html[data-style="<name>"]`. That attribute is how a style is switched on:

- the server writes it into the `<html>` tag when it serves the page (`styled_html` in `pvj/server.py`), from the chosen theme, so the first paint is already right and the connect screen has it too; only a name from `STYLES` is ever written;
- the panel sets it again when the look is changed under Look (`markLook` in `app.js`, which has its own list of names).

The panel also puts `data-area` on the root element: `room` on the Room screen, `clips` on Live and Media, `mix` on Mix, `system` on System and its pages, `shaders` on the Shaders and Vibes page. A style may use it; the default one does not.

**To add a style:** add its name to `STYLES` in `themes.py` and to `STYLES` in `app.js`; add its block at the end of `app.css`, every selector scoped; add a theme in `themes.d` that names it; extend the test `test_the_signal_block_of_the_stylesheet_only_ever_applies_under_its_style` to the new marker; run the browser test's style pass on it (`tests/ui/panel.test.js`, the Signal step) and add its screenshots (`tests/ui/screenshots.js`); both go through the list of screens, pages and states in `tests/ui/signal-pages.js`, which is also where a new page is added so that it is pictured and checked in every style. Fonts go in `pvj/web/fonts` as WOFF2 with their licence text beside them, declared in `REUSE.toml` and `THIRD_PARTY_LICENSES.md`; the server serves only `.woff2` files from that folder. The default look must stay as it is: nothing outside the block may change for a style.

## Signal

Two themes use the style `signal`: **Signal** (dark room) and **Signal light** (bright room). The source is direction "D. Signal" in the Figma file "nxlx.mastercontrol panel redesign" (https://www.figma.com/design/Cu6AGouBnUBMeBCIPPx02o, page "Start here", section "Style directions"). What Figma says is written down here, because nothing else holds it outside Figma.

**The idea (Figma's words).** "Bold and graphic: huge type and one strong colour per area, readable at arm's length in the dark." "The feeling: a gig poster. Big words, flat blocks of colour, no decoration." "What it costs: subtlety and space. Large type fits fewer words, long names must wrap or be shortened, and the colour blocks must stay small in the dark so they do not glare." "Dark room: a black ground, with colour only in the title block, the primary action and what is active, so the bright area stays small. Light room: an off-white ground with the same colour blocks and black type." "Square corners, 3 px rules, flat fills. Capitals for titles and buttons. State is a solid block with the word. Medium density with very large titles. No icons."

**Palette, from Figma.**

| Role | Dark room | Light room |
| --- | --- | --- |
| Background | `#0b0b0d` | `#f2f0ea` |
| Surface | `#1a1a1f` | `#ffffff` |
| Text | `#f5f5f0` | `#0b0b0d` |
| Muted text | `#b8b8c0` | `#4a4a52` |
| Accent (no area) | `#ffd60a` | `#ffd60a` |
| Off | `#3a3a42` | `#d6d6dc` |
| Set up | `#ffb020` in Figma. **Built as `#ff9500`**, see below | the same |
| Ready | `#f5f5f0` (the text colour) | `#0b0b0d` (the text colour) |
| Active | `#ffd60a` on the board; the area's colour on the screens. **Built as `#00e0ff`**, see below | the same |
| Problem, Danger | `#ff3b30` | `#ff3b30` |

"In Signal the accent changes with the area: Room `#ffd60a`, Shaders `#ff4fa3`, Clips `#3ddc97`, System `#7aa2ff`. Text on every one of them is black." (Black here is `#0b0b0d`.)

**Type, from Figma.** Archivo in three weights (Regular 400, SemiBold 600, Black 900) at width 100, and JetBrains Mono Medium 500 for numbers.

| Use | Size | Weight and case |
| --- | --- | --- |
| Screen title (phone) | 44 px, line height 1.05 | Black, capitals |
| Page title (laptop) | 30 px, in a block of the area's colour, padding 4 by 14 | Black, capitals |
| What is on screen now (laptop) | 48 px | Black, capitals |
| Row name | 20 px | Black, capitals |
| Primary action | 18 px, letter spacing 0.04 em | Black, capitals |
| Plain and danger button, slider name | 16 px, 0.04 em | Black, capitals |
| Section label, tab | 14 px, 0.04 em (tab: none) | Black, capitals |
| State chip, the switch's words | 13 px, 0.04 em | Black, capitals |
| Body | 16 px | Regular |
| Title block's line, a value | 15 px | SemiBold |
| Hint, state line | 14 px | Regular |
| A number beside a slider | 20 px | JetBrains Mono Medium |
| Other numbers | 16 px (12 px for a controller's control name, capitals) | JetBrains Mono Medium |

**Parts, from Figma.** Primary action: a block of the area's colour, 56 px high, 14 px side padding. Plain button: a 3 px outline in the text colour, 44 px high. Danger button: a `#ff3b30` block with black words. Switch: two halves of 48 by 44 px with 3 px outlines, saying OFF and ON; the half in force is filled (ON with the area's colour and black words, OFF with the text colour and the page colour's words), the other half has muted words. State chip: a solid block, padding 4 by 8, with the word. List row: a 3 px rule above, 12 px above and below, at least 56 px high, the name and one muted line, the chip or the control at the right. Slider: the name and the value on one line, then a bar 28 px high with a 3 px outline, filled with the area's colour up to the value. Tab bar: a 3 px rule across the top, five tabs 56 px high, the open one a block of its area's colour. Title block on a phone: the full width in the area's colour, padding 20, 16, 16, with the title and one line under it. On a laptop: panels with a 3 px outline and 16 px padding, 12 px apart.

**The screens Figma shows:** Room on a phone (dark and light), the System index on a phone (dark), Shaders and Vibes on a laptop (dark).

### What was decided here, not in Figma

- **Mix is violet, `#b78cff`.** Figma names four areas and Mix is not one of them. Violet is the strong colour furthest from the four and from the state colours (amber, red). It is one line in each theme file.
- **Active is cyan, `#00e0ff`, everywhere. For the owner to confirm.** Figma's board gives Active the fixed yellow `#ffd60a`, which is also Room's colour, and its Shaders screen draws Active in pink, the colour of that area. Either way a state would look like an area. The rule is that a state is never confused with an area, so Active needed a colour that is none of the five area colours, not amber and not red. White is Ready and grey is Off; of the hues left, cyan is the furthest from all of them (black on it is 12.27 to 1; in RGB it is 121 from the clips green, 137 from the System blue and over 200 from the rest). It is one line in each theme file (`states.active`). A test holds the theme files to this: Active at least 100 from every area and every other state, and the browser test fails if any state chip on a screen has the open area's colour.
- **Set up is orange, `#ff9500`, not Figma's amber. For the owner to confirm (D57).** Figma's `#ffb020` and Room yellow `#ffd60a` are 44 apart in RGB, the nearest state to any area. Looked at side by side as blocks on the Room screen (a group that is warming up wears the Set up colour under a title block and labels in yellow) and on the System index, they read as two shades of one colour. `#ff9500` is 66 from Room yellow and 102 from Problem red, clear of both (a little further from yellow would bring it under 100 from red); black on it is 8.94 to 1. A test holds it there: Set up at least 60 from every area, every state at least 100 from every other.
- **Errors are never the accent.** The default look writes an error line in the accent. In Signal that would make a Room error yellow, the colour of Active, so an error line is `#ff3b30` in the dark and `#b00020` in the light (red at 4.5 to 1 on the off-white page; Figma's `#ff3b30` is 3.1 to 1 there).
- **Lines and focus rings in the light.** An area colour on the off-white page is between 1.2 and 2.7 to 1, too faint for a ring or a thin line. So `--ink` is the area colour in the dark and the text colour in the light, and rings, the stripe beside a chosen row and dashed outlines use it. In the light the area colour is only ever a filled block with black words on it.
- **The Off chip's words** are the text colour (`#f5f5f0`, 10.3 to 1 on `#3a3a42`); Figma has `#e0e0e6`.
- **A page under System** (which Figma does not draw on a phone) has its title at 30 px in a block of the area's colour beside the page's switch, and wraps: "Shaders and Vibes", "People and codes" and "Projection mapping" are two lines at 390 px.
- **Every main screen is titled with its area's name, with the panel's name small under it** (Figma's title block has such a line). The Live screen's heading is "nxlx.mastercontrol" in the default look; in Signal the style writes LIVE before it at title size and the heading's own words become the small line. Room, Mix, Media and System get the same small line after their title. The words "Live" and "nxlx.mastercontrol" are in the stylesheet for this, so if the Live screen's heading or the product's name changes, the Signal block must follow.
- **The slider's touch target** is 44 px high; the bar drawn in it is Figma's 28 px. Its fill is the track's background, cut at `--fill`, which the panel sets on every slider from its value (`fillRanges` in `app.js`, on each input and four times a second, because the box moves sliders too). That is a track, a gradient and a thumb, with the `-webkit-` and `-moz-` names for the same parts, so nothing in it is special to one browser; it has been run in Chromium only (CI). The default look keeps the browser's own slider.
- **Live, Mix, Media, the MIDI controller page, fields, sheets, the pads and the questions asked in place** are not in Figma; they are built from the parts above. On the controller page a knob keeps its round outline: the shape is what tells a knob from a button; a cell is 88 px wide at least (58 in the default look) and in sentence case, so no word of what it does is cut, and a wide controller scrolls sideways inside its card.
- **A card on a phone has no box** (Figma's phone screens have none: a label, then what belongs to it); on a laptop it is a panel with the 3 px outline.
- **Names typed by people stay as typed** where the letters matter: fields, addresses, Wi-Fi names in the list of networks, codes. A row's name is in capitals, as Figma draws "MAIN PROJECTOR" and "AURORA".

### What was decided when the look was carried through every page (D57)

Figma draws three screens. Every other screen, page and state was looked at in the pictures of `tests/ui/signal-pages.js` and settled by these rules, which the browser test holds on each of them (at 390 and 1366 px, dark and light):

- **Archivo Black in capitals is for screen titles, a panel's own big name and the largest actions only** (the owner, after seeing the first pass: "feel free to use the lighter font style on smaller button"). That is: the title block, a page's title, a group's name on Room, the shader on stage, and the buttons that are 56 px and up (`.btn.big`, a scene, a source, a projector's power button, Pair and Join). Everything smaller is SemiBold or Bold in sentence case: ordinary buttons, the tabs (the open one Black), chips, the switch's Off and On, section headings (Bold 17 px), row names (Bold 18 px), field labels (SemiBold 14 px), slider names. This replaces Figma's "capitals for titles and buttons" for the small ones; Figma's sizes in the table above still hold for what stays in capitals. Never in capitals, whatever the size: a sentence, the name of a clip or a file, a controller's own name.
- **Numbers are in the number face:** slider values, clock times, sizes, addresses, codes, number and time fields.
- **A title is never cut inside a word.** Titles and names break between words only; a page under System has its title at 28 px on a phone (30 on a laptop), so "Projectors" and its switch share a line, and where a title needs the whole width the switch goes under it.
- **What staff press on Room and on Live is 56 px high at least;** everything else that can be tapped is 44. A word on a button is never cut, in any case.
- **What destroys something is the danger red, never an area's colour.** A block for what cannot be undone or stops the room (All off, Power off, Reset to factory settings, the "yes" of every question asked in place); a red outline for what removes or deletes one thing, and for Try again on a projector that does not answer. The factory reset is a panel with a red outline under a red "Danger" label.
- **A state is a chip in its own colour with its word, also where the default look has only a line of text.** A projector's row has one beside its name (On in the Active colour, Off, Warming up and Cooling down in the Set up colour, No answer in the Problem colour); a group on Room has one beside its name, in the same colours. The row's own text line still says it for a screen reader.
- **The projector's power button looks different in each state:** Turn on is the block of the area's colour, Turn off an outline (it asks first), Warming up and Cooling down a block of the Set up colour that cannot be pressed, Try again an outline in the danger red. 56 px high.
- **Room is read at arm's length.** A group is a ruled panel: its name at 30 px, the state chip, then On and Off as the two halves of one switch 64 px high (the half in force is filled, On with the screen's colour, as on every switch in the look), then "Source" and its buttons at 64 px with the chosen one filled, then the two mutes, in the lighter type and with a muted outline, then what the last press did in a ruled line on the surface colour. Scenes are blocks 72 px high. "Everything" stands apart under a double rule, with All off in the danger red and its question in place.
- **Ambience on Room is Room yellow, not Shaders pink.** It starts shaders, but the area's colour belongs to the screen: a second area's colour on Room would say "this part is another place", and staff do not need to know what ambience is made of. So the block's label and its button when it plays are the screen's colour, like everything else that is chosen or on there. The same button on Live (Vibes) is green there, and on the Shaders page pink.
- **A small set of choices is a segmented control:** one ruled frame on the surface colour, the chosen cell a block of the area's colour (banks on Live, the days of a schedule entry, how the network gets its address, the looks, a controller's brightness, the effects by work). Secondary actions under More and a group's projectors are grids of equal cells, not a ragged line; a field and its button share a line.
- **A slider's value is a block of its own** beside the name (the text colour, the page colour's digits in the number face), so the number is found without reading the line.
- **What an action did is a toast:** the page's message line is fixed above the tab bar, where the thumb is, as a block of the text colour (the danger red for an error), and goes by itself after 8 seconds (20 for an error). Results that belong to one control are still said beside it. The animation is `@keyframes signal-toast`, the one rule in the block that is not a scoped selector (only scoped rules use it; the unit test allows an animation named for the style).
- **On a phone every section starts under a 3 px rule** with its heading, so a long screen (Live, Mix) reads as parts; on a laptop the sections are the ruled panels.
- **On a laptop a list beside its form does not scroll by itself:** rows are taller than in the default look and a 768 px window showed one and a half of them; the page scrolls and the form stays in view. Room puts ambience beside the scenes and the groups in two or three columns.
- **Before pairing** the connect screen and the support sign-in have the same title block, in the theme's accent (no area is open yet), and on a laptop the form is a column 560 px wide.

### Contrast, computed

`tests/test_modules_themes.py` computes these from the theme files and fails under 4.5 to 1.

| Pairing | Dark room | Light room |
| --- | --- | --- |
| Text on the page / on a surface | 17.98 / 15.85 | 17.26 / 19.66 |
| Muted text on the page / on a surface | 9.98 / 8.80 | 7.70 / 8.78 |
| Black on Room yellow | 13.93 | 13.93 |
| Black on Shaders pink | 6.46 | 6.46 |
| Black on Clips green | 11.13 | 11.13 |
| Black on Mix violet | 7.69 | 7.69 |
| Black on System blue | 7.90 | 7.90 |
| Words on the Off chip | 10.30 | 13.59 |
| Black on Set up orange | 8.94 | 8.94 |
| Black on Active cyan | 12.27 | 12.27 |
| Black on Problem red | 5.54 | 5.54 |
| Ready chip (page colour on text colour) | 17.98 | 17.26 |
| An error line on the page / on a surface | 5.54 / 4.89 | 6.43 / 7.33 |
| Ring and thin line (`--ink`) on the page, the faintest area | 6.46 (pink) | 17.26 (the text colour) |

### Fonts

`pvj/web/fonts/archivo-latin.06fa7831.woff2` (27 KB, weights 400 to 900 in one file) and `jetbrains-mono-500-latin.6c95bc2f.woff2` (8 KB), with each family's `OFL.txt`. The eight digits in each name are the start of the file's SHA-256: a browser may keep a font for a day, so a font that is rebuilt must get a new name (a test compares the name with the checksum). Both are SIL Open Font License 1.1 and are subsets; `THIRD_PARTY_LICENSES.md` says where they came from and how they were made. The box serves them itself (`/fonts/...`, `font-src 'self'`), they are declared with `font-display: swap`, and behind them is the system font, so the panel can be used before a font has loaded. The default look never asks for them.
