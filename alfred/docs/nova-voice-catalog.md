# Nova Voice UX Design Doc — Full Question Space

Living reference for Nova/Alfred's intent catalog. Categories below map user
phrasings → voice response shape → screen response shape → backing endpoints →
likely follow-ups. [H] = already handled, [P] = partially handled, [N] = new.

---

## 1. Navigation Commands

**Phrasings**
- "Take me home." [H]
- "Home screen." [H]
- "Open Explore." [H]
- "Explore page." [H]
- "Pull up the library." [H]
- "Show me my watchlist." [P]
- "Open the Vibe Engine." [N]
- "Settings." / "Open settings." [H]
- "Admin panel." [N]
- "Daily." / "Daily kiosk." [N]
- "Go to Now Available." [N]
- "Browse page." [N]
- "Back." / "Go back." / "Previous screen." [P]
- "Close that." / "Dismiss." / "Get rid of this." [P]
- "Hide the modal." [N]
- "Yo Nova, take me to weather." [N]
- "Hey, sports page." [N]
- "Whatcha got under explore again." [N]
- "Switch to TV." (in search modal) [N]
- "Jump to people." (search tab) [N]

**Voice**: Brief confirm — "Opening Explore." For destructive moves, one-syllable ack.
**Screen**: Route change via SPA navigation. For "back" with ambiguity, breadcrumb pill.
**Endpoints**: Frontend router only — no backend hit.
**Follow-ups**: "scroll to trending," "what's the second rail," "show me the Trakt one."

---

## 2. Library Ownership — "What Do I Have"

**Phrasings**
- "Do I have Dune Part Two?" [H]
- "Is Oppenheimer in my library?" [H]
- "Got Sicario?" [H]
- "Do we own The Bear?" [H]
- "Is the new Mission Impossible on the Plex?" [H]
- "Do I have any Wes Anderson?" [N]
- "How many Marvel movies do we have?" [N]
- "All the Bond films I own." [N]
- "Do I have all the Lord of the Rings?" [N]
- "Is season 3 of Severance in there?" [P]
- "Do I have the extended cut of Lord of the Rings?" [N]
- "Do we have any A24 stuff?" [N]
- "Show me everything from 2024 in my library." [N]
- "Anything with Florence Pugh?" [P]
- "How many movies total." [H]
- "Library count by genre." [N]
- "Tell me what's in horror." [N]

**Voice**: Single-title: "Yes, you've got Dune Part Two in 4K REMUX from May." Counts: "You have 1,847 movies and 312 shows." Lists: "I found 12 — want me to show them?"
**Screen**: Single hit → MediaDetailModal. Multi-hit → mini-rail overlay. Counts → badge card.
**Endpoints**: `/api/check-ownership`, `/api/check-ownership-batch`, `/api/library/search/fast`, `/api/library/browse`, `/api/library/stats`.
**Follow-ups**: "Play it." "What quality." "When did I add it." "Got the prequel too?"

---

## 3. Recently Added / What's Fresh

**Phrasings**
- "What's new." [N]
- "Anything new on Plex?" [N]
- "What got added today." [N]
- "What was added this week." [N]
- "Show me the last week of additions." [N]
- "What dropped overnight." [N]
- "Anything fresh in 4K?" [N]
- "What's new in TV." [N]
- "New shows this month." [N]
- "Latest movie added." [N]
- "What did Alfred grab last night." [N]
- "Anything good come in this week." [N]
- "Hey, fresh stuff." [N]
- "What hit the library since Friday." [N]
- "Newest movie." [N]
- "What's the most recent thing I added." [N]

**Voice**: "Six new movies this week — Furiosa, The Substance, Civil War…" Read 3 max. "Want the rest?"
**Screen**: Horizontal rail with addedAt badges, sorted desc.
**Endpoints**: `/api/library/recent`, `/api/library/recent/cached`, `/api/hero/content`.
**Follow-ups**: "Play the first one." "Tell me about Furiosa." "Why is that one in 4K and the other isn't." "Add it to the watchlist."

---

## 4. Quality-Scoped Queries

**Phrasings**
- "What 4K movies do I have." [N]
- "Any UHD this week." [N]
- "Show me the REMUXes." [N]
- "1080p REMUX of Heat?" [N]
- "Best version of Inception I own." [N]
- "Do I have Dune in HDR." [N] (HDR not in current data — gap)
- "Show me the h265 stuff." [N]
- "Anything in Dolby Vision." [N] (gap)
- "What's the best quality copy of Blade Runner I have." [N]
- "All my 4K horror." [N]
- "How many 4K movies total." [N]
- "What's the biggest file in my library." [N]
- "Do I have a Blu-ray rip of John Wick." [N]
- "Anything still in 720p I should upgrade." [N]

**Voice**: "Your 4K shelf has 184 movies. Want them sorted by added date or rating?"
**Screen**: Filtered library grid with quality badges.
**Endpoints**: `/api/library/browse` (needs resolution/codec filter), `/api/check-ownership`, `/api/plex/movie/{rk}`.
**Follow-ups**: "Just the REMUXes." "Filter to last year." "Upgrade the 720p ones." "Show file sizes."

---

## 5. What's Hot / Trending / Popular

**Phrasings**
- "What's trending." [N]
- "Popular movies right now." [N]
- "What's everyone watching." [N]
- "Trending shows this week." [N]
- "Top 10 right now." [N]
- "What's hot on Trakt." [N]
- "Most-watched show this week." [N]
- "Buzz of the week." [N]
- "What's blowing up." [N]
- "What did everyone watch last weekend." [N]
- "Trending TV." [N]
- "Trending movies but nothing I already own." [N]

**Voice**: "Trending today: Civil War, Dune Two, The Acolyte." Brief.
**Screen**: Open Explore with trending rail focused, OR overlay rail.
**Endpoints**: `/api/trending?type&window`, `/api/popular`, `/api/trakt/shows/trending`, `/api/trakt/movies/trending`.
**Follow-ups**: "Do I have any of those." "Add the top one to my watchlist." "Show just movies." "What about last week."

---

## 6. What's Available to Download — Indexer Sweep

**Phrasings**
- "What's available to grab right now." [N]
- "What 4K releases have the most seeders." [N]
- "Best seeded REMUXes today." [N]
- "What did PHD drop today." [N]
- "Anything new on BHD." [N]
- "Top releases this week by seeders." [N]
- "Show me what came out on the trackers." [N]
- "Newest WEB-DLs." [N]
- "Got any 1080p BluRays available." [N]
- "What can I download tonight." [N]
- "Top of explore." [N]
- "Anything in 4K I don't already own." [N]
- "Search the trackers for Furiosa." [H]
- "Find Sicario on PHD." [N]

**Voice**: "Top 4K release today is Furiosa REMUX with 412 seeders on PHD. Want to grab it?"
**Screen**: Explore "From Your Trackers" rail with sort; or focused list with seeders/size/tracker columns.
**Endpoints**: `/api/explore/cached`, `/api/explore/top?quality&sort&limit&min_seeders&tracker`, `/api/indexer/browse`.
**Follow-ups**: "Grab it." "Just from PHD." "Filter to over 100 seeders." "Show me the next page."

---

## 7. In Theaters / Upcoming Releases

**Phrasings**
- "What's in theaters." [N]
- "What's playing this weekend." [N]
- "Movies out right now." [N]
- "What just came out." [N]
- "Upcoming releases." [N]
- "What hits theaters next week." [N]
- "When does Dune 3 come out." [N]
- "Movies coming this month." [N]
- "What should I see in theaters." [N]
- "Add the new Wes Anderson to my watchlist for when it streams." [N]

**Voice**: "In theaters now: Civil War, Furiosa, Kingdom of the Planet of the Apes…"
**Screen**: In-Theaters rail or grid with TMDb posters and release dates.
**Endpoints**: `/api/in-theaters`, `/api/tmdb/discover`, `/api/movie/{id}`.
**Follow-ups**: "Tell me about Furiosa." "Add it to the watchlist." "When can I download it." "Trailer."

---

## 8. Continue Watching / Resume / Watch History

**Phrasings**
- "Resume." [H]
- "Where did I leave off." [H]
- "Continue watching." [H]
- "Pick up where I left off." [H]
- "What was I watching last night." [H]
- "What episode am I on of Severance." [P]
- "How far am I in Dune." [H]
- "What did I finish last." [H]
- "Did I finish The Bear season 2." [N]
- "Show me my history." [N]
- "What have I watched this week." [N]
- "What's halfway done." [N]
- "Anything I started but didn't finish." [N]
- "Recent stuff I watched." [N]

**Voice**: "You're 47 minutes into Dune Part Two. Want to keep going?"
**Screen**: Continue Watching rail, or single resume card with progress bar.
**Endpoints**: `/api/plex/continue_watching`, `/api/plex/resume_info/{rk}`, `/api/plex/sessions/active`.
**Follow-ups**: "Play it in the living room." "Restart from the beginning." "What's next in the show." "Subtitles on."

---

## 9. Watchlist / Monitoring / Radarr+Sonarr

**Phrasings**
- "Add Furiosa to my watchlist." [N]
- "Put The Substance on my list." [N]
- "Watchlist The Bear season 4." [N]
- "Is Civil War on my watchlist." [N]
- "What's on my watchlist." [N]
- "Remove Argylle from the watchlist." [N]
- "Am I monitoring The Acolyte in Sonarr." [P]
- "Why isn't Severance downloading." [N]
- "What's Radarr looking for." [N]
- "How many movies is Radarr watching." [N]
- "Move Dune to higher quality profile." [N]
- "Track this for me." [N]
- "Don't bother monitoring it." [N]

**Voice**: "Added Furiosa to your watchlist. Want me to also have Radarr grab it when a 4K release shows up?"
**Screen**: Watchlist page or modal-level toggle update; Sonarr status badge.
**Endpoints**: `/api/watchlist`, `/api/watchlist/{id}/check`, `/api/radarr/watchlist/tmdb-ids`, `/api/sonarr/status/{tvdb}`, `/api/sonarr/add`.
**Follow-ups**: "What quality profile." "Set to 4K REMUX." "Show watchlist movies that are now available." "Clean up old ones."

---

## 10. TV-Specific Queries

**Phrasings**
- "What's airing tonight." [N]
- "What's on my Sonarr calendar this week." [N]
- "When's the next episode of Severance." [N]
- "When does The Last of Us return." [N]
- "Do I have all of season 2 of Andor." [P]
- "Which episodes of The Bear am I missing." [N]
- "Did the new Severance episode drop." [N]
- "How many episodes of Shogun do I have." [N]
- "Latest episode of Slow Horses." [N]
- "What aired last night." [N]
- "Show me the Sonarr calendar." [N]
- "Grab the latest episode of The Acolyte." [H]
- "Search for episodes of House of the Dragon." [H]
- "Mark season 1 monitored." [N]
- "Unmonitor everything before season 5." [N]

**Voice**: "Tonight at 9pm: The Acolyte episode 6 on Disney+. Sonarr is set to grab it."
**Screen**: TvDetailModal opened to season; or calendar overlay.
**Endpoints**: `/api/sonarr/calendar`, `/api/plex/tv/{rk}/seasons`, `/api/library/episodes`, `/api/sonarr/episode/search`, `/api/sonarr/season/search`.
**Follow-ups**: "Tell me when it's downloaded." "Play the previous episode." "Add it to the watchlist for tomorrow." "Show me the season summary."

---

## 11. Vibe Engine / Mood-Based

**Phrasings**
- "I want something cozy." [N]
- "Put on something dark." [N]
- "I need a laugh." [N]
- "Something mind-bending." [N]
- "Edge of my seat please." [N]
- "Easy background watch." [N]
- "Something I can fall asleep to." [N]
- "Pick one for me." [N]
- "Surprise me." [N]
- "I'm bored, pick something." [N]
- "Roulette mode." [N]
- "I want a throwback." [N]
- "Big adventure tonight." [N]
- "Something short." [N]
- "Nothing too heavy." [N]
- "I'm in a weird mood." [N]

**Voice**: Specific mood: "Cozy picks tonight: Paddington 2, About Time, The Holdovers." Surprise: "Trying… The Grand Budapest Hotel. Want it?"
**Screen**: Vibe Engine page with mood shelf focused, or Pick-For-Me roulette card.
**Endpoints**: `/api/library/vibe-engine`.
**Follow-ups**: "Next one." "Not that one, something else." "Filter to under 2 hours." "Play it."

---

## 12. Cast & Crew Queries

**Phrasings**
- "Movies with Tom Hanks." [N]
- "What else has Florence Pugh been in." [N]
- "Other movies by Denis Villeneuve." [N]
- "Show me everything by Christopher Nolan." [N]
- "Who directed this." [N]
- "Who's in this movie." [N]
- "Tom Cruise filmography." [N]
- "Did this director do anything else I have." [N]
- "What has Pedro Pascal done." [N]
- "Most-watched actor in my library." [N]

**Voice**: "Tom Hanks has 78 credits on TMDb. You own 12 of them. Want to see them?"
**Screen**: People tab in search OR dedicated person credits modal.
**Endpoints**: `/api/tmdb/person_by_id/{id}/credits`, `/api/tmdb/person/{actorId}/credits`, cross-ref `/api/check-ownership-batch`.
**Follow-ups**: "Just the ones I own." "Sort by rating." "Open Forrest Gump."

---

## 13. Ratings / Reviews / "Should I Watch"

**Phrasings**
- "Is Civil War any good." [N]
- "What's the Rotten Tomatoes score on Furiosa." [N]
- "IMDb rating for The Substance." [N]
- "Is Argylle worth watching." [N]
- "Should I watch Madame Web." [N]
- "What do critics say about Dune 2." [N]
- "Audience score on The Bear." [N]
- "Highest rated movie I haven't watched." [N]
- "Best reviewed thing I added this month." [N]
- "Read me a review." [N]
- "Metacritic on Oppenheimer." [N]

**Voice**: "Civil War: 81 on Rotten Tomatoes critic, 7.4 on IMDb. Critics liked it; audiences mixed."
**Screen**: MediaDetailModal scrolled to ratings + RT reviews panel.
**Endpoints**: `/api/plex/ratings/{rk}`, `/api/plex/ratings/resolve`, `/api/ratings/{title}`, `/api/imdb/votes`, `/api/rt/reviews`.
**Follow-ups**: "Show me reviews." "Play the trailer." "Add to watchlist." "Anything similar."

---

## 14. Storage / Downloads / Queue Status

**Phrasings**
- "How much storage is left." [H]
- "Disk space." [H]
- "What's downloading right now." [H]
- "Anything in the queue." [H]
- "What's in the Radarr queue." [H]
- "Did Furiosa finish downloading." [H]
- "How long until that's done." [N]
- "Cancel that download." [N]
- "What downloaded yesterday." [N]
- "Download history." [N]
- "Why is it stuck." [N]
- "How many torrents am I seeding." [N]

**Voice**: "Three downloading: Furiosa at 78%, The Bear S04E03 at 42%, Civil War queued."
**Screen**: Active downloads card / queue overlay with progress bars.
**Endpoints**: `/api/downloads/active`, `/api/downloads/history`, `/api/radarr/queue`.
**Follow-ups**: "Pause that one." "Remove the queued one." "Notify me when done."

---

## 15. Upgrades

**Phrasings**
- "Any 4K upgrades available." [N]
- "Upgrade Inception to REMUX." [N]
- "What can I upgrade." [N]
- "Show me upgrade candidates." [N]
- "Run an upgrade scan." [N]
- "Auto-grab upgrades." [N]
- "Top-rated stuff I should upgrade." [N]
- "Dismiss the upgrade for Argylle." [N]
- "Anything new in upgrades since last week." [N]
- "Upgrade my Marvel collection to 4K." [N]

**Voice**: "Twelve upgrades available, four are 4K REMUX bumps. Want them all or top-rated only?"
**Screen**: Upgrades list with before/after quality and seed counts.
**Endpoints**: `/api/upgrades/list`, `/api/upgrades/sync`, `/api/upgrades/batch`, `/api/upgrades/top-rated`, `/api/upgrades/auto-grab`, `/api/upgrades/new/scan`, `/api/upgrades/new/list`.
**Follow-ups**: "Grab the top three." "Skip Argylle." "Notify me when one finishes."

---

## 16. Playback Control

**Phrasings**
- "Play Furiosa." [H]
- "Play it in the living room." [H]
- "Pause." [H]
- "Resume." [H]
- "Stop." [N]
- "Skip ahead 30 seconds." [N]
- "Rewind 10." [N]
- "Subtitles on." [N]
- "Turn on captions." [N]
- "Switch audio to English." [N]
- "Volume up." [H]
- "Mute." [N]
- "Cast to the bedroom Shield." [N]
- "What's playing right now." [N]
- "Play next episode." [N]

**Voice**: "Playing Furiosa in the living room." Or silent ack with on-screen toast.
**Screen**: Player overlay or session card.
**Endpoints**: `/api/player/play`, `/api/plex/playback/control`, `/api/plex/playback/seek`, `/api/plex/playback/subtitles`, `/api/plex/clients`.
**Follow-ups**: "Bigger subtitles." "Skip the intro." "Pause and dim the lights."

---

## 17. Daily Kiosk Widgets — Weather / Sports / News

**Weather**
- "What's the weather." [H]
- "Forecast for tomorrow." [N]
- "Is it gonna rain." [N]
- "Hourly forecast." [N]
- "How cold tonight." [N]

**Sports**
- "Score of the game." [H]
- "Who's winning." [H]
- "Cubs score." [H]
- "When's the next Bears game." [H]
- "Who's playing tonight." [H]
- "What happened in the game." [H]
- "Bottom of the ninth?" [N]
- "Who's pitching." [N]
- "Did the Cubs win last night." [H]
- "When do the Bears play next." [H]
- "What's on the schedule this week." [N]

**News**
- "What's in the news." [N]
- "Top headlines." [N]
- "What's happening." [N]
- "Anything new today." [N] (disambiguate from recently added)

**Voice**: Sports: "Cubs 4, Brewers 2, top of the seventh, Cubs runner on second." Weather: "62 and partly cloudy, rain after 8."
**Screen**: Daily widget card pinned, or full Daily Kiosk view.
**Endpoints**: `/api/weather`, `/api/sports`, `/api/news`.
**Follow-ups**: "Tomorrow too." "Next inning update." "Open the full game." "Read me the top story."

---

## 18. Smart Home

**Phrasings**
- "Lights off." [H]
- "Living room lights to 30." [H]
- "Dim the kitchen." [H]
- "Fan on in the bedroom." [H]
- "Volume up." [H]
- "Movie mode." [N]
- "Turn everything off downstairs." [N]
- "What lights are on." [N]
- "What's the temperature in the kitchen." [N]
- "Pause and dim the lights." [N] (compound)

**Voice**: Brief ack — "Done."
**Screen**: SmartHomeModal flash, or toast.
**Endpoints**: `/api/ha/areas`, `/api/ha/area/{id}/entities`, `/api/ha/service`, `/api/room/{id}/remote`.
**Follow-ups**: "Brighter." "Now turn the fan on." "Save this as movie mode."

---

## 19. Shield/Kodi Activity

**Phrasings**
- "What's playing on the Shield." [N]
- "What's the TV doing." [N]
- "What did we watch last." [N]
- "Is the Shield idle." [N]
- "What app is open on the TV." [N]
- "Was the TV on last night." [N]

**Voice**: "Shield is on Plex playing Furiosa, 47 minutes in."
**Screen**: Activity card.
**Endpoints**: `/api/activity`, `/api/adb/shield_activity`, `/api/plex/sessions/active`.
**Follow-ups**: "Pause it." "Switch to Disney+."

---

## 20. Multi-User / Shared

**Phrasings**
- "What did Sarah watch last night." [N]
- "What's my wife into lately." [N]
- "Show me the kids' library." [N]
- "What's everyone been watching." [N]
- "Did anyone finish The Bear." [N]
- "Whose watchlist is Furiosa on." [N]

**Voice**: Caveat: "I can see active sessions but per-user history isn't wired yet."
**Screen**: Sessions list if available.
**Endpoints**: `/api/plex/sessions/active` (partial). Per-user history = gap.
**Follow-ups**: Once expanded, "show only mine," "filter to kids' content."

---

## 21. Settings / Admin

**Phrasings**
- "Test the Plex connection." [N]
- "Is Plex healthy." [N]
- "Switch the hero library to 4K Movies." [N]
- "Change the default quality profile to 4K REMUX." [N]
- "Turn on popularity boost." [N]
- "Restart the Alfred container." [N]
- "Clear the explore cache." [N]
- "Run an explore sweep now." [N]
- "Use Trakt for trending shows." [N]

**Voice**: "Plex is healthy, 4 libraries, 1,847 movies indexed."
**Screen**: Settings card with confirmation.
**Endpoints**: `/api/plex/health`, `/api/kiosk/settings`, `/api/alfred/reset`.
**Follow-ups**: "Save it." "Undo." "Show me the indexer settings."

---

## 22. Conversational / Disambiguation

**Phrasings**
- "No, not that one."
- "The other one."
- "The new one."
- "From last year."
- "The one with Pedro Pascal."
- "The original, not the remake."
- "Just the movies, not the show."
- "Yeah, that one."
- "Cancel that."
- "Wait, go back."
- "Not anymore, never mind."
- "Try again."
- "What did you say?"
- "Repeat."
- "Slower."
- "Skip that."

**Voice**: Crisp, low-latency. Repeat options as numbered: "I found three: 1. Dune 2021, 2. Dune 1984, 3. Dune Part Two."
**Screen**: Confirm-before-lookup 3-button UI (shipped). Extend to all multi-candidate flows.
**Endpoints**: `/api/alfred/command` with conversation state.
**Follow-ups**: All flows must support "no, the other one" within ~30s window.

---

## 23. Compound / Chained Queries

**Phrasings**
- "Show me 4K horror from the last month I haven't watched." [N]
- "Trending shows that are also on my watchlist." [N]
- "REMUXes added since Friday with a Rotten Tomatoes over 80." [N]
- "Wes Anderson movies I own in 1080p or better." [N]
- "Continue watching, but only TV." [N]
- "Movies under two hours, family-friendly, that nobody's watched yet." [N]
- "4K releases on PHD with over 200 seeders that aren't in my library." [N]
- "Find me a comedy under 100 minutes from the 90s." [N]
- "Show me everything from A24 in 4K REMUX." [N]
- "What got added this week and is rated above 8 on IMDb." [N]

**Voice**: Acknowledge filter chain: "Filtering 4K horror added in the last 28 days, unwatched — found 7."
**Screen**: Filtered grid with active filter chips at top.
**Endpoints**: Cross-cut — `/api/library/browse` + `/api/check-ownership-batch` + `/api/explore/top` + ratings.
**Follow-ups**: "Drop the rating filter." "Sort by added date." "Just the top one." "Play it."

---

## 24. Vague / Lazy / Fallback

**Phrasings**
- "Put something on."
- "Surprise me."
- "I'm bored."
- "What should I watch."
- "Pick one."
- "Just play something good."
- "Anything new and interesting."
- "Whatever."
- "You decide."
- "Random."
- "Background noise."
- "Something for dinner."
- "Something for date night."
- "Something for the kids."
- "Quick pick."

**Voice**: Confident commit: "Trying The Holdovers — cozy, two hours, you haven't watched it." Don't ask back-and-forth.
**Screen**: Single recommendation card with poster + Play CTA.
**Endpoints**: Vibe Engine "Pick For Me" mode + recently-added + ratings.
**Follow-ups**: "Next." "Not that, something funnier." "Yeah, play it." "Why this one."

---

## 25. Gaps Worth Supporting

**Library forensics**
- "What's the biggest file in my library." (fileSize)
- "Show me the longest movies I own." (duration)
- "Anything I've never watched." (viewCount=0)
- "Films I haven't touched in 3 years." (lastViewedAt)
- "Most-watched movie of the year."
- "Movies I've rewatched the most." (viewCount>1)
- "Hidden gems — high rated, low watched."

**Crew/production depth**
- "Most-watched director in my library."
- "Most-watched actor across all my watched titles."
- "All A24 / Studio Ghibli / Neon releases I own." (production_companies gap)
- "Movies in Spanish." (spoken_languages gap)
- "Letterboxd average for this." (gap)

**Money/box office**
- "How much did Furiosa make at the box office." (revenue)
- "What was the budget." (budget)
- "Most expensive movie I own."

**Indexer richness**
- "Show me all the REMUXes BTN seeded today." (per-tracker filter)
- "What's the average seeders on PHD right now." (analytics)
- "What got dropped in the last hour." (publishDate window)

**Ownership sub-states**
- "Is this in Plex, in Radarr, or just on the trackers."
- "Coming soon? When?"
- "Stuck in Sonarr import."

**Explainability**
- "Why did you pick this for me."
- "Why is this trending."
- "What's the catch."

**Notifications / proactive**
- "Tell me when Furiosa finishes downloading."
- "Alert me when the new Severance episode is grabbed."
- "Wake me if the Cubs are tied in the 9th."
- "Daily 8am brief."

**Cross-source sanity**
- "Is the show I'm watching going to have a new season."
- "What did I have on this exact day last year." (anniversary)

---

## Cross-Cutting Design Notes

**Wake-word forms**: "Hey Nova," "Yo Nova," "Alfred," "Hey Alfred," bare wake, middle-of-sentence. Classifier should ignore politeness words.

**Speech vs screen split**: Voice ≤ 2 sentences for lists with N>3. Screen shows the rest. Yes/no ownership → voice carries answer, screen confirms with thumbnail. Compound queries → voice narrates filter chain, screen shows items.

**Confirm-before-lookup pattern**: Extend to download_movie, download_tv, watchlist CRUD, playback target, upgrade grab, Sonarr add. Reuse 3-button UI.

**Context window**: Nova holds ~2 min of conversation. "The other one," "the new one," "play it," "add it," "open that" resolve against most recent surfaced item.

**Negative filter framing**: "but not horror," "nothing animated," "skip the kids' stuff" parse as exclusion modifiers.

**Time language**: "tonight" = today PM, "this weekend" = next Sat-Sun, "lately" = last 28d, "the other day" = last 7d, "a while back" = ask once.

**Tone**: Brief, confident, slightly dry. Don't over-confirm. Don't apologize unless something failed. When uncertain, ask one targeted question.

**Failure modes**: title not found, ambiguous title (3+ candidates), Plex offline (degrade to TMDb), Trakt disabled (silent skip), playback target offline, smart home device unreachable.
