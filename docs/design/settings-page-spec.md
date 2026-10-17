# Settings page + user menu (supersedes sidebar-nav-spec.md: no hamburger, no app sidebar)

## User menu (avatar, top-right, all sizes)
- Dropdown 8px below avatar, right-aligned, min-width 200px, padding 6px.
- Background rgba(21,16,42,.96) + blur 24px, 1px rgba(255,255,255,.10) border, radius 14px, shadow 0 12px 32px rgba(0,0,0,.45).
- Items: Settings (lucide Settings), 1px rgba(255,255,255,.08) divider with 6px margin, Sign out (LogOut). 44px tall, radius 10px, padding 0 12px, gap 12px, icon 18px white 70%, label 15px/500 white 90%. Hover/press rgba(255,255,255,.06).
- Opens 120ms fade + scale .98 to 1 from top-right; closes on outside tap, Esc, or selection. Same dropdown on phone (no sheet).

## Desktop (>=1024px): full Settings page at /settings
- App header unchanged (logo left, avatar right). Logo returns to Home.
- Page: max-width 1100px centered, padding 32px 60px. Left section list 220px, content column starts 40px to its right, content max-width 720px.
- Left list: heading "Settings" 24px/600 white 95%, 20px below. Items (Home screen for now) 40px, radius 10px, padding 0 12px, 15px/500 white 70%; hover white 6%; active rgba(59,130,246,.14) + white 95% + 3px #3b82f6 left bar.
- /settings redirects to /settings/home-screen. Content: section title 20px/600 then the cards from mobile-settings-spec.md (Libraries, plus Kiosk on desktop).

## Phone (<1024px): Plex iOS pattern (2026-10-17, Taylor)

### Account panel (replaces the dropdown on phone)
- Tapping the avatar slides in a right-side panel: width 82vw (max 340px), full height, from the right in 260ms cubic-bezier(.2,.8,.2,1). Home stays behind under a scrim rgba(0,0,0,.55); Home also shifts left 24px and dims.
- Panel: #15102a with a navy radial glow at top, plus rgba(255,255,255,.04). Left edge 1px rgba(255,255,255,.08). Safe-area padding top/bottom, sides 24px.
- Top: avatar 72px circle, 32px from safe-area top; username 22px/600 white 95%, 12px below. No email or helper line.
- List 32px below: rows 52px, no cards or dividers, icon 22px white 80% + label 17px/500 white 95%, gap 16px. Settings (lucide Settings), Sign out (LogOut). Press: rgba(255,255,255,.06) full-bleed, radius 12px.
- Closes on scrim tap, swipe right, or selecting a row.

### Settings sheet
- Opens as a full-screen sheet sliding up 300ms, top corners radius 20px, 8px below safe-area top; Home visible dimmed behind the gap.
- Sheet bg #15102a (navy token) with an rgba(255,255,255,.04) overlay and the StartUI navy glow at top. Never #1b1533 or any plum value.
- Header 56px: circular close button left (36px circle rgba(255,255,255,.10), lucide X 18px white 90%, 44px target, 16px from edge); title "Settings" centered 17px/600 white 95%.
- List starts 16px below: plain full-width rows 56px, padding 0 20px, label 17px/400 white 95%, ChevronRight 18px white 30% at right. 1px rgba(255,255,255,.08) dividers inset 20px on both sides, none after the last row. No cards, no icons, no search (one section for now).
- Rows: Home screen.
- Swipe down on the header or tap X closes it.

### Section page (Home screen)
- Pushes in from the right inside the sheet (260ms); same 56px header with a circular back button (same 36px circle, ChevronLeft) and centered title "Home screen".
- Group header "Libraries": 20px/700 white 95%, padding 24px 20px 8px.
- Rows 56px, padding 0 20px, label-left 17px/400 white 95%, control right; dividers as on the Settings list.
  - Library rows: drag handle (GripVertical 20px white 40%, 44px target) left of the label, switch right: 51x31, on #3b82f6, off rgba(255,255,255,.18), white knob. Reorder behavior per mobile-settings-spec.md.
  - Value rows (future): value 17px white 50% + ChevronRight white 30%.
- Off libraries: label white 50%. Empty: single row "No libraries" white 50%.
- No Kiosk group on phone. Saves instantly, no Save button.

Sentence case, American English, no helper text.

## Settings > Trackers (Prowlarr port)
Section order in the list: Home screen, Trackers. Route /settings/trackers, /settings/trackers/new, /settings/trackers/:id.
Tokens: bg #15102a, glass rgba(255,255,255,.06) + 1px rgba(255,255,255,.08), primary #3b82f6 (hover #60a5fa), danger #ef4444, success #22c55e, text white 95/70/50%. Sentence case, no helper text, no placeholder sentences.

### Tracker list
- Desktop: content title "Trackers" 20px/600 with a blue "Add tracker" button (40px, radius 10px, Plus icon) right-aligned on the same line. One glass card, radius 16px; rows 56px, padding 0 20px: status dot 8px (green ok, red failing, white 30% off), name 15px/500 white 95%, type 13px white 50% (Torznab / Newznab), switch 44x26 at right (on #3b82f6). Row click opens edit; the switch toggles enable without opening. Dividers 1px white 8%. Disabled rows: name white 50%.
- Phone (Plex iOS sheet): header circular back + centered "Trackers" + circular Plus (36px, rgba(255,255,255,.10)) at right. Plain 56px rows as above, switch 51x31, chevron omitted (row tap = edit).
- Empty: one row "No trackers" white 50% plus the Add tracker button (desktop) / Plus (phone). No explanatory copy.

### Add tracker: type picker
- Desktop: modal 480px, #15102a + glass, radius 20px, title "Add tracker" 18px/600, close X circle top-right. Phone: pushed page in the sheet, title "Add tracker".
- Plain rows 56px: type name 16px/500 (Torznab, Newznab, later definitions alphabetically), ChevronRight white 30%. If the list exceeds 8, add a search field (40px, glass, Search icon, placeholder "Search").
- Picking a type opens the form.

### Tracker form (add and edit)
- Desktop: replaces the list in the content column, back link "Trackers" with ChevronLeft above title (title = tracker name, or type name when new). Phone: pushed page, circular back, centered title.
- Top row: "Enabled" label + switch (default on).
- Fields, stacked, 16px gap: label 13px/600 white 70% 6px above a 44px input (glass bg rgba(255,255,255,.06), 1px white 12% border, radius 10px, 15px text, focus border #3b82f6 + 3px rgba(59,130,246,.25) ring). Fields per type schema; typical: Name, URL, API key (masked, eye toggle), Categories (multi-select chips, selected = rgba(59,130,246,.18) + #60a5fa text), Priority (number). Field errors: border #ef4444, 13px #f87171 one-line message under the field ("Required", "Invalid URL").
- Actions row, 24px above, left to right: "Test" secondary (glass, white 95%), "Save" primary blue; "Delete" danger text button far right (edit only). Phone: full-width stacked, Save first, then Test, Delete as a plain red row at the bottom separated by 24px.
- Test: button shows a spinner while running, then inline status beside/under it for 4s: green Check "Connected" or red X with the server's short error ("Unauthorized", "Timed out"). No toasts for Test.
- Save: disabled until valid and changed; on success go back to the list with the top toast "Saved". Unsaved changes + back = confirm dialog "Discard changes?" with Discard (red) / Keep editing.
- Delete: confirm dialog "Delete <name>?" with Delete (red #ef4444) and Cancel; on success back to the list.
- Dialogs: 400px desktop / bottom sheet on phone, #15102a + glass, radius 20px, buttons 44px.
