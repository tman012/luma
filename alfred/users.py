"""User identity, roles, and authorization for Alfred.

Two home users (Taylor, Kirstin) are loaded from users.json. Everyone
else is implicitly EXTERNAL — a single fallback identity with a
restricted intent allowlist and a "polite guest" persona.

Resolve a User at the edge (Telegram chat_id, Luma source, /api/command
user_id) and thread it through dispatch() → _execute_handler() → any
handler that needs to know who is talking.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Set

logger = logging.getLogger(__name__)

_USERS_PATH = os.path.join(os.path.dirname(__file__), 'users.json')


@dataclass
class User:
    id: str                                  # stable key, e.g. "taylor"
    name: str                                # display name for TTS / prompts
    role: str                                # "home" | "external"
    persona: str = ''                        # system-prompt fragment for freeform replies
    allowed_intents: Optional[Set[str]] = None   # None = all, set = allowlist
    denied_intents: Set[str] = field(default_factory=set)

    def can(self, intent: str) -> bool:
        if intent in self.denied_intents:
            return False
        if self.allowed_intents is not None and intent not in self.allowed_intents:
            return False
        return True


# Catch-all for anyone who isn't Taylor or Kirstin.
# Restricted to safe, non-household intents and given a "guest voice" persona.
EXTERNAL = User(
    id='external',
    name='there',
    role='external',
    persona=(
        "You are speaking to someone outside the Bosley household — a friend, "
        "family member, or stranger. Be polite, warm, and brief. Do not reveal "
        "private household details like device states, storage, downloads, or "
        "what's in the library. You may answer general questions and chit-chat."
    ),
    allowed_intents={
        'time', 'date',
        'general_question',
        'score', 'next_game', 'event',
    },
)


class UserRegistry:
    def __init__(self):
        self._by_id: dict[str, User] = {}
        self._by_telegram: dict[int, str] = {}
        self._default_luma_user: Optional[str] = None
        self._load()

    def _load(self):
        if not os.path.exists(_USERS_PATH):
            logger.warning('users.json not found at %s — only EXTERNAL available', _USERS_PATH)
            return
        try:
            with open(_USERS_PATH) as f:
                data = json.load(f)
            for entry in data.get('users', []):
                user = User(
                    id=entry['id'],
                    name=entry['name'],
                    role=entry['role'],
                    persona=entry.get('persona', ''),
                    allowed_intents=(
                        set(entry['allowed_intents']) if entry.get('allowed_intents') else None
                    ),
                    denied_intents=set(entry.get('denied_intents', [])),
                )
                self._by_id[user.id] = user
                for chat_id in entry.get('telegram_chat_ids', []):
                    self._by_telegram[int(chat_id)] = user.id
            self._default_luma_user = data.get('default_luma_user')
            logger.info('Loaded %d home users from users.json', len(self._by_id))
        except Exception:
            logger.exception('Failed to load users.json')

    def by_id(self, user_id: Optional[str]) -> User:
        if not user_id:
            return EXTERNAL
        return self._by_id.get(user_id, EXTERNAL)

    def by_telegram_chat(self, chat_id: int) -> User:
        uid = self._by_telegram.get(chat_id)
        if uid and uid in self._by_id:
            return self._by_id[uid]
        return EXTERNAL

    def default_luma(self) -> User:
        if self._default_luma_user and self._default_luma_user in self._by_id:
            return self._by_id[self._default_luma_user]
        return EXTERNAL


registry = UserRegistry()
