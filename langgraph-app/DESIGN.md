---
name: AI Glasses Companion
description: A quiet graphite studio for tangible controls and readable AI in the lens.
colors:
  bg: "#0c120f"
  bg-2: "#111b15"
  surface: "#141f18"
  surface-2: "#19261d"
  solid: "#152219"
  line: "#26382d"
  line-2: "#354a3c"
  line-3: "#55715d"
  ink: "#e2ede5"
  ink-2: "#a9bdb0"
  ink-3: "#97ad9e"
  ink-4: "#8ba392"
  accent: "#a5eac5"
  accent-2: "#8dd6ad"
  accent-ink: "#b6f1cf"
  accent-wash: "#a5eac512"
  accent-line: "#a5eac56b"
  warn: "#d8bf83"
  danger: "#efa999"
  action: "#b5edc8"
  action-ink: "#12351e"
  action-hover: "#d4fbe0"
  control: "#18291e"
  control-ink: "#c7dbcd"
  hud: "#a9f5d5"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "clamp(25px, 2.7vw, 38px)"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "-0.03em"
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif'
    fontSize: "15px"
    lineHeight: 1.62
  title:
    fontSize: "19px"
    fontWeight: 500
    letterSpacing: "-0.012em"
  label:
    fontSize: "13.5px"
    fontWeight: 500
  hud-body:
    fontSize: "13px"
    lineHeight: 1.85
  mono:
    fontFamily: '"SF Mono", "Cascadia Mono", "JetBrains Mono", "Roboto Mono", Consolas, monospace'
    fontSize: "10px"
rounded:
  r-lg: "16px"
  r: "12px"
  r-sm: "9px"
  r-xs: "6px"
  nav: "7px"
  pill: "99px"
spacing:
  control-gap: "7px"
  compact-gap: "8px"
  card-gap: "16px"
  card-padding: "25px"
  stage-block: "30px"
  stage-inline: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.action-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.r-sm}"
    padding: "9px 17px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.action-ink}"
  button-secondary:
    backgroundColor: "{colors.control}"
    textColor: "{colors.control-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.r-sm}"
    padding: "9px 17px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.control-ink}"
    rounded: "{rounded.r-sm}"
    padding: "9px 17px"
  input:
    backgroundColor: "{colors.control}"
    textColor: "{colors.control-ink}"
    rounded: "{rounded.r}"
    padding: "12px 13px"
  navigation:
    rounded: "{rounded.nav}"
    padding: "11px 10px"
  chip:
    backgroundColor: "{colors.control}"
    textColor: "{colors.control-ink}"
    rounded: "{rounded.pill}"
    padding: "3.5px 10px"
  card:
    rounded: "{rounded.r-lg}"
    padding: "{spacing.card-padding}"
---

# Design System: AI Glasses Companion

## Overview

**Creative North Star: "The Optical Studio"**

A restrained night studio makes an AI assistant feel tangible. Green graphite surfaces hold pale mint controls and clear Chinese typography; material depth belongs to the devices, while the surrounding application stays quiet and compact. The lens is a place to read actual source content and AI responses, with physical framing that establishes its identity without obscuring the work.

The implemented world follows the user's requested high-tech Muzli direction. [Weekly Designers Update 500](https://muz.li/blog/weekly-designers-update-500/) and [Weekly Designers Update 558](https://muz.li/blog/weekly-designers-update-558/) are inspiration references, not copied assets or an assertion that any featured design was reproduced. Fonts are the installed system stacks declared below; no custom font is downloaded or self-hosted.

**Key Characteristics:**
- Green graphite surroundings with pale mint actions and projection.
- Sculpted hardware, restrained application surfaces.
- Source media and readable results share the optical view.
- Simulation is explicit; processing and errors reflect actual service state.

## Colors

The palette reads as dark green graphite with a luminous mint primary accent. Frontmatter values are normative; CSS cascade order is `app.css`, `exam.css`, `studio.css`, then `hardware.css`.

### Primary

- **Projection Mint** (`accent`, `accent-2`, `accent-ink`, `hud`): active affordances, optical status, and lens projection. The hardware HUD uses its own scoped mint.
- **Action Mint** (`action`, `action-hover`, `action-ink`): high-visibility filled actions and their dark lettering.
- **Mint Wash and Stroke** (`accent-wash`, `accent-line`): restrained emphasis and field focus borders.

### Neutral

- **Green Graphite** (`bg`, `bg-2`, `surface`, `surface-2`, `solid`): layered application backgrounds.
- **Structural Green** (`line`, `line-2`, `line-3`): dividers, boundaries, and interaction emphasis.
- **Frosted Ink** (`ink`, `ink-2`, `ink-3`, `ink-4`): descending text emphasis without pure-white glare.
- **Control Green** (`control`, `control-ink`): secondary controls, fields, and compact chips.
- **Warm Status** (`warn`, `danger`): warning and failure semantics, not additional decorative accents.

**The Signal Rule.** Use the brightest mint for an available action, active state, or projected result; surrounding structure should remain quiet.

## Typography

Display and body use the same system sans stack, including the implemented Chinese fallbacks. Labels inherit this stack; small command identifiers and keyboard hints use the separate mono stack. These are font preferences resolved by the operating system, not bundled font assets.

The hierarchy is restrained: a responsive medium-weight display introduces a view; medium-weight titles organize cards; body text supports sustained reading; compact labels identify controls. HUD prose has more generous leading than surrounding status text. The hardware heading becomes (27px) on small screens. Metadata uses observed (9–12px) sizes and must not become the primary reading surface. There is no imposed modular scale or evidenced maximum prose width.

**The Reading Rule.** Keep full AI responses as selectable, scrollable text; decorative projection must not replace readable content.

## Layout

The application opens directly into the optical studio. The top bar holds the return-to-lens brand, model choice, and library entry; there is no sidebar or mobile tab bar. Scene selection, capture, recording, settings, and result reading happen inside the lens using the ring. The library remains a separate shared archive view. Main stage spacing is represented in the frontmatter. The library caps at (1160px); the hardware surface caps at (1400px).

At (760px) and below, the hardware view becomes one column and the compact controller follows the lens. The lens grows vertically to preserve separate source and response regions. Do not shrink the response to fit a decorative silhouette. Implemented positions and breakpoints live in `lg_assistant/web/static/hardware.css`; interaction and state behavior are described in the [hardware guide](../docs/硬件协同演示.md).

## Elevation & Depth

Application depth comes mainly from tonal separation and fine strokes. The two low elevation tokens are explicitly `none`; the remaining shared shadow is (0 12px 34px #0004). Cards retain a small hover translation with no shadow. Material rendering is concentrated in the optical rim, the ring, and the projected result: gradients, inset highlights, contact shadows, and a translucent HUD create hardware depth. These materials should not spread to every panel.

**The Material Rule.** Physical objects receive sculpted depth; ordinary controls and panels use tonal boundaries.

Exact hardware shadows, focus outlines, motion, and responsive breakpoints live in the schemaVersion 2 sidecar because the frontmatter component schema cannot express them.

## Shapes

Ordinary application components use the small rounded scale in the frontmatter. Navigation has slightly tighter corners and chips use pill ends. The single lens uses asymmetric elliptical percentages, a separate metal rim, hinge, and bridge. Its curved boundary is essential to the optical identity. The ring is a small tilted oval with an inset opening and a distinct touch strip, not a large generic circular action button.

## Components

### Buttons

Filled mint marks the main action; green secondary buttons and transparent ghost actions share the compact radius and spacing. Hover lightens the surface; active presses scale to (.98). Disabled ordinary buttons use (.42) opacity and a disallowed cursor. Preserve native button semantics. The studio focus outline is (2px solid #b9f1d0), offset (3px); the hardware surface uses (2px solid #c0f5d5), offset (4px).

### Inputs / Fields

Textareas use the rounded field token, a thin green border, and vertical resize. Focus changes the background to (#192b1f) with the accent-line border. The placeholder is (#91a797), distinct from entered content. File import is a visible native button linked to a hidden native file input.

### Navigation

Compact icon-and-label rows use the tighter navigation radius. Active rows use (#1d3023) with (#ceefda) text; hover uses (#203326) with (#e0f5e7) text. Hardware scenario selectors are native buttons in a labeled group; the selected scenario has `aria-pressed="true"` and a mint underline.

### Chips

Compact status chips retain pill geometry and a small steady status dot. The final studio cascade gives them the control surface and text colors; do not infer a transparent chip from an earlier overwritten declaration. Status is conveyed in words as well as color.

### Cards / Containers

Cards use the large radius, a quiet green gradient, a fine stroke, and no resting or hover shadow. Hover lightens the surface and translates it slightly. Standard panels remain tonal containers. The hardware controller uses a separate compact surface, leaving the lens as the dominant object.

### Optical Hardware and HUD

The signature sequence is press, shutter, processing, and projection into the lens. Photo mode preserves an unobscured source region beside the scrollable response; meeting mode hides the photo and expands the response. Imported media and returned AI content are real; ring transport and device connection are explicitly simulated. Processing is indeterminate and must not imply measured progress or fabricated completion.

The result is keyboard-focusable and scrollable, with normal focus treatment. Hide/show controls preserve access to the response. Status updates use polite live regions. Reduced-motion preferences disable animation and transition, including shutter, scan, projection, and ring motion; visible textual state remains.

## Do's and Don'ts

### Do:
- **Do** keep imported media unobscured and full AI responses scrollable inside the lens.
- **Do** keep the ring physically small and its trigger obvious.
- **Do** preserve native controls, visible keyboard focus, and reduced-motion behavior.
- **Do** label simulated hardware separately from real AI processing and show failures honestly.
- **Do** reuse the effective dark studio tokens and installed font stacks.

### Don't:
- **Don't** replace the curved lens with an ordinary rectangular dashboard card.
- **Don't** use decorative metrics or prewritten answers as evidence of AI execution.
- **Don't** introduce a fitness scenario into the current meeting and photo product.
- **Don't** cover the source image with the response or reduce response type to preserve ornament.
- **Don't** describe inspiration references, external fonts, or unused artwork as shipped assets.
