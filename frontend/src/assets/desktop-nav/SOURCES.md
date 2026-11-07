# Luma desktop dock nav — revision C/D (static mockups, owner selection)
Static composites only. Proposed placements, not live UI. No browser, repo, deployment, accessibility, or motion claims. Mobile untouched.

## Base provenance
/workspace/luma-qa/fastgate/ece4395c/designer/home-probe.png (1440×900, existing real desktop Home screenshot). Only change to the base: old centered chevron at (707,43)-(733,61) removed by OpenCV Telea inpainting (_base-chevron-removed.png). Logo, avatar, hero art, title, metadata, description, genres, and gradient are unchanged.
Visual reference: ../dock-reference.jpg (Taylor's macOS Dock screenshot), used for treatment only: rounded-square dimensional icons, frosted translucent dock. No Apple or branded artwork copied.

## Icons (original, drawn in Pillow at 1024px, downsampled with Lanczos) — icons/
Left to right: Search (blue tile, glass lens, wood handle) · Libraries /browse (violet tile, fanned blue/pink/amber covers with play frame) · Explore (teal tile, compass with coral/slate needle) · Calendar /settings/tv (ivory tile, coral binding, rings, textless grid) · Watchlist (amber tile, crimson ribbon bookmark with star). No labels, numbers, or text. No active state, because Home isn't one of the five.
Sizes: 1024, 224, 144, 56, 48 px. icons-contact-sheet.png is for internal review.

## Placements
- C: compact frosted dock beside the real logo, replacing the chevron. Box (212,17)-(520,89): 72px tall, 48px icons, 11px gaps, 12px padding.
- D: centered bottom dock like the reference. Box (542,806)-(898,890): 84px tall, 56px icons, 12px gaps, 14px padding. It sits below the genre pills and to the right of them, so it doesn't cover title, metadata, description, or genres. It's 6px off the bottom edge.
Frosted glass: a locally blurred underlay (18px gaussian) with a light and charcoal translucent tint, a 1px white edge at 47% opacity, and a soft drop shadow. It isn't an opaque capsule.

## Files
C-full-context-1440x900.png, D-full-context-1440x900.png (native).
C-closeup-4x.png (2024×452), D-closeup-4x.png (1616×472): the dock and icons are rendered at 4× from 1024px masters. The background photo and logo pixels are only Lanczos-enlarged from the 1440 source.
build.py regenerates everything. Old A/B files and originals are untouched.
