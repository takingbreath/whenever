---
name: Whenever
description: Whenever it happens, do whatever you want. IFTTT's grammar for the physical world, where the sentence is the program.
colors:
  ground: "#f3f3f1"
  surface: "#ffffff"
  surface-2: "#ececea"
  ink: "#121316"
  ink-2: "#464950"
  ink-3: "#6b6f77"
  line: "#e1e1dd"
  line-strong: "#c9c9c4"
  stage: "#0d0e10"
  fired: "#e5322d"
  fired-ink: "#ffffff"
  ok: "#0b7a57"
  focus: "#3346d3"
  hue-indigo: "#3346d3"
  hue-jade: "#0b7a57"
  hue-peacock: "#0b7285"
  hue-plum: "#7b3fb2"
  hue-marigold: "#f2a20a"
  hue-ink: "#17181c"
  on-marigold: "#1d1503"
  dark-ground: "#101113"
  dark-surface: "#1a1b1f"
  dark-surface-2: "#24262b"
  dark-ink: "#f1f1ee"
  dark-ink-2: "#c4c5c9"
  dark-ink-3: "#9a9ca3"
  dark-line: "#2c2e33"
  dark-line-strong: "#3d4047"
  dark-focus: "#8b97ff"
typography:
  display:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(40px, 9vw, 68px)"
    fontWeight: 900
    lineHeight: 0.98
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(32px, 7vw, 44px)"
    fontWeight: 900
    lineHeight: 1.02
    letterSpacing: "-0.035em"
  sentence:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(23px, 5.4vw, 30px)"
    fontWeight: 800
    lineHeight: 1.12
    letterSpacing: "-0.025em"
  sentence-joint:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontWeight: 500
  slot:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(25px, 5.6vw, 32px)"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.025em"
  connector:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 500
    lineHeight: 1.05
    letterSpacing: "-0.03em"
  readback:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 800
    lineHeight: 1.35
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 800
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 700
  button:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 800
  numeral:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 900
    letterSpacing: "-0.02em"
    fontFeature: "\"tnum\" 1"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12.5px"
    lineHeight: 1.5
rounded:
  xs: "6px"
  sm: "10px"
  nav: "12px"
  md: "14px"
  tab: "16px"
  control: "18px"
  card: "22px"
  pill: "999px"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "22px"
  2xl: "34px"
  gutter-phone: "16px"
  gutter-desk: "40px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    typography: "{typography.button}"
    rounded: "{rounded.pill}"
    padding: "10px 20px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.hue-indigo}"
    textColor: "{colors.surface}"
  button-big:
    padding: "14px 30px"
    height: "56px"
  button-small:
    padding: "6px 14px"
    height: "36px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
  button-ghost-hover:
    backgroundColor: "{colors.surface-2}"
  button-danger:
    backgroundColor: "{colors.fired}"
    textColor: "{colors.fired-ink}"
  chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "7px 14px"
    height: "38px"
  chip-on:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
  segmented:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.pill}"
    padding: "4px"
  segmented-selected:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "6px 16px"
    height: "36px"
  card:
    backgroundColor: "{colors.hue-indigo}"
    textColor: "{colors.surface}"
    typography: "{typography.sentence}"
    rounded: "{rounded.card}"
    padding: "20px 20px 18px"
  card-marigold:
    backgroundColor: "{colors.hue-marigold}"
    textColor: "{colors.on-marigold}"
  fired-tab:
    backgroundColor: "{colors.fired}"
    textColor: "{colors.fired-ink}"
    typography: "{typography.label}"
    padding: "38px 16px 12px"
  builder:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "22px 20px 24px"
  slot-input:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.slot}"
    padding: "4px 0 10px"
  sentence-readback:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.readback}"
    rounded: "{rounded.md}"
    padding: "16px 18px"
  nav-create-phone:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    size: "58px"
  nav-link-desk-active:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.nav}"
    padding: "11px 12px"
  toast:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    padding: "13px 20px"
---

# Design System: Whenever

## Overview

**Creative North Star: "The Sentence Is the Program"**

Whenever plays the category grammar straight: bold colour cards, one sentence per rule, pill controls and a five-tab shell. The twist is typographic. Every rule is a plain sentence, and the build shows it the same way everywhere: the joints of the sentence ("When", ", then", "and") stay light, and the parts the person chose stay heavy. A card, the Create readback and Spark's Check card all speak this one grammar, so the person learns to read a program as a sentence.

The ground is a quiet warm grey with white surfaces and near-black ink. Colour belongs to the Whenevers. Each card is one flat hue from a fixed set of six, with a thin inset keyline that turns a slab of colour into an object. One red stays outside the card set: it means something happened or needs attention, and its signature form is the tab that drops out under a card that just fired.

Density is phone-first and generous: large titles, large sentences, 44px and larger touch targets, a bottom tab bar with a raised Create button. On a desktop the same shell becomes a left rail. Motion is quiet. The fired tab is the one authored moment. The build refuses a dashboard of panels and a developer console look; code and requests appear only inside dark "stage" blocks on demand.

**Key Characteristics:**
- One face, Schibsted Grotesk, from 500 to 900, with tight negative tracking on everything large.
- Six flat card hues, assigned per Whenever; red is never a card hue.
- Light joints, bold slots: the sentence grammar is shared by cards, the readback and the Check card.
- Pills for every control, 22px corners for every container, 1.5px inset rings instead of borders.
- 1.75-stroke line icons drawn as masks, so they take the text colour.
- A bottom tab bar on a phone, a 248px left rail at 960px and wider.

## Colors

A neutral warm-grey world where saturated colour is carried only by the Whenever cards, by indigo as the hand of interaction, and by one alarm red.

### Primary
- **Gate Indigo** (hue-indigo): the default card hue and the interaction colour. It is the hover fill of every primary pill, the focus ring, the text selection, the underline under a filled slot, the active Create button, the bars of the hourly chart and the brand mark.

### Secondary (the card hues)
- **Garden Jade** (hue-jade): a card hue; the same value is the "ok" colour for ready states, done ticks, hit dots and the live status dot.
- **Harbour Peacock** (hue-peacock): a card hue.
- **Night Plum** (hue-plum): a card hue; also the badge of camera actions.
- **Marigold** (hue-marigold): the one light card hue. It carries dark text (on-marigold) and dark keylines, tiles and switches, never white text.
- **Card Ink** (hue-ink): the card hue for schedule-only Whenevers (reports). Automatic assignment hashes each Whenever across the other five hues; ink appears only when a Whenever has a schedule and no sensors.

### Tertiary
- **Fired Red** (fired, with fired-ink text): the alarm colour. Its signature use is the fired tab under a card. The build also uses it for the Activity dot on the nav, the "Replay" badge on the camera frame, error notes, the destructive pill, the warning status, the live (recording) mic and the warning icon in the Check card.

### Neutral
- **Warm Ground** (ground): the page.
- **Paper White** (surface): builder, empty state, reports, neutral cards, the desktop rail.
- **Fog** (surface-2): the readback panel, segmented track, aid buttons, icon buttons, inactive bars, desktop nav hover and active.
- **Ink** (ink): text, the primary pill, the selected chip, the phone Create button, the toast.
- **Ink 2 / Ink 3** (ink-2, ink-3): secondary text, then meta text, connectors and placeholders.
- **Line / Line Strong** (line, line-strong): 1.5px inset rings and dividers; line-strong for hover rings, ghost pills and dashed empty slots.
- **Stage** (stage): the dark behind camera frames, pictures and code blocks.
- **Dark mode**: the dark-* tokens replace the neutrals under prefers-color-scheme: dark. Card hues and red stay the same; focus lightens to dark-focus.

### Named Rules
**The Six Hues Rule.** A Whenever card is one flat hue from the six, never a gradient, never a tint, never a hue outside the set. Text on a hue is white with a white 92% secondary tint, except on marigold, which takes on-marigold at 100% and 74%.

**The Red Means Attention Rule.** Fired red marks an event, an error, a destructive act or a live recording. It is never a card hue, a decoration or a brand colour.

**The Indigo Hand Rule.** Indigo is where the interface answers the hand: hover on the primary pill, focus, selection and the slot underline. Neutral controls stay ink and grey at rest.

## Typography

**Display Font:** Schibsted Grotesk (with ui-sans-serif, system-ui, sans-serif), self-hosted woff2, variable 400 to 900, SIL OFL.
**Body Font:** Schibsted Grotesk.
**Label/Mono Font:** ui-monospace, SFMono-Regular, Menlo, only for code, requests and inline code.

**Character:** a sturdy Scandinavian news grotesque pushed to 800 and 900 with negative tracking. Heavy and friendly at the size of a sentence, plain at body size.

### Hierarchy
- **Display** (900, clamp 40 to 68px, 0.98): the Start and sign-in title only.
- **Headline** (900, clamp 32 to 44px, 1.02, -0.035em): the screen title.
- **Sentence** (800, clamp 23 to 30px, 1.12, -0.025em): the sentence on every card. Detail, Check and Done cards scale it up to 34 to 38px; templates set it at 20px.
- **Slot** (800, clamp 25 to 32px): what the person types into Create; the placeholder drops to 500 in ink-3.
- **Connector** (500, 30px, ink-3): "When" and "Then" above each slot.
- **Readback** (800, 20px, 1.35): the program read back under the builder.
- **Title** (800, 20px): section titles; report and sheet titles at 18px; empty and writing titles at 24 to 28px in 900.
- **Body** (400, 16px, 1.45): running text; ledes at 19px with a 52ch measure.
- **Label** (600 to 800, 12 to 14px): card meta, chips, status pills, day labels, nav labels (12px 600 on a phone, 16px on a desktop). Sentence case, never uppercase, never letterspaced.
- **Numeral** (900, 24px, tabular): value tiles; stats at 20px 800.

### Named Rules
**The Light Joint Rule.** In any sentence that shows a Whenever, the joints are weight 500 in the secondary tint, and the content is 800. On a card the joint takes the on-hue secondary tint. In the readback the joint takes ink-3, and each chosen slot gets a 3px indigo underline; an empty slot turns dashed line-strong at 600.

**The Column Figures Rule.** Tabular figures apply only to numbers that stand in columns: stats, value tiles and the counts beside the bars. Schibsted's tnum widens punctuation, so never set it on sentences, times in card footers, or running text.

**The One Face Rule.** Schibsted Grotesk sets everything a person reads. Monospace appears only inside code and request blocks.

## Layout

Phone first. Screens sit in a centred column of 720px max (Live widens to 1100px) with a 16px gutter on a phone and 40px on a desktop. The bottom of the phone column reserves the tab bar height (72px) plus 40px plus the safe area.

At 960px and wider the shell becomes a two-column grid: a sticky 248px left rail and the content. The rail holds the brand, the Create pill, the four nav links and the spend meter at the foot. The toast recentres on the content column.

Rhythm steps in small even values: 6 to 8px inside clusters, 12 to 16px between cards and rows, 22px between builder blocks, 34px above section titles. Cards stack in a single column at 16px gaps. Templates scroll sideways with snap at 72% width on a phone, and become a two-column grid at 720px (three in "more").

Breakpoints in use: 420px (activity pictures drop under the text), 600px (camera tiles take 10px corners; the sheet becomes a bottom sheet at 640px), 620px (phone setup steps go two-column), 720px, 960px.

## Elevation & Depth

A hybrid. Two soft ink shadows lift objects that act; neutral information sits flat with a 1.5px inset ring. Shadows are two-layer: a tight contact shadow and a wide, negative-spread drop.

### Shadow Vocabulary
- **Rest** (`box-shadow: 0 1px 2px rgba(18,19,22,0.06), 0 8px 24px -12px rgba(18,19,22,0.22)`): hue cards, the builder, the writing panel, the camera frame, the selected segment, the desktop Create pill.
- **Lift** (`box-shadow: 0 2px 4px rgba(18,19,22,0.08), 0 18px 40px -16px rgba(18,19,22,0.32)`): card hover, the phone Create button, the draft action, sheets and the toast.
- **Ring** (`box-shadow: inset 0 0 0 1.5px var(--line)`): empty state, reports, value tiles, stats, chips, action items, choices, entry icons, controls, the status pill.
- **Keyline** (`box-shadow: inset 0 0 0 1.5px rgba(255,255,255,0.22)` on a layer inset 6px): inside every hue card.

### Named Rules
**The Ring or Shadow Rule.** A surface either rests on a shadow because it acts, or carries an inset ring because it informs. Neither uses a CSS border for its edge.

## Shapes

Round and soft. Every control that acts is a pill (999px) or a circle: buttons, chips, segmented controls, status pills, the toast, icon buttons, the mic, the phone Create button. Every container takes 22px corners: cards, builder, empty state, reports, sheets, camera frames. Inner parts step down: 18px for live controls, 16px for the fired tab's lower corners and the QR plate, 14px for panels inside cards (readback, warning, value tiles), 12px for nav links and entry icons, 10px for icon tiles, inputs and pictures, 6px for inline code and thumbnails. The brand mark repeats the card keyline: an indigo square with a white inset ring.

## Components

### Buttons
Confident, ink-solid pills that answer in indigo.
- **Shape:** full pill (999px), 44px tall by default.
- **Primary:** ink fill, surface text, weight 800, 10px 20px. Icons inside are 18px.
- **Hover / Focus / Active:** hover fills indigo with white text; active scales to 0.97; focus is the global 3px focus outline at 3px offset. Disabled sits at 45% opacity with no hover.
- **Sizes:** big (56px, 18px text) for the main act of a screen; small (36px, 14px) in rows.
- **Ghost:** transparent with a 1.5px line-strong inset ring; hover fills Fog.
- **Danger:** fired red with white text, for delete only.
- **Link button:** ink-2 text, 700, 1.5px underline at 4px offset.
- **On a hue card:** the pill inverts to white with indigo text; the ghost turns to a white 55% ring.

### Chips
- **Style:** surface fill, ink-2 text, 1.5px line ring, 38px tall (44px for Then choices in Create, 32 to 34px in feeds and live controls).
- **State:** hover strengthens the ring to line-strong; selected (on or aria-pressed) fills ink with surface text and no ring; waiting drops to 60% opacity.
- **Aid:** a Fog-filled sibling for helpers in Create (point at a thing, show an example).

### Segmented control
A Fog pill track with 4px padding; the selected segment becomes a white pill on the rest shadow.

### Switch
52 by 30px, a 24px white knob. On a hue card the track is translucent white (28% off, 95% on) and the knob takes the card hue when on. On marigold the track and knob invert to dark.

### Cards / Containers
- **Corner Style:** 22px.
- **Background:** one card hue; the stopped state desaturates the hue (saturate 0.15, brightness 0.92).
- **Shadow Strategy:** rest; linked cards lift 2px with the lift shadow on hover and press to 0.99.
- **Border:** none. A keyline layer sits 6px inside with 16px corners and a 1.5px white 22% inset ring (dark 18% on marigold).
- **Internal Padding:** 20px 20px 18px; detail and Check cards 24px 22px.
- **Anatomy:** a top row (32px icon tiles at white 18%, or the camera line with a live dot, plus the switch), the sentence, then a footer of 14px 600 meta in the secondary tint. Then-actions show as small white 16% pills. The 92% tint keeps 14px meta at about 4.6:1 or more on every white-text hue (jade is #0b7a57 for this reason).

### Fired Tab (signature)
A red tab tucked under the card it belongs to: it overlaps the card by 24px from behind (z-index under the card), is inset 22px from each side, has 16px lower corners and 38px top padding so the visible part reads as a tab. It carries a bell icon, "Fired N min ago" in 14px 800, and "See proof" underlined at the right. It drops 18px into place over 0.7s on the expo-out ease. The animation moves it only; the tab is fully visible at its resting state, so no frame of the animation hides it.

### Inputs / Fields
- **Slot:** no box. A transparent field with a 3px ink underline; empty shows a dashed line-strong underline; focus turns the underline indigo and solid. The sign-in code field uses the same slot at 28px with 0.06em tracking.
- **Secondary line:** an 18px 600 field with a 2px dashed underline that turns solid indigo on focus.
- **Box fields:** surface or Fog fill with 10 to 14px corners and a 1.5px line ring; focus turns the ring to a 2px indigo ring.

### Create Readback and Check Card
The builder is a white container on the rest shadow with connectors ("When", "Then") over slots and a 48px circular mic. Below it, the readback restates the program on Fog in the Light Joint grammar. The Check card is an indigo hue card that shows Spark's reading in the same grammar at 26 to 34px, a fact list under a white 22% rule, and, when needed, a white warning panel with a red alert icon.

### Navigation
- **Phone:** a fixed five-column tab bar, 72px plus the safe area, 92% surface with a saturated 14px blur and a top hairline. Labels are 12px 600 in ink-3 under 22px icons; the current tab turns ink. Create sits in the centre as a 58px ink circle raised 22px, with the lift shadow and a 5px ground-coloured ring; it turns indigo when current.
- **Desktop:** a sticky 248px white rail with a right hairline. The brand (22px 900) leads, then Create as a full-width pill, then rows of 16px links with 12px icon gaps; hover and current fill Fog. The spend meter (a 6px ink bar) sits at the foot.
- **Activity dot:** a 9px red dot with a 2px surface ring on the Activity link.

### Icons
Line icons on a 24px grid, 1.75 stroke, round caps and joins, no fill (the record icon's centre is the one filled detail). Each icon is an SVG data URL applied as a CSS mask over a currentColor block, so it always takes the text colour. Default size 22px; 18px in pills, chips, aids and tiles; 15 to 17px in small pills and the fired tab; 26px in the phone Create button.

### States
Status pills: 8px dot plus label in a ringed surface pill; live breathes the jade dot (2.4s), warn turns red. Ready and waiting tags use jade at 14% or Fog. Writing progress lists jade tick circles, with the last line in ink 700 and a spinning indigo-topped ring. Notes are 14px ink-3, red for errors, jade for ok. Toasts are ink pills that rise 12px and fade in over 0.5s. Reduced motion collapses every animation and transition to 0.01ms with one iteration.

## Do's and Don'ts

### Do:
- **Do** write every Whenever as one sentence and render it with the Light Joint grammar: joints at 500 in the secondary tint, content at 800.
- **Do** pick card colour only from the six hues, and give marigold dark text, keylines, tiles and switches.
- **Do** put the 6px inset keyline (1.5px, white 22%) inside every hue card.
- **Do** make every control a pill or a circle at 44px or taller on a phone, and every container 22px round.
- **Do** draw new icons as 24px-grid paths at 1.75 stroke with round caps, painted as masks in currentColor.
- **Do** animate with transform on the expo-out ease (cubic-bezier(0.16, 1, 0.3, 1)) between 0.2s and 0.7s, from a state that is already visible.
- **Do** limit tabular figures to columns of numbers.

### Don't:
- **Don't** use fired red as a card hue, a decoration or a brand colour.
- **Don't** add a second typeface, uppercase labels or letterspaced labels.
- **Don't** set tabular figures on sentences, card footers or running text; Schibsted's tnum widens punctuation.
- **Don't** draw the edge of a card or panel with a CSS border; use the rest shadow or a 1.5px inset ring.
- **Don't** use icon fonts, emoji or text glyphs as icons.
- **Don't** add gradients, tints or patterns to a hue card.
- **Don't** build a dashboard of panels or a developer console look; code stays in dark stage blocks behind a disclosure.

## Added after the first record (2026-11-03)

- **Pairing sheet.** A sheet with three numbered steps (30px ink circles with a counter): install ntfy, follow the private topic, send a test alert and confirm "Yes, it arrived". On a phone, step two is an "Open in ntfy" pill. On a desktop, it is a QR code on white with a 1.5px inset ring, and the topic sits under it on fog. The server makes the topic on first pairing, and phone alerts count as ready only after the confirmation. The Alert my phone chip, the Check card and the Actions page all open the same sheet.
- **Live focus.** "Watch it live" opens Live with the Whenever's own hue card above the wall: its state (a breathing dot, "Looking", red "Seen now", or "Stopped" in the stopped desaturation), its switch, its sentence at 19-24px, its sensor rows with white dots, and links to its page and to all cameras. When it watches one camera of the wall, a spotlight marks that tile: a 3px white ring and a 55% stage shade over every other tile.
