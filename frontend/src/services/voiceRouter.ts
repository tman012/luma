/**
 * Voice Command Router
 *
 * Routes ALL commands to Alfred except pure UI actions (navigation, reset)
 * that don't need an AI agent.
 */

import type { VoiceRoute, LocalAction } from '@/types/voice';

// ---------------------------------------------------------------------------
// Local-only rules — UI navigation that Alfred can't do (no browser access)
// ---------------------------------------------------------------------------

interface RouteRule {
  pattern: RegExp;
  action: (match: RegExpMatchArray) => LocalAction;
  confidence: number;
}

const LOCAL_RULES: RouteRule[] = [
  // Dismiss phrases — close Nova silently, stay on the current page. No TTS,
  // no navigation, no backend call. User just wants Nova to go away.
  {
    pattern: /^(?:nevermind|never\s+mind|exit|close|dismiss|leave(?:\s+me\s+alone)?|cancel|stop|quit|shut\s*up|go\s+away|forget\s+it|nothing|bye|goodbye|good\s*bye|i'?m\s+done|that'?s?\s+all|go\s+home|home)$/,
    action: () => ({ type: 'dismiss' as const }),
    confidence: 1,
  },

  // Explicit navigate-home phrases — close Nova AND navigate to /
  {
    pattern: /^(?:take\s+me\s+home|go\s+to\s+(?:the\s+)?(?:home|main|start)(?:\s+(?:page|screen))?|back\s+to\s+(?:the\s+)?home(?:\s+page)?|home\s+page)$/,
    action: () => ({ type: 'navigate', path: '/' }),
    confidence: 1,
  },
  {
    pattern: /^go\s+back$/,
    action: () => ({ type: 'navigate', path: 'back' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?explore(?:\s+page)?$/,
    action: () => ({ type: 'navigate', path: '/explore' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?(?:library|vibe\s+engine)$/,
    action: () => ({ type: 'navigate', path: '/library' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?(?:watchlist|watch\s+list)$/,
    action: () => ({ type: 'navigate', path: '/watchlist' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?(?:now\s+available|available|new\s+releases?)$/,
    action: () => ({ type: 'navigate', path: '/now-available' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?(?:browse|browse\s+library)$/,
    action: () => ({ type: 'navigate', path: '/browse' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?settings$/,
    action: () => ({ type: 'navigate', path: '/settings' }),
    confidence: 1,
  },
  {
    pattern: /^(?:open|show|go\s+to)\s+(?:the\s+)?(?:recently\s+added|recent)$/,
    action: () => ({ type: 'navigate', path: '/explore' }),
    confidence: 0.9,
  },

  // Reset Alfred — simple API call, no LLM needed
  {
    pattern: /^(?:reset|restart|reload)\s+alfred$/,
    action: () => ({ type: 'reset_alfred' as const }),
    confidence: 1,
  },
];

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Route a voice transcript.
 *
 * Only UI navigation and Alfred reset are handled locally.
 * Everything else goes to Alfred.
 */
export function routeVoiceCommand(transcript: string): VoiceRoute {
  // Strip trailing punctuation — Whisper often adds periods, commas, question marks
  const text = transcript.toLowerCase().trim().replace(/[.,!?;:]+$/, '').trim();

  // Local UI-only actions
  for (const rule of LOCAL_RULES) {
    const match = text.match(rule.pattern);
    if (match) {
      return {
        target: 'local_luma',
        localAction: rule.action(match),
        transcript,
        confidence: rule.confidence,
      };
    }
  }

  // Everything else → Alfred
  return {
    target: 'alfred',
    transcript,
    confidence: 1,
  };
}
