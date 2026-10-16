"""
LLM-based intent classifier — replaces regex pattern matching.

Uses Claude's tool_use pattern to get structured JSON classification of voice
commands. Returns intent key(s) + extracted parameters that map directly to
existing handler functions.

For general questions (weather, trivia, etc.), the classifier also returns
a direct answer — avoiding a second LLM call through the fallback handler.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_API_URL = 'https://api.anthropic.com/v1/messages'
_MODEL = 'claude-haiku-4-5-20251001'

# -------------------------------------------------------------------------
# Data types
# -------------------------------------------------------------------------

@dataclass
class Intent:
    key: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassificationResult:
    intents: List[Intent]
    answer: Optional[str] = None   # for general_question — pre-computed TTS answer


# -------------------------------------------------------------------------
# Tool schema — forces structured output from Claude
# -------------------------------------------------------------------------

_CLASSIFY_TOOL = {
    'name': 'classify_intent',
    'description': 'Classify a voice command into one or more intents with extracted parameters.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'intents': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'intent': {
                            'type': 'string',
                            'enum': [
                                'lights_on', 'lights_off', 'lights_brightness',
                                'volume_set', 'volume_adjust',
                                'fan_on', 'fan_off',
                                'describe',
                                'show_media', 'navigate',
                                'ownership_check',
                                'score', 'next_game', 'event',
                                'play',
                                'time', 'date',
                                'storage', 'free_space', 'downloads', 'queue',
                                'download_movie', 'download_tv',
                                'watch_progress', 'watch_history',
                                'search_movie', 'search_tv',
                                # Phase 1: library discovery
                                'library_recent', 'library_quality', 'library_stats',
                                # Phase 1: watchlist CRUD
                                'watchlist_list', 'watchlist_check', 'watchlist_remove',
                                # Phase 1: indexer / trackers
                                'indexer_top',
                                # Phase 1: discovery feeds
                                'trending', 'popular', 'in_theaters',
                                # Phase 1: vibe / mood
                                'vibe_mood', 'vibe_pick_for_me',
                                # Phase 2: TV calendar / ownership
                                'tv_airing', 'tv_next_episode', 'tv_season_ownership',
                                # Phase 2: playback control
                                'playback_pause', 'playback_resume_session',
                                'playback_stop', 'playback_seek', 'playback_subtitles',
                                'playback_now_playing',
                                # Phase 2: continue watching
                                'continue_watching_list',
                                # Bosley Kitchen
                                'kitchen_grocery_add', 'kitchen_grocery_list',
                                'kitchen_grocery_remove', 'kitchen_grocery_check',
                                'kitchen_grocery_clear',
                                'kitchen_meal_today', 'kitchen_meal_week',
                                'kitchen_meal_plan_next_week', 'kitchen_meal_cooked',
                                'kitchen_pantry_out',
                                'kitchen_recipe_lookup',
                                'kitchen_recipe_to_grocery',
                                'general_question',
                            ],
                        },
                        'parameters': {
                            'type': 'object',
                            'description': 'Extracted parameters for the intent.',
                        },
                    },
                    'required': ['intent', 'parameters'],
                },
            },
            'answer': {
                'type': 'string',
                'description': (
                    'For general_question intent ONLY: a direct 1-2 sentence answer '
                    'suitable for text-to-speech. Just state the answer naturally.'
                ),
            },
        },
        'required': ['intents'],
    },
}


# NOTE: Web search is NOT used in the classifier — tool_choice forces
# classify_intent only. General questions are delegated to the fallback
# handler which has its own web search integration.


# -------------------------------------------------------------------------
# System prompt
# -------------------------------------------------------------------------

def _build_system_prompt() -> str:
    """Build the classification system prompt."""
    base = (
        'You are a voice command classifier for a home media/smart-home assistant called Alfred.\n'
        'Your job is to classify the user\'s spoken command into one or more intents '
        'and extract the relevant parameters.\n\n'

        'INTENT GUIDE:\n'
        '- lights_on: turn on lights. params: {room?: string, device?: string, brightness?: number}\n'
        '  When the user names a specific fixture within a room (e.g. "the ceiling fan light '
        'in the family room", "office desk lamp", "kitchen under cabinet lights"), split into '
        'room + device. The device is the fixture name as spoken, minus the room. Examples:\n'
        '      "turn on the family room ceiling fan light" → {room: "family room", device: "ceiling fan light"}\n'
        '      "turn on the office desk lamp"              → {room: "office", device: "desk lamp"}\n'
        '      "turn on the kitchen under cabinet lights"  → {room: "kitchen", device: "under cabinet"}\n'
        '      "turn on the family room lights"            → {room: "family room"}  (no device — whole area)\n'
        '      "turn on the ceiling fan light"             → {device: "ceiling fan light"}  (no room — global device search)\n'
        '- lights_off: turn off lights. Same params as lights_on. Also accepts device for '
        'targeted fixtures.\n'
        '- lights_brightness: set light brightness. params: {room?: string, device?: string, brightness: number (0-100)}\n'
        '- volume_set: set volume to a number. params: {level: number}\n'
        '- volume_adjust: turn volume up or down. params: {direction: "up" | "down"}\n'
        '- fan_on: turn on a fan. params: {room?: string, device?: string}\n'
        '- fan_off: turn off a fan. params: {room?: string, device?: string}\n'
        '- describe: user is describing a movie/show they can\'t name, want identified, or want '
        'recommendations for. This includes "what\'s that movie where...", "find me a movie about...", '
        '"I\'m looking for something with...", or any description of a plot, scene, actor combo, or vibe. '
        'params: {description: string} — pass the FULL user sentence as the description.\n'
        '- show_media: user wants to VIEW details about a specific movie, TV show, or person '
        '(open its info/detail page). This includes "show me X", "find X", "search for X", '
        '"pull up X", "look up X", "what about X" when X is a specific title or person name. '
        'Examples: "show me Cast Away", "search for The Expendables 2", "find Inception", '
        '"pull up Seinfeld", "what about Breaking Bad Season 3", "show me Matt Damon". '
        'params: {title: string, media_type?: "movie"|"tv", season?: number}\n'
        '- navigate: user wants to go to a page in the app. The available pages are:\n'
        '    "/" (home), "/explore" (latest movies, trending, new releases), '
        '"/library" (Luma AI / vibe engine — moods, Pick For Me), "/watchlist" (watchlist), '
        '"/now-available" (movies available now / downloads ready), '
        '"/browse" (browse full library), "/daily" (daily kiosk — weather/sports/news), '
        '"/settings" (settings).\n'
        '  Examples: "show me the latest movies" → /explore, '
        '"take me to my watchlist" → /watchlist, '
        '"go to the library" → /library, '
        '"open the vibe engine" → /library, '
        '"what\'s available" → /now-available, '
        '"show me new releases" → /explore, '
        '"browse my library" → /browse, '
        '"open the daily kiosk" → /daily, '
        '"take me to weather" → /daily.\n'
        '  NOTE: DO NOT use navigate for content queries like "what\'s new", "what\'s trending", '
        '"what 4K movies do I have" — those have their own intents (library_recent, trending, '
        'library_quality). navigate is ONLY for explicit page-change requests.\n'
        '  params: {path: string}\n'
        '- ownership_check: user asks if a specific movie/show is in their library, '
        'optionally in a specific quality. "do we have Inception", "do I have Dune in 4K". '
        'params: {title: string, quality?: string} — title is JUST the movie/show name '
        '(no quality words). quality is the spoken quality keyword if any '
        '(e.g., "4K", "UHD", "1080p", "HD", "Blu-ray", "remux", "full rip").\n'
        '- score: ANY question about whether a team plays or played TODAY, or the current/'
        'latest score. Use this ONLY when the user references today / tonight / right now / '
        'earlier today. For tomorrow, any weekday name, "the weekend", or "next week", use '
        'next_game with a day parameter instead — NEVER score. Examples:\n'
        '    "what\'s the Orioles score"           → score\n'
        '    "how are the Ravens doing"           → score\n'
        '    "do the Orioles play today"          → score\n'
        '    "are the Orioles playing tonight"    → score\n'
        '    "do they have a game today"          → score\n'
        '    "was there a game today"             → score\n'
        '    "did the Ravens win today"           → score\n'
        '    "do they play tomorrow"              → next_game (NOT score) — tomorrow ≠ today\n'
        '    "what about Saturday"                → next_game (NOT score)\n'
        '  params: {team: string}\n'
        '- next_game: user wants to know when a team plays NEXT without anchoring to today. '
        'Use this ONLY when the user has not mentioned "today", "tonight", "now", or "earlier". '
        'Also use this when the user names a specific future day — Saturday, Friday, tomorrow, '
        'the weekend, next Tuesday — in which case PASS the day as a parameter. Examples:\n'
        '    "when do the Orioles play next"        → next_game {team: "Orioles"}\n'
        '    "do they play Saturday"                → next_game {team: "...", day: "Saturday"}\n'
        '    "what about Saturday"                  → next_game {team: "...", day: "Saturday"}\n'
        '    "do they play tomorrow"                → next_game {team: "...", day: "tomorrow"}\n'
        '    "are they playing tomorrow night"      → next_game {team: "...", day: "tomorrow"}\n'
        '    "are the Ravens playing this weekend"  → next_game {team: "Ravens", day: "this weekend"}\n'
        '    "do the Lakers play tomorrow night"    → next_game {team: "Lakers", day: "tomorrow"}\n'
        '    "when\'s the next Ravens game"         → next_game {team: "Ravens"}\n'
        '  CRITICAL: Whenever the user says "tomorrow", "Saturday", "Friday", or any other '
        'day word, you MUST include the `day` parameter. Do not drop it. The handler uses '
        '`day` to answer the actual question ("do they play Saturday?") correctly.\n'
        '  The `day` param is optional — omit it when the user asks the generic "when next". '
        'When the user uses a follow-up like "what about Friday" resolve the team from prior '
        'conversation context.\n'
        '  params: {team: string, day?: string}\n'
        '- event: user asks about a sports event (Final Four, Super Bowl, etc). '
        'params: {event_name: string}\n'
        '- play: user wants to play/watch something on their media system. '
        'params: {title: string, room?: string, quality?: string}\n'
        '- time: user asks what time it is. params: {}\n'
        '- date: user asks what today\'s date is. params: {}\n'
        '- storage: user asks about disk space / storage. params: {}\n'
        '- free_space: user wants to free up space / clean up downloads / remove old IPTorrents files. params: {}\n'
        '- downloads: user asks what\'s currently downloading. params: {}\n'
        '- queue: user asks about stuck/queued downloads. params: {}\n'
        '- download_movie: user wants to add a movie to the watchlist, download it, or grab it. '
        'Triggered by phrases like "add [title]", "download [title]", "get [title]", '
        '"can you add [title]", "put [title] on the watchlist", "grab [title]". '
        'Use this for MOVIES or when the media type is unclear. '
        'params: {title: string, quality?: string, year?: number, prefer_newest?: boolean}\n'
        '  - title: JUST the bare movie name — strip quality words AND version qualifiers '
        '("new", "latest", "newest", "recent", "most recent", "old", "original", "remake", "reboot").\n'
        '  - quality: spoken quality keyword if any (e.g. "4K", "UHD", "1080p", "remux", "full rip", "blu-ray").\n'
        '  - year: specific year if the user names one ("the 2019 version" → year: 2019). Omit otherwise.\n'
        '  - prefer_newest: set true when the user says "new", "latest", "newest", "recent", '
        '"most recent", "new version", or "remake/reboot" — signals to pick the most recent TMDb match.\n'
        '  Examples: "add the new little women movie" → {title: "little women", prefer_newest: true}\n'
        '            "add the latest halloween movie" → {title: "halloween", prefer_newest: true}\n'
        '            "add the original tron" → {title: "tron", prefer_newest: false} (or omit)\n'
        '            "add the 2019 little women" → {title: "little women", year: 2019}\n'
        '- download_tv: user wants to add a TV show to the watchlist, download it, or grab it. '
        'Triggered by the same phrases as download_movie but when the title is clearly a TV show, '
        'or user mentions "show", "series", "season", "episode". '
        'params: {title: string, season?: number, episode?: number, quality?: string, prefer_newest?: boolean} — '
        'same quality and prefer_newest extraction as download_movie.\n'
        '- watch_progress: user asks what episode they are on, where they left off, or how far '
        'into a movie/show they are. This checks the Continue Watching list.\n'
        '    "what episode of Chosen are we on"     → watch_progress {title: "Chosen"}\n'
        '    "where did I leave off on The Office"  → watch_progress {title: "The Office"}\n'
        '    "how far am I into Cast Away"          → watch_progress {title: "Cast Away"}\n'
        '  params: {title: string}\n'
        '- watch_history: user asks what they have watched recently, yesterday, last night, '
        'this week, etc. Returns a summary of recently played movies and shows from Plex.\n'
        '    "what did I watch yesterday"            → watch_history {period: "yesterday"}\n'
        '    "what did I watch last night"           → watch_history {period: "last night"}\n'
        '    "what movies did I watch recently"      → watch_history {media_type: "movie"}\n'
        '    "show me my watch history"              → watch_history {}\n'
        '    "what shows have I been watching"       → watch_history {media_type: "show"}\n'
        '    "what did we watch this week"           → watch_history {period: "this week"}\n'
        '  params: {period?: string, media_type?: "movie"|"show"}\n'
        '- search_movie: user wants to search torrent trackers/indexers for a movie (NOT for looking up a movie). '
        'Only use this if user explicitly mentions trackers, indexers, or torrents. params: {query: string}\n'
        '- search_tv: user wants to search for a TV show in Sonarr. params: {query: string}\n'
        '\n'
        'LIBRARY DISCOVERY INTENTS (questions about what is in my Plex library):\n'
        '- library_recent: user asks what was recently added to the library. '
        'Examples: "what\'s new", "anything new on Plex", "what got added this week", '
        '"what dropped overnight", "latest movies added", "new shows this month", '
        '"anything fresh", "what hit the library since Friday", "newest movie", '
        '"anything good come in this week". '
        'params: {media_type?: "movie"|"tv"|"all", quality?: string, period?: string} — '
        'media_type defaults to all. quality is spoken quality ("4K", "1080p", "REMUX") if '
        'mentioned. period is spoken time reference ("today", "this week", "overnight", '
        '"since Friday", "this month") — the handler interprets it.\n'
        '- library_quality: user asks about library items filtered by quality/format. '
        'Examples: "what 4K movies do I have", "show me the REMUXes", "all my 4K horror", '
        '"what\'s the biggest file in my library", "anything still in 720p I should upgrade", '
        '"how many 4K movies total", "show me the h265 stuff". '
        'params: {quality: string, media_type?: "movie"|"tv", genre?: string, sort?: "size"|"added"|"rating"} — '
        'quality is the primary filter. genre is optional secondary filter.\n'
        '- library_stats: user asks for counts/totals about the library — total movies, total '
        'shows, counts by genre, counts by year. Examples: "how many movies total" (but NOT '
        '"how much storage" — that\'s storage), "library count by genre", "tell me what\'s in '
        'horror", "how many shows do I have". '
        'params: {group_by?: "genre"|"year"|"media_type", filter?: string}\n'
        '\n'
        'WATCHLIST CRUD INTENTS (user\'s saved watchlist — auto-monitored for download):\n'
        '- watchlist_list: user asks what is on their watchlist. "what\'s on my watchlist", '
        '"show me my watchlist", "read my watchlist", "how many movies am I tracking". '
        'params: {}\n'
        '- watchlist_check: user asks if a specific title is on their watchlist. '
        '"is Furiosa on my watchlist", "am I tracking The Substance", "do I have Dune in my '
        'watchlist". params: {title: string}\n'
        '- watchlist_remove: user wants to remove a specific title from their watchlist. '
        '"remove Argylle from the watchlist", "take Furiosa off my watchlist", "I don\'t want '
        'Dune anymore". params: {title: string}\n'
        '\n'
        'INDEXER / TRACKER INTENTS (torrents available to grab right now):\n'
        '- indexer_top: user asks what releases are available on trackers (Prowlarr sweep '
        'results), optionally filtered by quality or tracker. Examples: "what\'s available to '
        'grab right now", "what 4K releases have the most seeders", "best seeded REMUXes today", '
        '"what did PHD drop today", "anything new on BHD", "top releases this week", "show me '
        'what came out on the trackers", "what can I download tonight", "top of explore". '
        'params: {quality?: string, tracker?: string, sort?: "seeders"|"date", min_seeders?: number, limit?: number} — '
        'quality is normalized ("4k", "1080p", "remux", "webdl"). tracker is normalized '
        '(PHD, BHD, IPT, BTN, etc).\n'
        '\n'
        'DISCOVERY FEED INTENTS (external trending/popular from TMDb & Trakt):\n'
        '- trending: user asks what is trending NOW across the internet (TMDb/Trakt). '
        'Examples: "what\'s trending", "trending shows this week", "what\'s hot on Trakt", '
        '"most-watched show this week", "buzz of the week", "what\'s blowing up", "trending TV". '
        'NOTE: "what\'s new in my library" → library_recent, NOT trending. '
        'params: {media_type?: "movie"|"tv", window?: "day"|"week"}\n'
        '- popular: user asks what\'s popular right now on TMDb. Examples: "popular movies '
        'right now", "what\'s popular", "top 10 right now". '
        'params: {media_type?: "movie"|"tv"}\n'
        '- in_theaters: user asks what\'s in theaters or playing this weekend in cinemas. '
        'Examples: "what\'s in theaters", "what\'s playing this weekend" (cinema, not Plex), '
        '"movies out right now in theaters", "what just came out in theaters", "upcoming '
        'releases", "what hits theaters next week". params: {}\n'
        '\n'
        'VIBE / MOOD INTENTS (mood-based library recommendations):\n'
        '- vibe_mood: user expresses a mood and wants a matching pick from the library. '
        'Available moods: cozy, dark, mind-bending, adventure, funny, throwback, edge-of-seat, easy. '
        'Examples: "I want something cozy" → mood: "cozy". "Put on something dark" → "dark". '
        '"I need a laugh" → "funny". "Something mind-bending" → "mind-bending". "Edge of my '
        'seat please" → "edge-of-seat". "Easy background watch" → "easy". "Something I can '
        'fall asleep to" → "easy". "I want a throwback" → "throwback". "Big adventure tonight" '
        '→ "adventure". "Nothing too heavy" → "easy". '
        'params: {mood: string} — must be one of the 8 moods above.\n'
        '- vibe_pick_for_me: user wants Nova to just pick something for them (no specific mood). '
        'Examples: "pick one for me", "surprise me", "I\'m bored, pick something", "roulette '
        'mode", "put something on", "just play something good", "you decide", "whatever", '
        '"random", "quick pick", "I\'m in a weird mood". params: {}\n'
        '\n'
        'TV CALENDAR / OWNERSHIP INTENTS:\n'
        '- tv_airing: user asks what episodes are airing soon, airing tonight, aired last '
        'night, or on the upcoming calendar. Examples: "what\'s airing tonight", "what TV is '
        'on tonight", "what\'s on my calendar this week", "what aired last night", "any new '
        'episodes tonight", "show me the Sonarr calendar", "what\'s airing tomorrow". '
        'params: {period?: string} — tonight, tomorrow, this week, last night, etc.\n'
        '- tv_next_episode: user asks when the next episode of a specific show is, or when '
        'a show returns. Examples: "when\'s the next episode of Severance", "when does The '
        'Last of Us return", "when\'s the new episode of House of the Dragon coming". '
        'params: {title: string}\n'
        '- tv_season_ownership: user asks whether they have a whole season, which episodes '
        'they are missing, or how many episodes they have of a show. Examples: "do I have '
        'all of season 2 of Andor", "which episodes of The Bear am I missing", "how many '
        'episodes of Shogun do I have", "do I have all of season 3 of Severance". '
        'params: {title: string, season?: number}\n'
        '\n'
        'PLAYBACK CONTROL INTENTS (control an already-playing session):\n'
        '- playback_pause: user says pause/pause it. params: {}\n'
        '- playback_resume_session: user says resume / keep going / unpause (distinct from '
        '"resume X" which is the play intent with a specific title). Use this for a bare '
        '"resume", "unpause", "keep watching", "continue", "play it" when referring to a '
        'currently-paused session. params: {}\n'
        '- playback_stop: user says stop / stop watching / turn it off / exit. '
        'params: {}\n'
        '- playback_seek: user wants to skip ahead or rewind. Examples: "skip ahead 30 '
        'seconds", "rewind 10", "forward 2 minutes", "back 1 minute", "jump forward 5 '
        'minutes". params: {direction: "forward"|"back", seconds?: number}\n'
        '- playback_subtitles: user wants to toggle subtitles/captions. Examples: "subtitles '
        'on", "turn on captions", "subtitles off", "captions on". '
        'params: {enabled?: boolean} — omit if just a toggle.\n'
        '- playback_now_playing: user asks what\'s currently playing. Examples: "what\'s '
        'playing right now", "what\'s on", "is anything playing", "what\'s being watched". '
        'params: {}\n'
        '\n'
        'CONTINUE-WATCHING INTENTS:\n'
        '- continue_watching_list: user wants to see their continue-watching queue (not a '
        'specific title). Examples: "show me my continue watching", "what\'s halfway done", '
        '"anything I started but didn\'t finish", "what\'s on my continue watching list". '
        'NOTE: "where did I leave off in X" → watch_progress (existing). This intent is for '
        'the LIST, not a specific title. params: {}\n'
        '\n'
        'BOSLEY KITCHEN INTENTS (grocery list, meal plan, pantry, recipes):\n'
        '- kitchen_grocery_add: user wants to add one or more items to the grocery/shopping list. '
        'Triggered by "add ketchup to the list", "put milk on the grocery list", "we need eggs", '
        '"add bread and butter", "add a gallon of milk". '
        'params: {items?: [string], item?: string, quantity?: number, unit?: string} — '
        'prefer `items` (array) for multiple items. Use `item` + `quantity`/`unit` only for '
        'a single item with explicit quantity (e.g., "add 2 pounds of ground beef"). '
        'Strip filler words like "to the list", "to grocery", etc. Just the item name(s).\n'
        '- kitchen_grocery_list: user asks what is on the grocery list. '
        '"what\'s on the grocery list", "what do I need to buy", "read my grocery list", '
        '"how many items on the list". params: {}\n'
        '- kitchen_grocery_remove: user wants to remove a single item from the grocery list. '
        '"remove milk from the list", "take eggs off the grocery list", "I don\'t need bread anymore". '
        'params: {item: string}\n'
        '- kitchen_grocery_check: user says they bought/got an item (mark as purchased). '
        '"I got the milk", "mark bread as bought", "we picked up the eggs", "got ketchup". '
        'params: {item: string}\n'
        '- kitchen_grocery_clear: user wants to clear/empty the entire grocery list. '
        '"clear the grocery list", "empty the shopping list", "delete everything off the list". '
        'params: {}\n'
        '- kitchen_meal_today: user asks what we are eating on a specific day — today, '
        'tonight, tomorrow, tomorrow night, or any named weekday (Monday, Wednesday, etc.). '
        'Examples: "what\'s for dinner", "what are we having tonight", "what\'s for lunch today", '
        '"what\'s for dinner tomorrow", "what\'s on the menu Friday night", "what are we eating Wednesday". '
        'params: {meal_type?: "breakfast"|"lunch"|"dinner", day?: string, day_offset?: number} — '
        'default meal_type to dinner if unclear. '
        'For `day`, pass the spoken day reference exactly: "today", "tonight", "tomorrow", '
        '"tomorrow night", "Wednesday", "next Friday", etc. The handler resolves it. '
        'Alternatively (prefer `day` when the user spoke a word), use `day_offset`: 0=today, '
        '1=tomorrow, 2=day after tomorrow. Do NOT pass both.\n'
        '- kitchen_meal_week: user asks about this week\'s meal plan. '
        '"what are we eating this week", "what\'s on the meal plan", "show me this week\'s dinners". '
        'params: {}\n'
        '- kitchen_meal_plan_next_week: user asks about or wants to see next week\'s draft meal plan. '
        '"what\'s planned for next week", "show me next week\'s meals", "plan next week". '
        'params: {}\n'
        '- kitchen_meal_cooked: user says they cooked/finished a meal (deducts ingredients from pantry). '
        '"I cooked dinner", "we finished the chili", "dinner is done", "we just ate lunch". '
        'params: {meal_type?: "breakfast"|"lunch"|"dinner"}\n'
        '- kitchen_pantry_out: user says they are out of a pantry item (marks out AND adds to grocery list). '
        '"we\'re out of butter", "ran out of milk", "no more bread", "we finished the ketchup". '
        'params: {item: string}\n'
        '- kitchen_recipe_lookup: user asks about a recipe — its ingredients, prep/cook time, '
        'or description. "what\'s the recipe for chicken parm", "tell me about the lasagna recipe", '
        '"how do I make the jambalaya", "what\'s in that" (when prior turn named a recipe/meal). '
        'params: {recipe: string} — the recipe/dish name, resolved from conversation context if '
        'the user said "that" / "it" / "the recipe".\n'
        '- kitchen_recipe_to_grocery: user wants to add the ingredients of a recipe to the grocery list. '
        '"add the ingredients for lasagna", "I want to make chili this week, add what I need", '
        '"add everything I need for chicken parm". params: {recipe: string} — just the recipe/dish name.\n'
        '\n'
        '- general_question: anything else — weather, trivia, general knowledge, etc. params: {}\n\n'

        'RULES:\n'
        '- CONVERSATION CONTEXT: If prior turns appear in the messages array, '
        'use them to resolve references in the CURRENT user message. Pronouns '
        'like "that", "it", "this", "the recipe", "that one", "the same", and '
        'elliptical follow-ups ("what about tomorrow?", "and for lunch?") all '
        'refer to something in the most recent turns. Rewrite the referent '
        'into a concrete parameter before classifying. Examples:\n'
        '  Prior: user "what\'s for dinner tomorrow" / assistant "Dinner tomorrow night is Chicken Parm."\n'
        '  Current: "what\'s the recipe for that" → kitchen_recipe_lookup {recipe: "Chicken Parm"}\n'
        '  Prior: user "add The Batman" / assistant "Added The Batman to your watchlist."\n'
        '  Current: "in 4K" → download_movie {title: "The Batman", quality: "4K"}\n'
        '  Prior: user "what\'s for dinner tonight" / assistant "Dinner tonight is Lasagna."\n'
        '  Current: "what about tomorrow" → kitchen_meal_today {day: "tomorrow"}\n'
        '  Prior: user "play Dune in the living room" / assistant "Playing Dune in the living room."\n'
        '  Current: "pause it" → (no intent today) — fall through to general_question\n'
        '  ONLY use history to resolve pronouns/ellipsis. If the current message '
        'is clearly a new topic, ignore history entirely.\n'
        '- For compound commands ("set family room to 10 and turn on kitchen"), '
        'return MULTIPLE intents.\n'
        '- For "describe" intent, pass the user\'s ENTIRE sentence as the description — '
        'the downstream AI needs the full context.\n'
        '- For general_question, provide a direct 1-2 sentence answer (max 30 words) '
        'in the "answer" field. Just state facts — never say "based on search results" '
        'or narrate your process. Never tell the user to check another app or website.\n'
        '- The room parameter is the spoken room name (e.g., "family room", "kitchen"). '
        'Don\'t normalize it.\n'
        '- Quality keywords for play: "4K", "UHD", "remux", "1080p", "best quality", etc.\n'
        '- CRITICAL INTENT DISTINCTIONS (get these right — misclassification causes wrong actions):\n'
        '  • "add X", "can you add X", "add the movie X", "download X", "get X", "grab X", '
        '"put X on the watchlist" → download_movie or download_tv. '
        'The words "add", "download", "get", "grab" ALWAYS mean download_movie or download_tv. '
        'NEVER classify these as play or show_media.\n'
        '  • "play X", "put on X", "watch X", "play X in the [room]" → play. '
        'The word "play" or "watch" means start playback. NEVER classify as download_movie.\n'
        '  • "show me X", "pull up X", "look up X", "what about X", "find X", '
        '"search for X" → show_media (VIEW: opens info page). '
        'NEVER classify as download_movie or play.\n'
        '  Examples:\n'
        '    "add the movie Hitch" → download_movie {title: "Hitch"}\n'
        '    "play Hitch in the family room" → play {title: "Hitch", room: "family room"}\n'
        '    "show me Hitch" → show_media {title: "Hitch"}\n'
        '    "can you add Severance" → download_tv {title: "Severance"}\n'
        '    "download Inception in 4K" → download_movie {title: "Inception", quality: "4K"}\n'
        '- "show me X" where X is a person name → show_media (people are titles too). '
        '"show me X" where X is a page/category (latest movies, watchlist, library) → navigate. '
        '"describe" is for when the user can\'t name the '
        'title and is describing plot/actors/vibes — NOT for named people or specific titles.\n'
    )

    return base


# -------------------------------------------------------------------------
# API call
# -------------------------------------------------------------------------

def _call_claude(system: str, messages: list, tools: list) -> Dict[str, Any]:
    """Make a Claude API call with tool_use for structured output."""
    headers = {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
    }

    payload = {
        'model': _MODEL,
        'max_tokens': 1024,
        'system': system,
        'messages': messages,
        'tools': tools,
        'tool_choice': {'type': 'tool', 'name': 'classify_intent'},
    }

    resp = requests.post(_API_URL, headers=headers, json=payload, timeout=15)

    # Retry once on 429/529
    if resp.status_code in (429, 529):
        wait = min(int(resp.headers.get('retry-after', 3)), 10)
        logger.info('Claude API %d, retrying in %ds', resp.status_code, wait)
        time.sleep(wait)
        resp = requests.post(_API_URL, headers=headers, json=payload, timeout=15)

    resp.raise_for_status()
    return resp.json()


# -------------------------------------------------------------------------
# Parse response
# -------------------------------------------------------------------------

# Valid intent keys — must match handler dispatch
_VALID_INTENTS = {
    'lights_on', 'lights_off', 'lights_brightness',
    'volume_set', 'volume_adjust',
    'fan_on', 'fan_off',
    'describe',
    'show_media', 'navigate',
    'ownership_check',
    'score', 'next_game', 'event',
    'play',
    'time', 'date',
    'storage', 'free_space', 'downloads', 'queue',
    'download_movie', 'download_tv',
    'watch_progress', 'watch_history',
    'search_movie', 'search_tv',
    # Phase 1: library / watchlist / indexer / discovery / vibe
    'library_recent', 'library_quality', 'library_stats',
    'watchlist_list', 'watchlist_check', 'watchlist_remove',
    'indexer_top',
    'trending', 'popular', 'in_theaters',
    'vibe_mood', 'vibe_pick_for_me',
    # Phase 2: TV calendar / ownership + playback control + continue watching
    'tv_airing', 'tv_next_episode', 'tv_season_ownership',
    'playback_pause', 'playback_resume_session',
    'playback_stop', 'playback_seek', 'playback_subtitles',
    'playback_now_playing',
    'continue_watching_list',
    'kitchen_grocery_add', 'kitchen_grocery_list',
    'kitchen_grocery_remove', 'kitchen_grocery_check', 'kitchen_grocery_clear',
    'kitchen_meal_today', 'kitchen_meal_week',
    'kitchen_meal_plan_next_week', 'kitchen_meal_cooked',
    'kitchen_pantry_out',
    'kitchen_recipe_lookup',
    'kitchen_recipe_to_grocery',
    'general_question',
}


def _parse_response(data: Dict[str, Any]) -> ClassificationResult:
    """Extract ClassificationResult from Claude's tool_use response."""
    for block in data.get('content', []):
        if block.get('type') == 'tool_use' and block.get('name') == 'classify_intent':
            inp = block.get('input', {})
            raw_intents = inp.get('intents', [])
            answer = inp.get('answer')

            intents = []
            for item in raw_intents:
                key = item.get('intent', '')
                if key not in _VALID_INTENTS:
                    logger.warning('Classifier returned unknown intent: %s', key)
                    continue
                intents.append(Intent(
                    key=key,
                    params=item.get('parameters', {}),
                ))

            if not intents:
                # Fallback — treat as general question
                intents = [Intent(key='general_question')]

            return ClassificationResult(intents=intents, answer=answer)

    # No tool_use block found — shouldn't happen with tool_choice forced
    logger.error('No classify_intent tool_use block in response')
    return ClassificationResult(
        intents=[Intent(key='general_question')],
        answer="I'm not sure what you're asking. Could you try again?",
    )


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------

def classify(command: str,
             history: Optional[List[Dict[str, str]]] = None) -> ClassificationResult:
    """Classify a voice command into structured intents.

    Args:
        command: raw voice transcript
        history: optional list of prior conversation turns as
            [{'role': 'user'|'assistant', 'content': str}, ...]. Passed
            into the Claude messages array so the classifier can resolve
            pronouns ("that", "it", "the recipe") and follow-up questions
            against the previous exchange.

    Returns:
        ClassificationResult with intent(s) and optional answer
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY not set')

    system = _build_system_prompt()
    messages: List[Dict[str, str]] = []
    if history:
        # Only the last few turns — keeps prompt small and focused
        for h in history[-6:]:
            role = h.get('role')
            content = (h.get('content') or '').strip()
            if role in ('user', 'assistant') and content:
                messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': command})

    data = _call_claude(system, messages, [_CLASSIFY_TOOL])
    return _parse_response(data)
