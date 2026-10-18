# Luma: TV series edit, monitor on add, quality profiles (Designer spec, 2026-10-18, v158, desktop only)

Tokens: navy #15102a. Glass .06 with a 1px white 8% border and 24px blur. Primary #3b82f6 (hover #60a5fa), red #ef4444. Text is white at 95/70/50%. Sentence case, American English, no helper or explanatory text under or beside fields, nothing pinned to the bottom of the screen.

## Shared modal frame
- Scrim rgba(10,8,24,.6). The modal is 560px wide and centered vertically and horizontally, glass .08 at 40px blur, 16px corners, 24px padding.
- Header: the title (18/600) on the left and a 36px glass close (x) on the right. 20px below it, the fields start.
- Field rows use a 2-column grid: label column 160px (14/500, white 70%, right-aligned, vertically centered on the control), 16px gap, then the control fills the rest. 16px between rows.
- Controls:
  - Select and text fields: 40px, glass .06, 10px corners, 14px text, 12px padding, chevron at 16px white 50%. Focus is a 1px #3b82f6 border with a 3px accent 25% ring.
  - Toggle (replaces Sonarr's checkbox and its sentence): 40×22 switch, on = #3b82f6, off = white 15%. No text next to it.
  - Dropdown menus are a glass .10 popover, 8px corners, 36px rows, with the selected row in accent text and a check.
- Footer row: 24px under the last field, separated by a 1px white 8% line with 16px above it. Delete (when present) is on the left, Cancel (glass) and Save (primary) on the right, all 40px tall. Save is at 40% opacity and disabled until something changes.
- Errors: "Couldn't save" (13px #ef4444) on the left of the footer, with nothing else added.

## 1. Edit series modal (opened from a Series poster)
- Title: "Edit The Reluctant Vampire". No dash.
- Rows, in order:
  - Monitored: toggle.
  - Monitor new seasons: select (All seasons, None).
  - Season folders: toggle.
  - Quality profile: select of the TV profiles.
  - Series type: select (Standard, Daily, Anime). Plain options; leave out Sonarr's grey example text.
  - Path: a text field with a 40px glass folder icon button joined to its right edge (shared 10px outer corners). The button opens the existing path picker. Drop Sonarr's second button.
  - Tags: leave this out for v158 unless the backend uses it.
- Delete: a glass button with #ef4444 text and a red 30% border. Clicking it swaps the footer to "Delete The Reluctant Vampire?" (14/500) on the left, a "Delete files too" toggle, and Cancel plus a solid #ef4444 "Delete" on the right. There's no second modal.

## 2. Monitor options on Add (search results, path picker popover)
- The popover grows to 360px. Above the path radios, add a "Monitor" label (12/600, white 50%) and a full-width 40px select: All episodes (default), Future episodes, Missing episodes, First season, Latest season, None.
- With a single path there's normally no popover. Once Monitor exists, Add opens the popover even with one path, showing Monitor plus the path line (no radio) and Cancel/Add. That keeps one flow.
- The quality profile select sits under Monitor too, defaulting to the default profile.

## 3. TV quality profiles (Options tab)
- Section "Quality profiles" (15/600), with a glass "Add profile" button (36px, + icon) right-aligned on the same row.
- One glass card of rows, each 56px: the profile name (14/600), a "Default" chip (22px glass pill, accent text) on the default one, and on the right the cutoff, e.g. "Up to 1080p" (13px, white 50%), then a chevron. Clicking a row opens the editor modal.
- Empty: "No quality profiles" centered in a 120px glass card.

### Profile editor modal (same frame, 560px)
- Title: "New profile" or the profile name.
- Rows:
  - Name: text field.
  - Upgrades: toggle.
  - Upgrade until: select of the enabled qualities. It's hidden when Upgrades is off; the space closes with no jump of other content, just a 150ms height ease.
  - Default: toggle.
  - Qualities: full width under the label column. A glass .04 list, 8px corners, rows 40px: a drag handle (6-dot, white 30%), a 18px checkbox (checked = #3b82f6 fill and white check), the quality name (14px; unchecked rows at white 50%), and the size on the right only if the backend has it. Order is top = most preferred. Dragging lifts the row to glass .12 with a soft shadow. Inner scroll with max-height 320px.
- Footer: Delete, Cancel, and Save. Delete is hidden (not disabled, no explanation) on the default profile and on any profile a show uses.
