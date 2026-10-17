# Luma app navigation (Sonarr-style), navy

## Desktop (>=1024px)
- Fixed left sidebar, 220px, full height. Background rgba(21,16,42,.92) + blur 24px over #15102a; right border 1px rgba(255,255,255,.08).
- Top: Luma logo 28px tall, 64px row, padding 0 20px (matches header height).
- Content area: margin-left 220px. Web kiosk backdrop fills the content area (not under the sidebar). Avatar menu stays top-right of content.
- Items (Home, Settings): 44px tall, margin 0 8px, padding 0 12px, radius 10px, gap 12px. Icon 20px (lucide House, Settings). Label 15px/500, white 70%.
- Hover: bg rgba(255,255,255,.06), text white 95%.
- Active: bg rgba(59,130,246,.14), text white 95%, icon #3b82f6, 3px #3b82f6 bar on the left inside edge (radius 2px).
- Settings expands: 16px chevron at right, rotates 90deg in 150ms. Auto-expanded on any /settings route; click toggles.
- Sub-items (Home screen): 36px tall, padding-left 44px (aligns with label), 14px/400, white 60%. Active sub: white 95% + 3px blue bar, no fill. 2px gap between subs, 4px below parent.
- Focus: 2px #60a5fa ring, offset 2px. Labels sentence case. No section headings, counts, or helper text.

## Phone/tablet (<1024px)
- Header 56px + safe-area top: 44px hamburger tap target (24px icon) at left, logo next to it, avatar right. Same navy/glass as now.
- Drawer: 280px (max 85vw), slides from left 220ms cubic-bezier(.2,.8,.2,1); scrim rgba(0,0,0,.5) fades in.
- Drawer background #15102a with the same glass layer, safe-area top/bottom padding, logo row 56px.
- Items 48px tall (tap target), sub-items 44px; same colors and active state as desktop.
- Closes on scrim tap, Esc, swipe left, or selecting an item. Focus trapped while open; body scroll locked.
- prefers-reduced-motion: fade only, no slide.
