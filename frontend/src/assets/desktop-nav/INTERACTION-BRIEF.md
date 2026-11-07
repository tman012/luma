# Approved Luma desktop navigation — interaction implementation brief

November7,2026 (EST). Requirements completing the selected static design, NOT additional owner-selected artwork variations. Static selection does not constitute an accessibility or behavior gate.

## Preserve selected appearance
Use the exact original five PNG illustrations. No redrawing, glyph substitution, new finish, baked state rings, screenshot-as-UI, or circular-button reinterpretation.
Native dock356×84px, radius26; each transparent icon canvas56×56px, gap12px,padding14px. At1440px viewport top-center x720,y52: box(542,10)–(898,94). Icon centers584/652/720/788/856,y52. Selected closeup is4× these native dimensions.
Keep logo/avatar unchanged. Integrate through the actual current desktop breakpoint and real header constraints; avoid logo/avatar collision without dropping destinations. Verify narrower desktop fit in implementation; don't invent a breakpoint. Mobile unchanged.
Genuine dark translucent dock and backdrop blur: retain source material recipe and review against actual top backdrop. Selected bottom-context dark appearance is background-dependent; same tints may read lighter over bright hero. Do not bake screenshot pixels into a UI background. See ASSET-HANDOFF.md for exact original styling.

## Semantics and destinations
Five individual controls, in visual order: Search, Libraries, Explore, Calendar, Watchlist. Each has the matching accessible name; decorative raster artwork has empty alt when enclosing control supplies its name. No visible labels or helper text. Use actual existing app routes/actions; this brief intentionally specifies no route strings. Real links for destination navigation; real buttons for actions/search as appropriate to actual app behavior. Do not fabricate routes to match historical source notes.

## Keyboard, focus and states
- Tab order matches visual order. Standard native Enter activation for links/buttons; Space activates buttons. Do not add Space-to-navigate behavior to links.
- Visible focus-visible high-contrast ring outside the icon canvas, implemented in UI styling rather than baked artwork. Ensure ring isn't clipped by dock overflow and remains discernible against variable backdrop. No keyboard trap.
- Mark an actual current destination link with aria-current="page" and restrained under-icon dot/edge emphasis. For a genuinely applicable non-navigation toggle, use its correct native state semantics rather than falsely applying aria-current. Home must not falsely select one of the five destinations.
- Hover: subtle brightness or raised emphasis; no icon morph, large magnification, layout-shifting scaling or bounce. Pressed: reduced emphasis. Keep original artwork intact.
- Use brief existing motion tokens only, not a new animation system. Respect prefers-reduced-motion: no transform animation; retain immediate non-motion state/focus cues.
- Pointer hit area≥44px, accommodated within56px canvas. Disabled styling/semantics only when actual app functionality is disabled, not invented for appearance.

## Implementation verification still required
Actual desktop header fit, correct routing/actions, tab/activation, focus visibility, aria-current accuracy, contrast across backdrops, reduced motion and pointer sizing require working-UI testing. This static reference/package does not certify them. No mobile or release authorization implied.
