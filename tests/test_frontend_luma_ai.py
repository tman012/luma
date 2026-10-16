"""Vibe Engine / Luma AI stays live: entry points, routes and the no-key fallback."""

from tests.frontend_ts import FRONTEND, needs_ts, run_ts

SRC = FRONTEND / 'src'


def _read(rel):
    return (SRC / rel).read_text()


def test_entry_points_present():
    top = _read('components/layout/TopBar.tsx')
    assert "{ label: 'AI', to: null" in top and 'openVibeEngine' in top
    menu = _read('components/mobile/MobileSideMenu.tsx')
    assert "label: 'Luma AI'" in menu and 'onVibeOpen' in menu
    assert 'onVibeOpen={openVibeEngine}' in _read('components/mobile/MobileHeader.tsx')
    home = _read('pages/AuthenticatedHomePage.tsx')
    assert "id: 'luma-ai'" in home and '<LumaAISheet' in home
    host = _read('contexts/AppModalHost.tsx')
    assert '<VibeEngineModal' in host and 'openVibeEngine' in host
    assert "| 'vibe_engine'" in _read('contexts/ModalHostContext.ts')
    assert 'ai: <AiTab />' in _read('pages/SettingsPage.tsx')


def test_routes_present():
    app = _read('App.tsx')
    assert 'path="library" element={isExpanded ? <Navigate to="/browse" replace /> : <VibeEnginePage />}' in app
    assert 'path="describe-it"' in app and 'path="downloads"' in app
    assert '<VoiceProvider>' in app


def test_no_diagnostics_or_old_event_names():
    for rel in ('contexts/AppModalHost.tsx', 'hooks/useVoiceAssistant.ts', 'components/media/HeroSection.tsx'):
        src = _read(rel)
        assert '_appdiag' not in src and "'voice-search'" not in src


@needs_ts
def test_describe_hidden_without_openai():
    out = run_ts('''
import { availableModes } from '@src/services/vibeApi';
const cards = [{ id: 'feel' }, { id: 'pick' }, { id: 'describe' }] as const;
console.log(JSON.stringify({
  off: availableModes(cards, false).map((c) => c.id),
  on: availableModes(cards, true).map((c) => c.id),
}));
''')
    assert out == {'off': ['feel', 'pick'], 'on': ['feel', 'pick', 'describe']}


@needs_ts
def test_settings_read_ai_flag(tmp_path):
    out = run_ts('''
import { fetchVibeSettings } from '@src/services/vibeApi';
(globalThis as any).window = {};
let body: any = { ai_search_available: true };
(globalThis as any).fetch = async () => ({ ok: true, json: async () => body });
(async () => {
  const a = await fetchVibeSettings();
  console.log(JSON.stringify({ a: a.aiSearchAvailable }));
})();
''')
    assert out == {'a': True}


@needs_ts
def test_kiosk_voice_suggestion_uses_wake_word():
    out = run_ts('''
import { voiceSuggestion } from '@src/pages/DailyKioskPage';
console.log(JSON.stringify([voiceSuggestion('nova', 1), voiceSuggestion('', 0), voiceSuggestion('alfred', 11)]));
''')
    assert out == ['"Nova, play something funny"', '"What movies came out this week?"',
                   '"Alfred, play something funny"']


def test_kiosk_voice_suggestion_hidden_when_voice_off():
    src = _read('pages/DailyKioskPage.tsx')
    assert 'if (!voiceEnabled) return null;' in src


def test_server_reports_ai_availability(test_app):
    import app.main
    import app.user_auth as user_auth
    user_auth.create_user('owner', '1234', is_admin=True)
    c = test_app.test_client()
    lan = {'REMOTE_ADDR': '192.168.1.20'}
    app.main.settings.update({'apis': {'openai': {'enabled': True, 'api_key': ''}}})
    assert c.get('/api/kiosk/settings', environ_base=lan).get_json()['ai_search_available'] is False
    app.main.settings.update({'apis': {'openai': {'enabled': True, 'api_key': 'sk-test'}}})
    assert c.get('/api/kiosk/settings', environ_base=lan).get_json()['ai_search_available'] is True
    app.main.settings.update({'apis': {'openai': {'enabled': False, 'api_key': ''}}})
