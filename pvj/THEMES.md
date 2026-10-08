# Themes and styles (the panel's look)

The owner chooses the look under **Setup > Look**. It applies when tapped, is kept in the box's settings (`theme`: a name and an optional accent), and travels in a settings export. Two things make a look:

- A **theme** is a small JSON file of colours and design tokens. Anyone can make one, add it through the panel, and carry it to another box.
- A **style** is a block of the panel's own stylesheet (`pvj/web/app.css`): how a switch, a chip, a row or a title block is built. There is a small fixed set of them, and only this project's code adds one. A theme names the style it is made for.

A theme file never carries CSS. It holds names from fixed lists, colours as `#rrggbb` and whole numbers within fixed ranges, and nothing else from it reaches the page (D54, D60).

## Make your own theme

**The short way.** Open Setup > Look, tap **Save this look as a file** while the look you want to start from is on (Signal is a good start: the file then lists every token), change the file, and tap **Add a theme**. It appears among the looks with a small "yours"; tap it to use it. Add the same file again after a change and it replaces the one before. **Remove** takes it off the box (if it is the look in use, the panel goes back to Dark stage first). At most 16 added themes; a file is at most 16 KB.

**To another box.** Either add the same file there, or export the settings (Setup > Backup and reset): a settings file from a box with added themes carries them, and importing it brings them and the look in use. A factory reset removes added themes.

**From Figma.** Three ways to get the values out of the Figma file "nxlx.mastercontrol Signal UI kit" (its variable collection "Signal theme"):

1. *Ask Claude.* A Claude session with the Figma connector can read the variables of the file and write the theme file; say which file and what the theme should be called.
2. *A variables-export plugin.* Export the collection as JSON (the W3C design-tokens shape, any plugin's nested JSON with `$value` or `value`, or a list of named values) and run `tools/figma-theme.py export.json --id my-look --name "My look" -o my-look.json` on a computer that has this repository. It prints what it took, the contrast of every pairing and any name it did not know, and writes the file only if the box would accept it.
3. *By hand.* Copy `tools/theme-samples/starter-flat.json`, type the dozen values from Figma into it, and run the same command on it.

Then add the file under Setup > Look. The Figma plan in use allows one mode per collection, so an export holds one theme at a time; if a file ever holds several modes, `--mode "Signal dark"` chooses one.

### The file

```json
{
  "id": "soft-room",
  "name": "Soft room",
  "style": "signal",
  "tokens": { "bg": "#101418", "cd": "#1b2229", "fg": "#eef2f5", "ln": "#5b6772", "mu": "#a9b4be", "ac": "#ffd166", "on": "#101418" },
  "areas": { "room": "#ffd166", "shaders": "#f58ad2", "clips": "#7fd6a4", "mix": "#c4a7ff", "system": "#8fb8ff" },
  "states": { "off": "#37424d", "setup": "#ff8a1f", "active": "#22d3ee", "problem": "#ff3d55", "error": "#ff6b78" },
  "design": { "radius_control": 12, "radius_panel": 18, "border_width": 2, "density": "roomy", "control_height": 48, "control_height_large": 60,
              "title_case": "sentence", "title_weight": 800, "tabs": "outlined" }
}
```

(That is `tools/theme-samples/soft-room.json` without the design tokens it leaves at Signal's own value: a rounded, roomy, sentence-case variant of Signal with its own colours, kept in the repository to show the range. A test holds it valid.)

`id` is small letters, digits and hyphens (2 to 41 characters, a letter first) and may not be the id of a look that comes with the box. `name` is what the Look page shows: letters, digits, spaces, dots and hyphens, at most 40. No other key is allowed at the top than the seven shown.

**`tokens`**, all seven required:

| Token | Name in Figma | What it colours |
| --- | --- | --- |
| `bg` | `colour/page` | the page |
| `cd` | `colour/surface` | a card, a field, a sheet (the surface) |
| `fg` | `colour/text` | text |
| `ln` | `colour/line` | lines and borders (the default look; Signal draws its rules in the text colour) |
| `mu` | `colour/muted` | muted text: hints, state lines |
| `ac` | `colour/accent` | the accent: what is chosen or active. In a theme with `areas` it shows only before a screen is open (the pairing screen) |
| `on` | `colour/on colour` | text on the accent |

**`style`**: `default` or `signal`. Left out: `default`. A well-formed name this version does not know is not an error: the theme keeps its colours and gets the default look (so a theme written for a later version still loads).

**`areas`**, any of `room`, `shaders`, `clips`, `mix`, `system` (`area/room`, `area/shaders`, `area/live`, `area/mix`, `area/system` in Figma; Figma's "live" is `clips` here): one colour per part of the panel, for a style that uses them. A theme with `areas` has no single accent, so Look hides the accent swatches for it and a stored accent is ignored.

**`states`**, any of `off`, `setup`, `active`, `problem`, `error` (`state/off`, `state/setup`, `state/active`, `state/problem`, `state/error text`): the fills of the Off, Set up, Active and Problem chips, and the colour of an error line of text. Ready has no colour of its own: it is always the text colour (`state/ready` in Figma follows `colour/text`).

**`design`**, any of these; each one left out keeps the style's own value. They act in the style `signal` only (the default look is not built on them, and a theme without that style is told so when it is added):

| Token | Values | Signal's own | Name in Figma | What it does |
| --- | --- | --- | --- | --- |
| `radius_control` | 0 to 24 | 0 | `shape/corner` (one value for both) | corners of buttons, fields, chips, pads, the halves of a switch, a slider's bar |
| `radius_panel` | 0 to 24 | 0 | `shape/corner` | corners of panels, sheets, questions asked in place, pictures |
| `border_width` | 0 to 4 | 3 | `shape/rule` | every outline and rule. At 0 a plain button stands on the surface colour instead of an outline |
| `density` | `compact`, `regular`, `roomy` | `regular` | none (an option of the converter) | one scale (0.75, 1, 1.3) for the gaps between parts and the padding of rows and panels |
| `control_height` | 44 to 72 | 44 | `size/control` | height of an ordinary control. Never under 44 px: a finger needs that |
| `control_height_large` | 56 to 72 | 56 | `size/control big` | height of what staff press on Room and Live, the tabs and the largest actions. Never under 56 (D57), never under `control_height`. Room's On, Off and sources are 8 px more, scenes 16 px more |
| `title_case` | `capitals`, `sentence` | `capitals` | none (capitals are on Figma's text styles; an option of the converter) | screen titles, a group's name, the shader on stage and the 56 px actions |
| `title_weight` | 400, 500, 600, 700, 800, 900 | 900 | none (option) | the weight of those same titles. These are the weights the shipped Archivo carries |
| `text_weight` | 400, 500, 600 | 400 | none (option) | the weight of running text and of what is typed in a field |
| `font_title`, `font_text` | `archivo`, `system` | `archivo` | `font/words` (one value for both) | the typeface of titles, and of everything else that is words |
| `font_number` | `jetbrains-mono`, `archivo`, `system` | `jetbrains-mono` | `font/numbers` | the typeface of numbers, times, addresses and codes |
| `tabs` | `filled`, `outlined` | `filled` | none (option) | the open tab: a block of the area's colour, or the page colour with a bar in the line colour |
| `primary` | `filled`, `outlined` | `filled` | none (option) | Save, Add, Play and the like: a block of the area's colour, or an outline with a bar. Outlined needs a border width of 1 or more |

The fonts are a fixed list: what is shipped on the box (Archivo and JetBrains Mono, see Fonts below) and `system`, the device's own. No family was added for this: a new one means fetching it, cutting a Latin subset and declaring its licence, which is a job of its own; the list is where it would be added (`TEXT_FONTS` and `NUMBER_FONTS` in `pvj/themes.py`, the `@font-face` rules and `LOOK_FONTS` in `app.js`).

### What the box checks, and refuses

Every theme goes through `pvj/themes.py` (`checked`), whichever way it arrives: added on the Look page, inside a settings file, or put into the add-on folder by hand. One rule set, one place.

- The file is at most 16 KB of UTF-8, one JSON object, **no key twice**, whole numbers only.
- Only the known keys; ids, names and colours by a whole match; numbers within their ranges; words from their lists.
- **Contrast.** The box computes every pairing of text and ground the look draws and refuses a theme where one is under 4.5 to 1, naming it: "Text on the Room colour is 2.1 to 1; it needs 4.5". The pairings: text and muted text on the page and on a surface; text on the accent; for each area and each state colour, the page colour or the text colour on it, whichever reads better (that is what the panel uses); an error line on the page and on a surface; and, for a theme of the default style without areas, the accent as text on the page and on a surface (the default look writes in the accent).
- **An accent is held to the same rule.** An accent chosen under Look replaces a theme's own, so `POST /api/theme` and a settings import refuse one whose pairings fall under 4.5 to 1 ("This accent cannot be used with Light: The accent as text on the page is 1.1 to 1; it needs 4.5"), and the Look page offers only the swatches that pass for the look in use. An accent already in the settings that fails (saved before this rule) is not used: the theme's own is, and the Look page says so.
- A name that is the name of a look that comes with the box (in any case of letters) is refused: two looks called Signal could not be told apart.
- **A warning, not a refusal**, when a state colour is within 60 (in RGB) of an area colour: the theme is added and the Look page says which two are close. A state must not read as a part of the panel (D54).

The converter prints the same report before anything reaches a box.

### What a theme cannot change, and why

Layout and structure are built in code: which screens exist, what is on them and in what order, where the tab bar is, what a switch or a row is made of, which words are used, how a page behaves on a phone and on a laptop. A new layout is designed (in Figma) and then built by hand in `app.js` and `app.css`, where it is tested on every screen. A theme also cannot bring a font, an image, an icon or any CSS: a file that could would let whoever makes it, or a settings file from elsewhere, put content into the panel that every paired device loads. Sizes have floors (44 px, and 56 px for what staff press) because a theme must not be able to make the room controls hard to hit. Colours are held to 4.5 to 1 for the same reason.

### Names the converter knows

`tools/figma-theme.py` matches names without regard to letter case, `colour` or `color`, spaces, hyphens or underscores, and `/` or `.`; collections and modes above the name are ignored. So `Colour / On colour`, `color/on-color` and `var(--on)` are one name.

| Goes to | Names in the Figma file | Also accepted |
| --- | --- | --- |
| `tokens.bg`, `cd`, `fg`, `mu`, `ln`, `ac`, `on` | `colour/page`, `colour/surface`, `colour/text`, `colour/muted`, `colour/line`, `colour/accent`, `colour/on colour` | `color/...`, `color/on-accent`, and the code names `bg`, `--bg`, `var(--bg)` and so on |
| `areas.room`, `shaders`, `clips`, `mix`, `system` | `area/room`, `area/shaders`, `area/live`, `area/mix`, `area/system` | `area/clips`, `ar-room`, `var(--ar-room)` and so on |
| `states.off`, `setup`, `active`, `problem`, `error` | `state/off`, `state/setup`, `state/active`, `state/problem`, `state/error text` | `state/error`, `st-off`, `var(--st-setup)` and so on; `colour/danger` fills in for `state/problem` when that is not given |
| `design.radius_control`, `radius_panel` | `shape/corner` (both) | `radius/control`, `radius/panel` (each wins over `shape/corner`) |
| `design.border_width` | `shape/rule` | `border/width` |
| `design.control_height`, `control_height_large` | `size/control`, `size/control big` | `size/control-large` |
| `design.font_title`, `font_text` | `font/words` (both) | `font/title`, `font/text` |
| `design.font_number` | `font/numbers` | `font/number` |
| `design.title_case`, `title_weight`, `text_weight`, `density`, `tabs`, `primary` | not in the file | `type/title-case`, `type/title-weight`, `type/text-weight`, `space/density`, `style/tabs`, `style/primary`; or the options `--title-case`, `--title-weight`, `--text-weight`, `--density`, `--tabs`, `--primary` |

`state/ready` is noted and not used (Ready is the text colour). Passed over without a word, because the panel has no token for them: `space/4` to `space/24`, `size/control room`, `size/scene`, `size/slider bar` and the type sizes under `type/`. Any other name is listed at the end and changes nothing. A colour may be `#rrggbb`, `#rgb`, `#rrggbbff`, `rgb(r, g, b)` or Figma's `{r, g, b, a}` with parts from 0 to 1 (a colour with transparency is refused); a number may be `8`, `8.0` or `"8px"`; an alias such as `{colour.text}` is followed. What the export does not give comes from `--base` (Signal unless said otherwise): with no line colour, lines take the text colour; with no accent, the accent takes the Room colour; with no colour for text on the accent, the box's own choice; with no error colour, the Problem colour if it reads on the page.

## Where themes live

Built-in themes are in `pvj/themes.d`. The owner's are in `<state>/addons/themes` (on a box `/var/lib/pvj/addons/themes`), which updates never touch; the panel writes a theme there as `<id>.json`, whole, beside its place and then renamed into it, and never reads or writes through a symbolic link. A file put there by hand is read the same way at the next start and held to the same checks; one that fails is skipped, and no more than 16 are taken. A file that is not used is never silent: one line in the log (`pvj-web: theme file <name> was not used: <why>`) and a line on the Look page ("One theme file on this box could not be used: <file>: <why>"). Two files with one id are one theme: the file the panel wrote (`<id>.json`) is used, else the first by name; the others are reported, and all of them go when the theme is removed or replaced. An added theme cannot replace a built-in one. If the theme in use is damaged or gone, the panel is drawn in the look the box came with (Dark stage) until it is back; the page is served before anyone has paired, so this is never an error.

Over the API (full access; adding and removing are refused through the remote-support tunnel, because they write a file on the box): `POST /api/theme/add` with `{"file": "<the text of the theme file>"}`, `POST /api/theme/remove` with `{"id": ...}`, `POST /api/theme/export` with `{}` or `{"id": ...}`. Neither is done while a settings import or a factory reset runs. `GET /api/theme` gives every paired device the names and styles of the looks; each look's tokens (for its picture), the files that were not used and a dropped accent go to full access only. A look that comes with the box is saved under an id of its own (`my-signal`, "My Signal"), because its own id is never accepted back.

From these the box works out the rest and serves it as `/theme.css` (custom properties on `:root`, nothing else): the seven tokens; for each area the fill `--ar-<name>`, the text that reads on it `--ar-<name>-on` (the theme's `bg` or `fg`, whichever contrasts more) and `--ar-<name>-ink` (the area colour where it reads at 4.5 to 1 as text or a thin line on both `bg` and `cd`, else `fg`); for each state `--st-<name>` and `--st-<name>-on`; and for each design token the theme sets, `--tk-*` variables whose values are numbers the box formatted or strings from its own tables (a font stack, `uppercase`, `var(--ink)`). The Signal block of `app.css` reads each as `var(--tk-..., <Signal's own value>)`, so a theme that sets none is drawn exactly as Signal always was; a test holds the two lists (what the box can write, what the stylesheet reads) equal, and the fallbacks equal to the defaults in `themes.py`.

The Look page draws each look as a small picture from its own tokens (page, surface, the area colours, a title in its font and case, a button and a chip). Those pictures use the shipped fonts, so the Look page is the one place where the default look asks the box for them.

## A style

`pvj/themes.py` lists the styles (`STYLES`). The default one has no block: it is `app.css` as it always was. Every other style is one marked block at the end of `app.css` (`/* ==== STYLE: <name> ...`), and every selector in it starts with `html[data-style="<name>"]`. That attribute is how a style is switched on:

- the server writes it into the `<html>` tag when it serves the page (`styled_html` in `pvj/server.py`), from the chosen theme, so the first paint is already right and the connect screen has it too; only a name from `STYLES` is ever written;
- the panel sets it again when the look is changed under Look (`markLook` in `app.js`, which has its own list of names).

The panel also puts `data-area` on the root element, one of five names a theme can colour. Since the Workspace shell (D65) they belong to its four areas: `clips` on Play (Pads, Library), `mix` on Shape (Controls, Effect, Picture, Mapping, Sound), `room` on Room, `system` on Setup and its pages, and `shaders` on the one screen Play > Shaders, which keeps its own colour. A style may use it; the default one does not.

**To add a style:** add its name to `STYLES` in `themes.py` and to `STYLES` in `app.js`; add its block at the end of `app.css`, every selector scoped; add a theme in `themes.d` that names it; extend the test `test_the_signal_block_of_the_stylesheet_only_ever_applies_under_its_style` to the new marker; run the browser test's style pass on it (`tests/ui/panel.test.js`, the Signal step) and add its screenshots (`tests/ui/screenshots.js`); both go through the list of screens, pages and states in `tests/ui/signal-pages.js`, which is also where a new page is added so that it is pictured and checked in every style. Fonts go in `pvj/web/fonts` as WOFF2 with their licence text beside them, declared in `REUSE.toml` and `THIRD_PARTY_LICENSES.md`; the server serves only `.woff2` files from that folder. Where the block writes a value a theme may set (a rule's width, a corner, a control's height, a gap, the case, weight or face of a title), write it as `var(--tk-..., <the value>)`, as the Signal block does. The default look must stay as it is: nothing outside the block may change for a style.

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
- (Before the Workspace shell; see "The Workspace shell in Signal" below for what holds now.) **Every main screen is titled with its area's name, with the panel's name small under it** (Figma's title block has such a line). The Live screen's heading is "nxlx.mastercontrol" in the default look; in Signal the style writes LIVE before it at title size and the heading's own words become the small line. Room, Mix, Media and System get the same small line after their title. The words "Live" and "nxlx.mastercontrol" are in the stylesheet for this, so if the Live screen's heading or the product's name changes, the Signal block must follow.
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

`pvj/web/fonts/archivo-latin.06fa7831.woff2` (27 KB, weights 400 to 900 in one file) and `jetbrains-mono-500-latin.6c95bc2f.woff2` (8 KB), with each family's `OFL.txt`. The eight digits in each name are the start of the file's SHA-256: a browser may keep a font for a day, so a font that is rebuilt must get a new name (a test compares the name with the checksum). Both are SIL Open Font License 1.1 and are subsets; `THIRD_PARTY_LICENSES.md` says where they came from and how they were made. The box serves them itself (`/fonts/...`, `font-src 'self'`), they are declared with `font-display: swap`, and behind them is the system font, so the panel can be used before a font has loaded. The default look never asks for them, except on the Look page, whose pictures of the looks are drawn in each look's own type (D60).

### The Workspace shell in Signal (D65)

The panel's layout changed (four areas, a side menu from 600 px, one transport strip); the look did not. What that means for the rules above:

- **The title block is the shell's title bar** (`#wshead`), a block of the area's colour across the top of every screen: the area's name (PLAY, SHAPE, ROOM, SETUP) in the display face, 34 to 44 px on a phone and 26 px from 600 px wide, and under it one line in 15 px that says the open screen and its job ("Pads: Start what sits on a pad."). The words are real text now: the stylesheet no longer writes "Live" or the panel's name through `content`. The panel's name is in the bar from 600 px wide (in the number face) and not on a phone.
- **A page keeps its own title** (Setup's pages, and Shaders, Mapping and Sound for those who have the page): the 28 to 30 px block beside its switch, as before, under the bar. The bar's title is then not a heading, so a screen has one first heading.
- **What is open is a block of the area's colour**: the area's tab under 600 px, the screen in the row under the title, and the screen's item in the side menu. The side menu's area names are capitals over a rule in the text colour (in the ink colour for the open area); its items are 44 px high.
- **The transport strip**: the four buttons that are always there (Previous, Next, Stop, Blackout) are 56 px (`--tk-chl`), the others 44 px (`--tk-ch`); on a phone all of the first row is 56. Blackout has a rule in the Problem colour and is a block of it while the screen is black.
- **The message toast** sits above the strip (the panel measures the strip and the tabs and gives the stylesheet their height as `--dock`).
- **Colours by screen**: Mapping and Sound are screens of Shape and wear its colour (they were pages of System and wore blue). Play > Shaders is the one screen whose colour is not its area's.
- **Sizes are asked of the panel, not of the window**: the shell is a size container (`app`) and the workspace another (`ws`); a rule for a laptop's columns reads `@container ws (min-width: 900px)`. The test that holds this block to its style holds the rules inside a container query too.
