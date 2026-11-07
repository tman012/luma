# Exact selected dock artwork — source handoff

Inspected November 7, 2026 (EST). No new design proposal or implementation.

## Confident identity
`approved-top-dock/taylor-approved.png` is **pixel-for-pixel identical in decoded RGB** to `revision-dock/D-closeup-4x.png`. Both are 1616 × 472. Maximum/mean RGB difference: 0. The PNG file hashes differ (encoding/metadata), so this is decoded-pixel identity, not byte identity.
Approved SHA256: `527a801722ec45a601fa7f1e209e5ce00a23754808bee1f54d427502a7511e7f`.
Original D PNG SHA256: `6e32cf77b187c426c472fca2cdc91982c26507c322cdd456be6070d3a8ac6d70`.
C uses the same artwork family but a different dock size/placement and background; D is the EXACT selected image match.

All paths below are relative to `/workspace/design/desktop-circle-nav/2026-11-07/`.

## Actual original artwork — reuse, do not redraw
Original 1024 × 1024 transparent RGBA masters:
- `revision-dock/icons/luma-dock-search-1024.png`
- `revision-dock/icons/luma-dock-libraries-1024.png`
- `revision-dock/icons/luma-dock-explore-1024.png`
- `revision-dock/icons/luma-dock-calendar-1024.png`
- `revision-dock/icons/luma-dock-watchlist-1024.png`

Each also exists as original 224,144,56,48px PNG under the same filename pattern. Use 1024px masters or already-made 56px assets, preserving aspect/alpha. These originals are raster Pillow artwork, not Material Symbols, SF Symbols, Apple icons, or sourced SVGs. No matching original SVGs found in this stream. Do not substitute newly drawn or professional-family glyphs: Taylor selected these exact original tile illustrations.

Selected visual sequence: blue dimensional magnifying-glass tile; violet fanned media covers/play tile; teal detailed compass tile; ivory/coral calendar tile; amber crimson bookmark/star tile. Rounded-square tiles, dimensional surfaces and detailed artwork are intentional in the selected pixels.

## Original source and native layout
`revision-dock/build.py`:
- Lines7–111: original gradients, tile rim, gloss, illustration masks, inset/element shadows.
- Lines112–120: master PNG and size exports.
- Lines124–142: exact dock material/compositing function.
- Lines143–151: actual C/D dimensions, placement and 4× selected closeup rendering.
`revision-dock/SOURCES.md` records provenance and original design assumptions; route mentions there are historical notes, not newly verified implementation facts.

Original D: native viewport1440 × 900; dock box `(542,806)–(898,890)`, width356,height84,radius26. Icon bitmap56 × 56, horizontal gap12, padding14, centers584/652/720/788/856, y848. All values are native pixels before the selected 4× closeup render. Selected crop is source coordinates `(518,782)–(922,900)` rendered at4×, giving1616 × 472. In selected-image coordinates the dock spans `(96,96)–(1520,432)`; each icon bitmap224 × 224, gap48,padding56,radius104.

Material recipe in `dock()`:
- Local backdrop Gaussian blur18px.
- RGBA light tint(232,228,236,64), then dark tint(20,18,28,46).
- 1px white edge at alpha120/255.
- Panel shadow offset6px, Gaussian14px, alpha90/255.
- Icon shadow offset2px, Gaussian3px, alpha80/255.
- Masters Lanczos-resized. These selected effects must not be replaced with the later circle/glass variants.

The dark-looking panel in the selected crop is influenced by the original dark bottom-of-Home background. The same genuine blur/tints at top of the bright hero will look lighter. Exact dark pixel appearance and physically background-dependent translucency cannot both be promised on a different backdrop without owner/reviewer choice; do not silently bake the dark bottom screenshot into a top bar.

## Normal desktop TOP-CENTER geometry suggestion (handoff only)
Apply exact selected design at **one quarter of approved closeup dimensions**, i.e. its original1× native D size. Center the356 × 84 dock at x720,y52: box `(542,10)–(898,94)`, radius26; retain56px bitmap icons,12px gaps,14px padding. Icon centers584/652/720/788/856,y52. This changes placement only, not chosen proportions. Actual visible tile body is slightly inset inside each transparent56px canvas, as in the selected original. Preserve the true logo left/avatar right. Do not carry forward the superseded48px circular-button geometry.

This is a proposed implementation geometry, not a new rendered option or owner approval of exact positioning. Header fit/narrow desktop behavior still needs actual implementation review; no mobile change implied.

## Scope
No application source/repository, browser, deployment, mobile or messaging action taken. Selected image and all historical output files unchanged. Individual-glass/shelf exploration stopped when selection superseded it; no further rendering or proposal delivery.
