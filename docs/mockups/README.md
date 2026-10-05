# Panel mock-ups

Editable copies of every control panel screen, as a starting point for layout work.

## Where the files are

`current/` (not kept in git; the CI job `panel-ui` regenerates it as the `ui-mockups` artifact):

```
current/
  phone/     390 px wide (pictures at 2x: 780 px)
  laptop/    1280 px wide (pictures at 2x: 2560 px)
```

Each folder holds the same screens, each as `.svg`, `.psd` and `.png`. The names are fixed, lower-case with hyphens, so a layout file can keep pointing at them:

| File | Screen |
| --- | --- |
| `live`, `mix`, `media` | the three main screens |
| `live-shader` | Live while a shader plays (the Vibes row and the shader's strip) |
| `room` | the Room screen, as staff see it |
| `system` | the System index: Health, then the rows in three groups, each with its chip |
| `system-health` | Health |
| `system-projectors`, `system-room`, `system-schedule`, `system-shaders-and-vibes`, `system-people-and-codes`, `system-sound` | the Everyday pages |
| `system-at-power-up`, `system-streams`, `system-projection-mapping`, `system-boxes-in-step`, `system-midi-controller`, `system-dmx`, `system-osc` | the Show tools pages (the MIDI page with a fake Korg nanoKONTROL2 plugged in, so its drawn layout is there) |
| `system-network`, `system-updates`, `system-remote-support`, `system-backup-and-reset`, `system-look`, `system-about-and-power` | the This box pages |
| `system-page-off` | a page whose module is switched off (Streams): the description and one button |

Every optional module is switched on, so every card is in the picture.

## Which file for which tool

- **SVG, for Illustrator, Penpot, Figma or Inkscape.** These are vectors with real, editable text. Each section, card and control is its own named group, for example `Card: At power-up` and then `Button: Try it now`. In Illustrator, open the file and look at the Layers panel. In Penpot or Figma, drag the file onto the canvas.
- **PSD, for Photoshop.** One layer group per card. Inside each group, the card's background is at the bottom with one layer per control above it (button, slider, label). Text is pictures here, not editable type. The page background is the bottom layer.
- **PNG** is the flat picture.

## What the groups are called

A screen is cut into sections, and each section is a group at the top of the SVG (a layer group in the PSD): `Header` or `Page header` (the title, with the page's `Switch: ... (on)` or `(off)`), `Back button`, `Description`, `State line` (with its `Chip: Ready`), `Message line`, `Card: <its title>`, `Group: Everyday` (a block of the System index, with one `Row: <name>` per page and its `Chip: ...`), `Controller: <its name>` (with `Controller drawing` and one `Control: <name>` per knob, fader and button), `Pads`, `Banks` and `Tab bar`. Inside a card: `List row: <name>`, `Form: <its title>`, `Fold: <its summary>`, `Label: ...`, `Hint: ...`, `State: ...`, `Button: ...`, `Field: ...`, `Menu: ...`, `Slider: ...`, `Switch: ...`. A name that repeats on a screen gets a number.

## Bring it into Figma

1. Download the `ui-mockups` artifact of the newest `panel-ui` run (GitHub > Actions > "pvj platform layer" > the run > Artifacts) and unzip it.
2. Drag an `.svg` onto a Figma canvas. It arrives as one frame with the named groups as layers; the text is real text and stays editable.
3. Drag the `.png` of the same name beside it as a reference: it is the true picture, with the fonts and shadows of the machine that made it.
4. Keep `phone/` and `laptop/` on separate pages or sections; the frames are 390 and 1280 px wide.

Text takes the font Figma has for the panel's font list (the system font), so a line may sit a little wider or narrower than in the PNG. A switch is drawn as a track and a thumb; other shapes made with CSS only are missing from the SVG (see Limits).

## Limits

- The files are made on a Linux machine, so its fonts are measured and pictured. In the SVG the text names the panel's own font list, so on a Mac it shows in the system font and may sit a little wider or narrower than its box.
- Icons drawn with CSS (not SVG) are not in the SVG.
- Shadows and gradients are left out of the SVG; the PSD and PNG have them.
- The tool is `tests/ui/mockups.js`, called by `tests/ui/screenshots.js` when `MOCKUPS` is set.
