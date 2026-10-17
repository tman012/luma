# Phone Settings > Home screen (navy, <1024px)

Navigation
- Reached from the drawer: Home, Settings > Home screen (sidebar spec). No back arrow; header is the 56px app header: hamburger, page title "Home screen" (17px/600, white 95%, left after hamburger), avatar right.
- Remove "Libraries" from the avatar menu; it keeps only Sign out.

Layout
- Page padding 16px sides, 24px + safe-area bottom. Background #15102a + StartUI navy gradient.
- Section heading "Libraries": 13px/600, white 50%, sentence case, 24px above, 8px below, 4px inset.
- One glass card: rgba(255,255,255,.06), 1px rgba(255,255,255,.08) border, radius 16px.

Library rows (replace the up/down arrows)
- 56px rows, padding 0 12px 0 4px. Left: drag handle (lucide GripVertical 20px, white 50%) in a 44x44 target. Name 16px/500 white 95%, single line, ellipsis. Right: switch 51x31, on #3b82f6, off rgba(255,255,255,.2), white knob.
- Dividers 1px rgba(255,255,255,.06), inset to start at the name.
- Drag starts immediately from the handle only (touch-action:none); row body scrolls normally. Lifted row: bg rgba(59,130,246,.14), scale 1.02, shadow 0 8px 24px rgba(0,0,0,.4); other rows shift in 150ms; vibrate(10) if supported.
- Handle is a focusable button (aria-label "Reorder <name>"); ArrowUp/ArrowDown move the row when focused.
- Off libraries stay in place, name drops to white 50%, still draggable.
- Changes save instantly. No Save button, no success toast. On failure revert and show the top toast "Couldn't save".
- No libraries: card holds one centered line "No libraries", white 50%, 56px tall.

Kiosk picker
- Hidden below 1024px (kiosk is web-only). At >=1024px it's a second section "Kiosk" with one 56px row "Backdrop library", current value white 60% + chevron, opening a list of libraries with a blue check on the selected one.

Rules: sentence case, American English, no helper or explanatory text anywhere on the page.
