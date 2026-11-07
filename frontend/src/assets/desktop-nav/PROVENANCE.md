# Desktop dock artwork provenance

Development record only. Nothing in this file is shown in the UI.

Approved reference `taylor-approved.png` SHA256 `527a801722ec45a601fa7f1e209e5ce00a23754808bee1f54d427502a7511e7f` (1616×472). Designer reported it decoded-pixel-identical to `revision-dock/D-closeup-4x.png` (file SHA256 `6e32cf77b187c426c472fca2cdc91982c26507c322cdd456be6070d3a8ac6d70` differs; encoding only).

Portable bundle `approved-nav-assets.zip` SHA256 `4178ef04c313292ba7640add3abb4d4aa280bf69e62e4ca9e475b3f2a403c912`, 1871061 bytes. All 31 `SHA256SUMS` entries verified before copy.

Icons are the original Pillow rasters from `original/revision-dock/build.py` (gradients, tile rim, gloss, illustration masks). They are not Material Symbols, SF Symbols, Apple icons, or substitute SVGs. No original SVG exists in the handoff.

Copied into `icons/` at 56, 144, 224, and 1024 (masters, alpha kept). The UI displays the 56 CSS px canvas with a `srcset` of 56 / 144 / 224. 1024 masters are stored and not imported into the bundle. The bundle's 48px rasters were not copied: the approved control is a 56px tile, and 48px was the superseded circular-button size.

Dock material follows `dock()` in `build.py`: backdrop blur 18px, light tint rgba(232,228,236,64/255), dark tint rgba(20,18,28,46/255), 1px white edge at 120/255, panel shadow offset 6 blur 14 alpha 90/255, icon shadow offset 2 blur 3 alpha 80/255. CSS `backdrop-filter` is the live equivalent. The approved closeup was composited on the dark bottom of Home; the same tints over a bright hero read lighter. The screenshot is not baked into the bar.

Native D geometry, placed at the top: 356×84, radius 26, padding 14, gap 12, icon canvas 56. At a 1440 viewport the box is (542,10)–(898,94), center (720,52).

`SOURCES.md`, `ASSET-HANDOFF.md`, `INTERACTION-BRIEF.md`, `build.py`, and `SHA256SUMS` are the designer's files, kept here as the record.
