# Luma: Settings > TV, with Calendar, Search and Show detail (Designer spec, 2026-10-17)

Rules: navy #15102a, glass cards (rgba(255,255,255,.06), 1px white 8% border), primary #3b82f6 (hover #60a5fa), green #22c55e, red #ef4444, amber #f59e0b. American English, sentence case, no helper or explanatory text. Use the desktop Settings width (the TV panel is 1076px at 1440). Nothing is pinned to the bottom.

## 1. Page frame (Settings > TV)
- Title row:
  - "TV" title (18/600) on the left.
  - Right-aligned on desktop: a segmented control with **Calendar** (the default) | **Import** | **Libraries** | **Options**. Options holds Upgrades and Trackers.
  - Import shows an amber count badge when there are review rows.
  - Phone: the segmented control drops to a full-width row under search and scrolls sideways if it has to.
- Search field, 12px under the title row:
  - Full content width, 44px tall, glass, 12px corners, magnifier icon.
  - Placeholder "Search shows". A clear (x) appears once there's text.
- The view area starts 20px below the search field.
- The selected tab sits in the URL (`?view=calendar|import|libraries|options`), so Back works.

## 2. Calendar, desktop (content width ≥ 1000px)
- Toolbar:
  - Left: two 36px glass icon buttons, ‹ and ›, then a "Today" glass pill (36px, hidden on the current week).
  - Right: the week label "Oct 12 – 18" (15/600), or "Dec 28 – Jan 3" across months.
- Week grid:
  - 7 columns, Sunday first, 8px gaps.
  - Header: "Sun 12" (12/600, white 50%). Today's header is accent text with a 4px accent dot.
  - Columns are top-aligned. Each holds its episode cards stacked with 8px gaps.
  - Column height grows with the content. No inner scroll.
- Episode card (fills the column width, about 147px):
  - Glass, 10px corners, 8px padding, plus a 3px left status stripe (the same pattern as Activity).
  - 28×42 poster, or the TMDB logo on a glass tile when there's no poster.
  - Show name: 13/600, 1 line, truncated.
  - "S02E05 · Episode title": 12px, white 70%, at most 2 lines.
  - Air time "9:00 PM": 12px, white 50%.
  - Stripe and state:
    - Downloaded: green #22c55e.
    - Missing (aired, no file): red #ef4444.
    - Upcoming: accent #3b82f6.
    - The state is also the card's aria-label.
    - A downloaded card's air time gets a 12px green check icon before it. That gives a visible cue that isn't color alone.
  - Hover: glass .10. Click opens Show detail at that season and episode.
- Upcoming (only on the current week):
  - Section label "Upcoming", 28px below the grid.
  - An agenda list for the next 3 weeks, in the phone agenda format (§3).
- Empty week: a centered "No episodes this week" (15/500, white 70%) inside a 160px-tall glass card where the grid would be.
- Loading: skeleton cards at glass .04, 3 per column, with a soft pulse. No spinner.

## 3. Calendar, phone (and any content width < 1000px)
- Toolbar:
  - ‹ and › (44px), with the week label centered between them.
  - "Today" pill on the right when off the current week.
- Agenda grouped by day, with empty days omitted:
  - Day header: "Today", "Tomorrow", then "Mon, Oct 20" (13/600, white 70%). It's sticky under the page header while scrolling.
  - Rows in one glass card per day, 72px tall, 12px padding:
    - 40×60 poster.
    - Show name: 14/600, truncated.
    - "S02E05 · Title": 13px, white 70%, 1 line.
    - Right: time (13px, white 50%), and under it the state word at 12px/600 in its color: "Downloaded", "Missing" or "Upcoming".
    - The 3px status stripe stays on the left.
- The week view's list is followed by Upcoming (the next 3 weeks), the same as on desktop.
- Empty week: same as desktop.

## 4. Search
- Typing (250ms debounce, 2+ characters) swaps the view area for results, and the segmented control dims to 40%. Clearing the field (x or Esc) brings back the previous view.
- One list in a glass card, library matches first, then TMDB results, with no duplicates:
  - Row, 72px:
    - 40×60 poster.
    - "Title (Year)": 14/600.
    - "5 seasons": 13px, white 50%.
  - Library rows: an "In library" chip (12/600, 22px glass pill, 6px green dot) and a chevron. Clicking opens Show detail.
  - Other rows: an "Add" button on the right, 32px desktop / 40px phone, #3b82f6 primary.
    - Add with exactly one TV library path: adds right away. The button turns into a spinner, then an "In library" chip.
    - Add with 2+ paths:
      - Desktop: a 320px popover. Phone: a bottom sheet.
      - Contents: label "Add to", a radio list of the selected TV library paths (name 14/500, path 12px white 50%), then a centered block with "Cancel" (glass) and "Add" (primary).
      - Nothing is preselected, and Add stays at 40% until a path is picked.
    - Add failure: an inline 13px #ef4444 "Couldn't add show" under the row, with a Retry pill.
- Loading: 3 skeleton rows. No results: "No results" (15/500, white 70%) centered in the card.

## 5. Show detail (a sub-view inside Settings > TV)
- Back: desktop "‹ TV" as a text button above the header. Phone: the existing 40px glass back button in the header bar. It returns to the previous view (calendar or search).
- Header:
  - Desktop: a 120×180 poster on the left. On the right:
    - Title: 24/600.
    - "2008 · AMC · Ended": 13px, white 50%.
    - "62 of 62 episodes": 13px, white 70%.
    - "Wrong show?" as an accent text action.
  - Phone: an 80×120 poster with the same text stack beside it.
- Seasons:
  - A horizontal chip row: "Season 1" … and "Specials" last. 32px glass chips, with the active one #3b82f6.
  - It opens on the season of the clicked episode, or else the latest season.
- Episode list in a glass card, rows 56px:
  - "E05" (13/600, white 50%, 40px column).
  - Title: 14/500, truncated.
  - Air date "Oct 20, 2026": 13px, white 50%.
  - Right: the state word at 12/600 in its color, plus the 3px stripe.
  - The deep-linked episode gets a 1.6s glass .12 highlight that fades out.
- 404 / failed load: a centered "Couldn't load show" with a glass Retry. No grey modal and no #0a0a0a.

## 6. Secondary views
- **Import**: exactly the Import, Review and progress spec already sent (Import row, progress card, Review list, Import all).
- **Libraries**: the current Libraries selector. The no-paths state is a glass row "Library paths" with a chevron.
- **Options**: Upgrades (Replace / Keep both), then Trackers, unchanged.
- No TV library selected: Calendar and Search show a centered "No TV libraries" (15/500) with a primary "Choose libraries" button under it, opening the Libraries view.

## 7. Shots needed for the gate (390 and 1440, plus one 1024 check)
- Calendar: current week (mixed states), another week, empty week, loading, and Upcoming.
- Search: library and TMDB results, Add with one path, Add with the path picker, Add error, no results.
- Show detail: from a calendar card with the episode highlighted, plus a long title.
- The Import, Libraries and Options tabs; the No TV libraries state; the Import badge.
