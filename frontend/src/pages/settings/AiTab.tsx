/**
 * AiTab — Connection, Voice, Content Preferences, and Model Parameters for AI search.
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { MobileCardNav, type CardItem } from './components/MobileCardNav';
import { useSettings } from './useSettings';
import { apiFetch } from '../../apiClient';

// Preview phrases for each voice
const VOICE_PREVIEWS: Record<string, string> = {
  nova: "Hey, I found a few movies you might enjoy tonight.",
  alloy: "Your download is complete. Dune Part Two is ready to watch.",
  echo: "You have three terabytes of free storage remaining.",
  fable: "I've added Reacher to your watchlist and started the download.",
  onyx: "Nothing is currently downloading. Your queue is empty.",
  shimmer: "Here are some movies similar to Bernie that you might like.",
  elevenlabs: "Hey, I found a few movies you might enjoy tonight.",
};

function VoicePreviewButton({ voice, elevenlabsVoiceId }: { voice: string; elevenlabsVoiceId?: string }) {
  const [playing, setPlaying] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const handlePreview = useCallback(async () => {
    // Stop any current preview
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
      setPlaying(false);
    }

    setPlaying(true);
    setPreviewError('');
    try {
      const text = VOICE_PREVIEWS[voice] || VOICE_PREVIEWS.nova;
      const payload: Record<string, unknown> = { text, voice };
      if (elevenlabsVoiceId) {
        payload.elevenlabs_voice_id = elevenlabsVoiceId;
      }
      const resp = await apiFetch('/api/voice/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: 'TTS failed' }));
        setPreviewError(err.error || 'TTS failed — save settings first');
        setPlaying(false);
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        setPlaying(false);
        URL.revokeObjectURL(url);
        audioRef.current = null;
      };
      audio.onerror = () => {
        setPlaying(false);
        URL.revokeObjectURL(url);
        audioRef.current = null;
      };
      await audio.play();
    } catch {
      setPlaying(false);
    }
  }, [voice, elevenlabsVoiceId]);

  return (
    <>
    <button
      type="button"
      onClick={handlePreview}
      disabled={playing}
      className="flex items-center gap-1.5 rounded-lg bg-white/[0.06] hover:bg-white/10 disabled:opacity-50 px-3 py-2 text-[13px] text-white/60 hover:text-white/80 transition-colors"
    >
      {playing ? (
        <>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-pulse">
            <rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" />
          </svg>
          Playing...
        </>
      ) : (
        <>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
          Preview
        </>
      )}
    </button>
    {previewError && (
      <span className="text-[12px] text-red-400 ml-2">{previewError}</span>
    )}
    </>
  );
}

function ElevenLabsVoiceSelect() {
  const { getByPath, setByPath } = useSettings();
  const [voices, setVoices] = useState<Array<{voice_id: string; name: string; category: string}>>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiFetch('/api/elevenlabs/voices')
      .then(r => r.json())
      .then(data => {
        if (!cancelled && data.voices) setVoices(data.voices);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const currentId = (getByPath('apis.elevenlabs.voice_id') as string) || '';

  if (loading) return <p className="text-[13px] text-white/40">Loading voices...</p>;
  if (!voices.length) return <p className="text-[13px] text-white/40">Save your API key first, then reopen this card to see voices.</p>;

  return (
    <div className="sm:col-span-2 flex flex-col gap-1.5">
      <label className="text-[13px] font-medium text-white/60">Voice</label>
      <select
        value={currentId}
        onChange={e => setByPath('apis.elevenlabs.voice_id', e.target.value)}
        className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[14px] text-white/90 focus:border-emerald-500/40 focus:outline-none"
      >
        <option value="">Select a voice...</option>
        {voices.map(v => (
          <option key={v.voice_id} value={v.voice_id}>
            {v.name} {v.category ? `(${v.category})` : ''}
          </option>
        ))}
      </select>
    </div>
  );
}

export function AiTab() {
  const { getByPath, setByPath } = useSettings();
  const currentVoice = (getByPath('apis.openai.tts_voice') as string) || 'nova';
  const ttsProvider = (getByPath('apis.openai.tts_provider') as string) || 'openai';
  const currentElVoiceId = (getByPath('apis.elevenlabs.voice_id') as string) || '';

  const customInstructions = (getByPath('apis.openai.custom_instructions') as string) ?? '';

  const items: CardItem[] = [
    {
      title: 'Connection',
      subtitle: 'OpenAI API for Describe It search',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 2a4 4 0 0 1 4 4v1a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4Z" />
          <path d="M12 13c-4 0-7 2-7 4v3h14v-3c0-2-3-4-7-4Z" />
          <circle cx="12" cy="12" r="10" strokeDasharray="2 2" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Connection"
          subtitle="OpenAI API for Describe It search in Luma AI"
          paths={['apis.openai.enabled', 'apis.openai.api_key', 'apis.openai.model', 'apis.openai.voice_enabled', 'apis.openai.wake_word']}
          testService="openai"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M12 2a4 4 0 0 1 4 4v1a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4Z" />
              <path d="M12 13c-4 0-7 2-7 4v3h14v-3c0-2-3-4-7-4Z" />
              <circle cx="12" cy="12" r="10" strokeDasharray="2 2" />
            </svg>
          }
        >
          <div className="sm:col-span-2">
            <ToggleSwitch label="Enable AI Search" path="apis.openai.enabled" />
          </div>
          <div className="sm:col-span-2">
            <ToggleSwitch
              label="Voice Assistant"
              hint="Enable voice commands for search and playback control"
              path="apis.openai.voice_enabled"
            />
          </div>
          <FormField
            label="Wake Word"
            path="apis.openai.wake_word"
            type="text"
            placeholder="alfred"
            hint="The word that activates voice listening (e.g. alfred, jarvis, luma, friday)"
          />
          <FormField label="API Key" path="apis.openai.api_key" type="password" placeholder="sk-..." />
          <FormField
            label="Model"
            path="apis.openai.model"
            type="select"
            options={[
              { value: 'gpt-4.1', label: 'GPT-4.1 (Recommended)' },
              { value: 'gpt-5', label: 'GPT-5 (Best accuracy, slower)' },
              { value: 'gpt-4o', label: 'GPT-4o' },
              { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
              { value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo' },
            ]}
          />
        </SettingsCard>
      ),
    },
    {
      title: 'Voice',
      subtitle: 'Choose the voice for spoken responses',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
          <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Voice"
          subtitle="Choose the TTS provider and voice for spoken responses"
          paths={['apis.openai.tts_provider', 'apis.openai.tts_voice', 'apis.elevenlabs.api_key', 'apis.elevenlabs.voice_id', 'apis.elevenlabs.model_id']}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
            </svg>
          }
        >
          <FormField
            label="TTS Provider"
            path="apis.openai.tts_provider"
            type="select"
            options={[
              { value: 'openai', label: 'OpenAI' },
              { value: 'elevenlabs', label: 'ElevenLabs' },
            ]}
          />

          {ttsProvider === 'openai' && (
            <>
              <FormField
                label="Voice"
                path="apis.openai.tts_voice"
                type="select"
                options={[
                  { value: 'nova', label: 'Nova — warm, conversational' },
                  { value: 'shimmer', label: 'Shimmer — soft, friendly' },
                  { value: 'alloy', label: 'Alloy — neutral, balanced' },
                  { value: 'fable', label: 'Fable — British, expressive' },
                  { value: 'echo', label: 'Echo — deeper male' },
                  { value: 'onyx', label: 'Onyx — deep, authoritative' },
                ]}
                hint="Save first, then preview to hear the difference"
              />
              <div className="flex items-center gap-3">
                <VoicePreviewButton voice={currentVoice} />
                <span className="text-[12px] text-white/30">
                  Plays a sample with the selected voice
                </span>
              </div>
            </>
          )}

          {ttsProvider === 'elevenlabs' && (
            <>
              <FormField label="API Key" path="apis.elevenlabs.api_key" type="password" placeholder="xi-..." />
              <ElevenLabsVoiceSelect />
              <FormField
                label="Model"
                path="apis.elevenlabs.model_id"
                type="select"
                options={[
                  { value: 'eleven_multilingual_v2', label: 'Multilingual v2 (Best quality)' },
                  { value: 'eleven_turbo_v2_5', label: 'Turbo v2.5 (Faster)' },
                  { value: 'eleven_turbo_v2', label: 'Turbo v2' },
                  { value: 'eleven_monolingual_v1', label: 'English v1' },
                ]}
              />
              <div className="flex items-center gap-3">
                <VoicePreviewButton voice="elevenlabs" elevenlabsVoiceId={currentElVoiceId} />
                <span className="text-[12px] text-white/30">
                  Preview with your selected ElevenLabs voice
                </span>
              </div>
            </>
          )}
        </SettingsCard>
      ),
    },
    {
      title: 'Content Preferences',
      subtitle: 'Tell the AI what to include or exclude',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Content Preferences"
          subtitle="Tell the AI what to include or exclude from recommendations"
          paths={['apis.openai.custom_instructions']}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
          }
        >
          <div className="sm:col-span-2 flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Custom Instructions</label>
            <textarea
              value={customInstructions}
              onChange={(e) => {
                const val = e.target.value.slice(0, 500);
                setByPath('apis.openai.custom_instructions', val);
              }}
              placeholder="e.g. Only suggest English-language films. Do not include horror movies."
              rows={4}
              maxLength={500}
              className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 text-[14px] text-white/90 placeholder:text-white/25 focus:border-emerald-500/40 focus:outline-none resize-none"
            />
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-white/30">
                These instructions are appended to the AI prompt for Describe It searches.
              </span>
              <span className="text-[11px] text-white/30">
                {customInstructions.length}/500
              </span>
            </div>
          </div>
        </SettingsCard>
      ),
    },
    {
      title: 'Model Parameters',
      subtitle: 'Fine-tune AI response behavior',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" />
          <line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" />
          <line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" />
          <line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Model Parameters"
          subtitle="Fine-tune AI response behavior"
          paths={['apis.openai.temperature', 'apis.openai.max_tokens']}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" />
              <line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" />
              <line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" />
              <line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" />
            </svg>
          }
        >
          <FormField
            label="Temperature"
            path="apis.openai.temperature"
            type="number"
            placeholder="0.7"
            min={0}
            max={1}
            step={0.1}
            hint="Lower = more predictable, higher = more creative"
          />
          <FormField
            label="Response Length"
            path="apis.openai.max_tokens"
            type="number"
            placeholder="1500"
            min={500}
            max={8000}
            step={100}
            hint="How much the AI can write back (500 = short, 1500 = normal, 4000+ = detailed)"
          />
        </SettingsCard>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">AI</h2>
        <p className="mt-1 text-[13px] text-white/40">
          AI-powered search, content preferences, and model configuration.
        </p>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}
