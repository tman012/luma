import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useVoiceAssistant } from '@/hooks/useVoiceAssistant';
import { useVoice } from '@/contexts/VoiceContext';

// Amazon Ember font — injected once
const EMBER_FONT_ID = 'amazon-ember-font';
function ensureEmberFont() {
  if (document.getElementById(EMBER_FONT_ID)) return;
  const style = document.createElement('style');
  style.id = EMBER_FONT_ID;
  style.textContent = `
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_Lt.ttf') format('truetype');
      font-weight: 300;
      font-style: normal;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_LtIt.ttf') format('truetype');
      font-weight: 300;
      font-style: italic;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_Rg.ttf') format('truetype');
      font-weight: 400;
      font-style: normal;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_RgIt.ttf') format('truetype');
      font-weight: 400;
      font-style: italic;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/Amazon-Ember-Medium.ttf') format('truetype');
      font-weight: 500;
      font-style: normal;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/Amazon-Ember-MediumItalic.ttf') format('truetype');
      font-weight: 500;
      font-style: italic;
      font-display: swap;
    }
  `;
  document.head.appendChild(style);
}

const EMBER_FAMILY = "'Amazon Ember', -apple-system, BlinkMacSystemFont, sans-serif";

// ---------------------------------------------------------------------------
// Living Orb — audio-reactive visualizer
// ---------------------------------------------------------------------------

export function LivingOrb({
  isListening,
  isProcessing,
  isSpeaking,
  analyserNode,
  onTap,
}: {
  isListening: boolean;
  isProcessing: boolean;
  isSpeaking: boolean;
  analyserNode: AnalyserNode | null;
  onTap: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const phaseRef = useRef(0);
  // Smooth amplitude to avoid jitter
  const smoothAmpRef = useRef(0);

  // Track props via refs so the draw loop reads current values without
  // tearing down the animation on every state change (prevents stutter).
  const stateRef = useRef({ isListening, isProcessing, isSpeaking, analyserNode });
  stateRef.current = { isListening, isProcessing, isSpeaking, analyserNode };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const c = canvas.getContext('2d');
    if (!c) return;

    const SIZE = 520;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = SIZE * dpr;
    canvas.height = SIZE * dpr;
    c.scale(dpr, dpr);
    const ctx = c;

    const CX = SIZE / 2;
    const CY = SIZE / 2;
    const PI2 = Math.PI * 2;

    // --- Pre-allocated buffers ---
    // Size 1024 covers any analyser fftSize; draw() slices what it needs
    const freqData = new Uint8Array(1024);

    // --- Particle system (3D projected) ---
    const PARTICLE_COUNT = 220;
    // Per-particle: x, y, z, orbitRadius, orbitSpeed, orbitPhase, orbitTilt, size
    const pX = new Float32Array(PARTICLE_COUNT);
    const pY = new Float32Array(PARTICLE_COUNT);
    const pZ = new Float32Array(PARTICLE_COUNT);
    const pOrbitR = new Float32Array(PARTICLE_COUNT);
    const pSpeed = new Float32Array(PARTICLE_COUNT);
    const pPhase = new Float32Array(PARTICLE_COUNT);
    const pTilt = new Float32Array(PARTICLE_COUNT);
    const pSize = new Float32Array(PARTICLE_COUNT);
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      pOrbitR[i] = 70 + Math.random() * 135;
      pSpeed[i] = (0.3 + Math.random() * 0.7) * (Math.random() > 0.5 ? 1 : -1);
      pPhase[i] = Math.random() * PI2;
      pTilt[i] = (Math.random() - 0.5) * Math.PI * 0.8;
      pSize[i] = 0.6 + Math.random() * 1.8;
    }

    // --- Tendril system ---
    const TENDRIL_COUNT = 7;
    const tendrilAngles = new Float32Array(TENDRIL_COUNT);
    const tendrilSpeeds = new Float32Array(TENDRIL_COUNT);
    for (let i = 0; i < TENDRIL_COUNT; i++) {
      tendrilAngles[i] = (i / TENDRIL_COUNT) * PI2 + (Math.random() - 0.5) * 0.4;
      tendrilSpeeds[i] = 0.08 + Math.random() * 0.12;
    }

    // --- Lens flare drifters ---
    const FLARE_COUNT = 5;
    const flareAngle = new Float32Array(FLARE_COUNT);
    const flareR = new Float32Array(FLARE_COUNT);
    const flareSpeed = new Float32Array(FLARE_COUNT);
    const flareBright = new Float32Array(FLARE_COUNT);
    for (let i = 0; i < FLARE_COUNT; i++) {
      flareAngle[i] = Math.random() * PI2;
      flareR[i] = 40 + Math.random() * 100;
      flareSpeed[i] = 0.2 + Math.random() * 0.5;
      flareBright[i] = Math.random();
    }

    // --- Noise function (simple 2D value noise for plasma) ---
    const NOISE_SIZE = 64;
    const noiseTable = new Float32Array(NOISE_SIZE * NOISE_SIZE);
    for (let i = 0; i < noiseTable.length; i++) noiseTable[i] = Math.random();
    function noise2d(x: number, y: number): number {
      const xi = ((x | 0) % NOISE_SIZE + NOISE_SIZE) % NOISE_SIZE;
      const yi = ((y | 0) % NOISE_SIZE + NOISE_SIZE) % NOISE_SIZE;
      const x1 = (xi + 1) % NOISE_SIZE;
      const y1 = (yi + 1) % NOISE_SIZE;
      const fx = x - Math.floor(x);
      const fy = y - Math.floor(y);
      const a = noiseTable[yi * NOISE_SIZE + xi]!;
      const b = noiseTable[yi * NOISE_SIZE + x1]!;
      const cc = noiseTable[y1 * NOISE_SIZE + xi]!;
      const d = noiseTable[y1 * NOISE_SIZE + x1]!;
      const top = a + (b - a) * fx;
      const bot = cc + (d - cc) * fx;
      return top + (bot - top) * fy;
    }

    // --- Color palette per state ---
    function getColors(state: string, amp: number) {
      switch (state) {
        case 'speaking': return {
          core: [197, 205, 210],     // Echo cyan-blue
          outer: [190, 200, 215],    // Echo blue outer
          coreLight: 60 + amp * 20,
          outerLight: 45 + amp * 15,
          sat: 85 + amp * 15,
          glowAlpha: 0.35 + amp * 0.45,
        };
        case 'listening': return {
          core: [197, 205, 210],     // Echo cyan-blue
          outer: [190, 200, 215],    // Echo blue outer
          coreLight: 55 + amp * 15,
          outerLight: 40 + amp * 10,
          sat: 80 + amp * 20,
          glowAlpha: 0.25 + amp * 0.35,
        };
        case 'processing': return {
          core: [270, 290, 310],     // electric violet/magenta
          outer: [250, 280, 320],    // shifting purple spectrum
          coreLight: 60 + amp * 25,
          outerLight: 45 + amp * 20,
          sat: 80 + amp * 20,
          glowAlpha: 0.4 + amp * 0.5,
        };
        default: return {            // idle: Luma purple
          core: [275, 280, 290],
          outer: [265, 275, 285],
          coreLight: 40,
          outerLight: 28,
          sat: 55,
          glowAlpha: 0.18,
        };
      }
    }

    function draw() {
      ctx.clearRect(0, 0, SIZE, SIZE);
      phaseRef.current += 0.012;
      const t = phaseRef.current;

      // Read current state from ref — avoids tearing down animation on state changes
      const { isListening: _listening, isProcessing: _processing, isSpeaking: _speaking, analyserNode: _analyser } = stateRef.current;

      // --- Audio amplitude ---
      let rawAmp = 0;
      if (_speaking && _analyser) {
        _analyser.getByteFrequencyData(freqData);
        let sum = 0;
        const bins = Math.min(freqData.length, 48);
        for (let i = 0; i < bins; i++) sum += freqData[i]!;
        rawAmp = sum / bins / 255;
      } else if (_speaking) {
        // Synthesizer-like pulsing — layered frequencies for speech-reactive feel
        const bass = Math.sin(t * 3.8) * 0.18;
        const mid = Math.sin(t * 7.3) * 0.12;
        const high = Math.sin(t * 13.7) * 0.06;
        const burst = Math.pow(Math.abs(Math.sin(t * 2.1)), 4) * 0.2;
        const flutter = Math.sin(t * 21) * 0.03;
        rawAmp = 0.35 + bass + mid + high + burst + flutter;
      } else if (_listening) {
        rawAmp = 0.18 + Math.sin(t * 1.8) * 0.12 + Math.sin(t * 3.1) * 0.05;
      } else if (_processing) {
        // Dramatic pulsing — energy building with harmonic oscillations
        const pulse = Math.sin(t * 3.5) * 0.15;
        const spike = Math.pow(Math.abs(Math.sin(t * 1.8)), 3) * 0.25;
        const wobble = Math.sin(t * 7) * 0.06 + Math.cos(t * 11) * 0.04;
        rawAmp = 0.3 + pulse + spike + wobble;
      } else {
        rawAmp = 0.04 + Math.sin(t * 0.7) * 0.03;
      }
      // Smooth
      smoothAmpRef.current += (rawAmp - smoothAmpRef.current) * 0.15;
      const amp = smoothAmpRef.current;

      const state = _speaking ? 'speaking' : _listening ? 'listening' : _processing ? 'processing' : 'idle';
      const pal = getColors(state, amp);

      const baseSpeedMult = state === 'listening' ? 1.8 : state === 'processing' ? 3.5 : state === 'speaking' ? 1.4 : 1;

      // =======================================================================
      // 1. OUTER BLOOM / GLOW (fake bloom via layered transparent circles)
      // =======================================================================
      for (let i = 4; i >= 0; i--) {
        const ratio = i / 4;
        const r = 170 + ratio * 80 + amp * 40;
        const a = (1 - ratio) * pal.glowAlpha * 0.35;
        const hue = pal.outer[i % 3]!;
        const grad = ctx.createRadialGradient(CX, CY, r * 0.3, CX, CY, r);
        grad.addColorStop(0, `hsla(${hue}, ${pal.sat}%, ${pal.outerLight}%, ${a})`);
        grad.addColorStop(1, `hsla(${hue}, ${pal.sat}%, ${pal.outerLight}%, 0)`);
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, SIZE, SIZE);
      }

      // =======================================================================
      // 2. ENERGY RINGS (2-3 concentric, rotating, dashed)
      // =======================================================================
      ctx.save();
      ctx.translate(CX, CY);
      const ringConfigs = [
        { r: 115 + amp * 20, rot: t * 0.4 * baseSpeedMult, tilt: 0.25, dash: [8, 14], width: 1.4, hueIdx: 0 },
        { r: 145 + amp * 25, rot: -t * 0.55 * baseSpeedMult, tilt: -0.35, dash: [4, 20, 12, 8], width: 1.0, hueIdx: 1 },
        { r: 170 + amp * 30, rot: t * 0.3 * baseSpeedMult, tilt: 0.15, dash: [2, 24], width: 0.8, hueIdx: 2 },
      ];
      for (const ring of ringConfigs) {
        ctx.save();
        ctx.rotate(ring.rot);
        // Simulate tilt via scaleY
        const tiltScale = 0.3 + Math.abs(Math.sin(t * 0.2 + ring.tilt * 5)) * 0.7;
        ctx.scale(1, tiltScale);
        ctx.setLineDash(ring.dash);
        const ringAlpha = (0.15 + amp * 0.5) * (0.5 + tiltScale * 0.5);
        const rHue = pal.core[ring.hueIdx]!;
        ctx.strokeStyle = `hsla(${rHue}, ${pal.sat + 10}%, ${pal.coreLight}%, ${ringAlpha})`;
        ctx.lineWidth = ring.width + amp * 1.5;
        ctx.shadowColor = `hsla(${rHue}, ${pal.sat}%, ${pal.coreLight + 20}%, ${ringAlpha * 0.6})`;
        ctx.shadowBlur = 8 + amp * 12;
        ctx.beginPath();
        ctx.arc(0, 0, ring.r, 0, PI2);
        ctx.stroke();
        ctx.restore();
      }
      ctx.setLineDash([]);
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;
      ctx.restore();

      // =======================================================================
      // 3. PARTICLE FIELD (3D projected orbits)
      // =======================================================================
      const spreadMult = state === 'speaking' ? 1.0 + amp * 0.6 : state === 'listening' ? 0.7 + amp * 0.2 : 1.0;
      const focalLength = 300;
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const phase = pPhase[i]! + t * pSpeed[i]! * baseSpeedMult * 0.5;
        const orbitR = pOrbitR[i]! * spreadMult;
        const tilt = pTilt[i]!;
        // 3D orbit position
        const ox = Math.cos(phase) * orbitR;
        const oy = Math.sin(phase) * orbitR * Math.cos(tilt);
        const oz = Math.sin(phase) * orbitR * Math.sin(tilt);
        pX[i] = ox;
        pY[i] = oy;
        pZ[i] = oz;
        // 3D → 2D perspective
        const perspective = focalLength / (focalLength + oz);
        const sx = CX + ox * perspective;
        const sy = CY + oy * perspective;
        const sz = pSize[i]! * perspective * (0.8 + amp * 0.6);
        // Depth-based alpha
        const depthAlpha = 0.15 + perspective * 0.6 + amp * 0.25;
        const hue = pal.core[(i % 3)]!;
        ctx.fillStyle = `hsla(${hue + (i % 20) - 10}, ${pal.sat}%, ${pal.coreLight + 10}%, ${Math.min(depthAlpha, 0.9)})`;
        ctx.beginPath();
        ctx.arc(sx, sy, Math.max(sz, 0.3), 0, PI2);
        ctx.fill();
      }

      // =======================================================================
      // 4. FILAMENT TENDRILS (wavy energy arcs from core)
      // =======================================================================
      const tendrilExtent = state === 'idle' ? 50 : state === 'processing' ? 80 + amp * 90 : 65 + amp * 75;
      for (let i = 0; i < TENDRIL_COUNT; i++) {
        const baseAngle = tendrilAngles[i]! + t * tendrilSpeeds[i]!;
        ctx.beginPath();
        const segments = 20;
        for (let s = 0; s <= segments; s++) {
          const ratio = s / segments;
          const dist = 30 + ratio * tendrilExtent;
          // Wave displacement perpendicular to tendril direction
          const waveOff = Math.sin(ratio * 8 + t * 3 + i * 1.7) * (6 + amp * 18) * ratio;
          const waveOff2 = Math.cos(ratio * 5 - t * 2 + i * 2.3) * (3 + amp * 8) * ratio;
          const angle = baseAngle + waveOff * 0.015;
          const x = CX + Math.cos(angle) * dist + Math.sin(angle) * (waveOff + waveOff2);
          const y = CY + Math.sin(angle) * dist - Math.cos(angle) * (waveOff + waveOff2);
          if (s === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        const tHue = pal.core[i % 3]!;
        const tAlpha = (0.08 + amp * 0.35) * (1 - (i % 2) * 0.3);
        ctx.strokeStyle = `hsla(${tHue}, ${pal.sat + 15}%, ${pal.coreLight + 15}%, ${tAlpha})`;
        ctx.lineWidth = 1 + amp * 2;
        ctx.shadowColor = `hsla(${tHue}, ${pal.sat}%, ${pal.coreLight + 30}%, ${tAlpha * 0.5})`;
        ctx.shadowBlur = 6 + amp * 10;
        ctx.stroke();
      }
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;

      // =======================================================================
      // 5. CENTRAL PLASMA CORE (animated noise + overlapping gradients)
      // =======================================================================
      const coreR = 55 + amp * 25;
      // Multiple plasma layers with shifting hues
      for (let layer = 0; layer < 4; layer++) {
        const noiseOff = noise2d(t * 2.5 + layer * 17, t * 1.8 + layer * 23);
        const hShift = (noiseOff - 0.5) * 40;
        const rShift = (noise2d(t * 1.2 + layer * 7, layer * 13) - 0.5) * 12;
        const hue = pal.core[layer % 3]! + hShift;
        const r = coreR + rShift;
        const grad = ctx.createRadialGradient(
          CX + Math.sin(t * 1.3 + layer) * 4, CY + Math.cos(t * 1.7 + layer) * 4, 0,
          CX, CY, r
        );
        const layerAlpha = (0.25 + amp * 0.4) / (1 + layer * 0.3);
        grad.addColorStop(0, `hsla(${hue}, ${pal.sat + 20}%, ${pal.coreLight + 25}%, ${layerAlpha})`);
        grad.addColorStop(0.4, `hsla(${hue + 15}, ${pal.sat + 10}%, ${pal.coreLight + 10}%, ${layerAlpha * 0.7})`);
        grad.addColorStop(1, `hsla(${hue + 30}, ${pal.sat}%, ${pal.outerLight}%, 0)`);
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(CX, CY, r, 0, PI2);
        ctx.fill();
      }

      // Bright white hot-spot at very center
      const hotGrad = ctx.createRadialGradient(CX, CY, 0, CX, CY, 18 + amp * 12);
      const hotAlpha = 0.4 + amp * 0.5;
      hotGrad.addColorStop(0, `rgba(255, 255, 255, ${hotAlpha})`);
      hotGrad.addColorStop(0.4, `hsla(${pal.core[0]}, 60%, 85%, ${hotAlpha * 0.5})`);
      hotGrad.addColorStop(1, 'rgba(255, 255, 255, 0)');
      ctx.fillStyle = hotGrad;
      ctx.beginPath();
      ctx.arc(CX, CY, 18 + amp * 12, 0, PI2);
      ctx.fill();

      // =======================================================================
      // 6. CHROMATIC ABERRATION (RGB offset glow layers)
      // =======================================================================
      const aberrationDist = 2 + amp * 4;
      const aberrationAlpha = 0.06 + amp * 0.12;
      const channels: Array<[number, number, string]> = [
        [-aberrationDist, -aberrationDist * 0.5, `rgba(255, 60, 60, ${aberrationAlpha})`],
        [aberrationDist, 0, `rgba(60, 255, 120, ${aberrationAlpha * 0.7})`],
        [0, aberrationDist * 0.5, `rgba(80, 80, 255, ${aberrationAlpha})`],
      ];
      for (const [dx, dy, color] of channels) {
        const abGrad = ctx.createRadialGradient(CX + dx, CY + dy, coreR * 0.3, CX + dx, CY + dy, coreR * 1.6);
        abGrad.addColorStop(0, color);
        abGrad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = abGrad;
        ctx.fillRect(0, 0, SIZE, SIZE);
      }

      // =======================================================================
      // 7. AMBIENT LENS FLARES (small drifting bright spots)
      // =======================================================================
      for (let i = 0; i < FLARE_COUNT; i++) {
        flareAngle[i] = flareAngle[i]! + flareSpeed[i]! * 0.008;
        flareBright[i] = 0.5 + Math.sin(t * 1.5 + i * 2.1) * 0.5;
        const fr = flareR[i]! + Math.sin(t * 0.7 + i) * 15;
        const fx = CX + Math.cos(flareAngle[i]!) * fr;
        const fy = CY + Math.sin(flareAngle[i]!) * fr;
        const fb = flareBright[i]! * (0.15 + amp * 0.5);
        if (fb > 0.05) {
          const flareSize = 2 + fb * 4;
          const fGrad = ctx.createRadialGradient(fx, fy, 0, fx, fy, flareSize);
          fGrad.addColorStop(0, `rgba(255, 255, 255, ${fb})`);
          fGrad.addColorStop(0.5, `hsla(${pal.core[0]}, 50%, 80%, ${fb * 0.4})`);
          fGrad.addColorStop(1, 'rgba(255, 255, 255, 0)');
          ctx.fillStyle = fGrad;
          ctx.beginPath();
          ctx.arc(fx, fy, flareSize, 0, PI2);
          ctx.fill();
        }
      }

      // =======================================================================
      // 8. PROCESSING SHOCKWAVE — pulsing expanding rings (processing only)
      // =======================================================================
      if (state === 'processing') {
        // Multiple staggered shockwave rings
        for (let w = 0; w < 3; w++) {
          const wavePhase = (t * 1.2 + w * 2.1) % 4.0; // 0→4 cycle
          const waveProgress = wavePhase / 4.0; // 0→1
          const waveR = 20 + waveProgress * 200;
          const waveAlpha = (1 - waveProgress) * 0.35 * (0.6 + amp * 0.4);
          if (waveAlpha > 0.01) {
            ctx.beginPath();
            ctx.arc(CX, CY, waveR, 0, PI2);
            ctx.strokeStyle = `hsla(${275 + w * 20}, 85%, ${60 + amp * 20}%, ${waveAlpha})`;
            ctx.lineWidth = 2 + (1 - waveProgress) * 3;
            ctx.shadowColor = `hsla(${275 + w * 20}, 90%, 70%, ${waveAlpha * 0.6})`;
            ctx.shadowBlur = 15 + amp * 20;
            ctx.stroke();
          }
        }
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;

        // Rapid spinning particle burst — extra orbiting sparks
        for (let s = 0; s < 30; s++) {
          const sparkAngle = t * 5 + (s / 30) * PI2 + Math.sin(t * 8 + s) * 0.3;
          const sparkR = 50 + Math.sin(t * 3 + s * 0.7) * 40 + amp * 50;
          const sx = CX + Math.cos(sparkAngle) * sparkR;
          const sy = CY + Math.sin(sparkAngle) * sparkR;
          const sparkAlpha = 0.3 + Math.sin(t * 6 + s * 2) * 0.2 + amp * 0.3;
          ctx.fillStyle = `hsla(${270 + (s % 5) * 12}, 80%, 75%, ${sparkAlpha})`;
          ctx.beginPath();
          ctx.arc(sx, sy, 1 + amp * 1.5, 0, PI2);
          ctx.fill();
        }
      }

      animRef.current = requestAnimationFrame(draw);
    }

    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- draw reads state via stateRef, no need to restart animation
  }, []);

  return (
    <button
      type="button"
      onClick={onTap}
      className="relative"
      style={{ width: 520, height: 520 }}
      aria-label={isListening ? 'Stop listening' : isSpeaking ? 'Stop speaking' : 'Start listening'}
    >
      <canvas
        ref={canvasRef}
        style={{ width: 520, height: 520 }}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// VoiceAssistantSheet — immersive voice overlay
// ---------------------------------------------------------------------------

interface VoiceAssistantSheetProps {
  open: boolean;
  onClose: () => void;
}

export function VoiceAssistantSheet({ open, onClose }: VoiceAssistantSheetProps) {
  const voice = useVoice();
  const {
    isListening,
    isProcessing,
    startListening,
    stopListening,
    clearHistory,
    isSpeaking,
    stopSpeaking,
    error,
    analyserNode,
    lastAssistantText,
    lastPosterUrl,
  } = useVoiceAssistant({
    onRequestClose: onClose,
    onShowResults: (title, items) => voice.showNovaResults(title, items as any),
  });

  const [entered, setEntered] = useState(false);
  // When wake word triggers, show blue (listening) orb immediately — before
  // startListening() fires after the mic-release delay.
  const [pendingListen, setPendingListen] = useState(false);
  // Controls whether the response text is visible (stays 5s after TTS ends)
  const [responseVisible, setResponseVisible] = useState(false);
  const responseFadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Inject Amazon Ember font
  useEffect(() => { ensureEmberFont(); }, []);

  const { wakeSignal, setVoicePhase, openVoice } = voice;

  // Mini orb: visible when sheet is closed but Alfred is speaking or listening for follow-up
  const [miniOrbVisible, setMiniOrbVisible] = useState(false);
  useEffect(() => {
    if (!open && (isSpeaking || isListening)) {
      setMiniOrbVisible(true);
    } else if (!open && !isSpeaking && !isListening) {
      setMiniOrbVisible(false);
    }
    if (open) {
      setMiniOrbVisible(false);
    }
  }, [open, isSpeaking, isListening]);

  // Show response text while speaking/processing/listening-for-followup.
  // Only start the fade timer when truly idle (no speaking, processing, or re-listening).
  useEffect(() => {
    if (lastAssistantText && (isSpeaking || isProcessing || isListening)) {
      // Active — keep visible and clear any pending fade
      if (responseFadeTimer.current) { clearTimeout(responseFadeTimer.current); responseFadeTimer.current = null; }
      setResponseVisible(true);
    } else if (lastAssistantText && !isSpeaking && !isProcessing && !isListening && responseVisible) {
      // Truly idle after a terminal response — brief hold then fade
      if (!responseFadeTimer.current) {
        responseFadeTimer.current = setTimeout(() => {
          setResponseVisible(false);
          responseFadeTimer.current = null;
        }, 1500);
      }
    }
    if (!lastAssistantText) {
      setResponseVisible(false);
      if (responseFadeTimer.current) { clearTimeout(responseFadeTimer.current); responseFadeTimer.current = null; }
    }
  }, [lastAssistantText, isSpeaking, isProcessing, isListening, responseVisible]);

  // Cleanup fade timer on unmount
  useEffect(() => () => { if (responseFadeTimer.current) clearTimeout(responseFadeTimer.current); }, []);

  // Clear pending-listen as soon as real listening begins
  useEffect(() => {
    if (isListening) setPendingListen(false);
  }, [isListening]);

  // Report phase to VoiceContext so wake word knows when to listen
  useEffect(() => {
    if (isSpeaking) setVoicePhase('speaking');
    else if (isProcessing) setVoicePhase('processing');
    else if (isListening) setVoicePhase('listening');
    else setVoicePhase('idle');
  }, [isListening, isProcessing, isSpeaking, setVoicePhase]);

  // Stable refs for callbacks
  const startListeningRef = useRef(startListening);
  startListeningRef.current = startListening;
  const clearHistoryRef = useRef(clearHistory);
  clearHistoryRef.current = clearHistory;
  const stopSpeakingRef = useRef(stopSpeaking);
  stopSpeakingRef.current = stopSpeaking;

  // React to wake word — start listening immediately each time wake word is detected.
  // VoiceContext stops SpeechRecognition when sheet opens (wakeWordEnabled = !voiceOpen).
  // startListening has built-in retry if the mic is still held by SpeechRecognition.
  const prevWake = useRef(wakeSignal);
  const hasAutoStarted = useRef(false);
  useEffect(() => {
    if (wakeSignal === prevWake.current) return;
    prevWake.current = wakeSignal;
    if (!open) return;
    // Ignore wake word if we're actively listening, processing, or speaking —
    // this catches buffered SpeechRecognition results from the user's own
    // speech (e.g., "Reset Alfred" contains the wake word "Alfred")
    if (isListening || isProcessing || isSpeaking) {
      console.log('[Voice] Wake word ignored — currently active:', { isListening, isProcessing, isSpeaking });
      return;
    }
    clearHistoryRef.current();
    hasAutoStarted.current = true;
    setPendingListen(true); // Show blue orb immediately
    startListeningRef.current(); // Start immediately — retry handles mic contention
  }, [wakeSignal, open, isListening, isProcessing, isSpeaking]);

  // Auto-start listening when sheet opens via wake word.
  // Handles the case where wakeSignal fires before sheet is mounted.
  // Does NOT auto-start on manual open (mic tap) — user wants idle purple orb.
  useEffect(() => {
    if (open && !hasAutoStarted.current && wakeSignal > 0 && wakeSignal === prevWake.current) {
      // wakeSignal already processed but sheet just mounted — catch up
      hasAutoStarted.current = true;
      clearHistoryRef.current();
      setPendingListen(true); // Show blue orb immediately
      startListeningRef.current(); // Start immediately
    }
    if (!open) {
      hasAutoStarted.current = false;
      setPendingListen(false);
    }
  }, [open, wakeSignal]);

  // Animate in — slight delay lets the orb animation settle before the
  // sheet becomes visible, avoiding any perceptible hitch on appear.
  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => setEntered(true), 250);
      return () => clearTimeout(timer);
    } else {
      setEntered(false);
    }
  }, [open]);

  // Auto-close in expanded mode: once the response text fades out and
  // we're fully idle, dismiss the sheet. The 5-second response hold gives
  // the user time to read; after that, the orb disappears.
  useEffect(() => {
    if (!open) return;
    // Only close when truly idle: not listening, not processing, not speaking,
    // and no response text visible (it's had its 5-second hold).
    if (isListening || isProcessing || isSpeaking || responseVisible) return;
    // Don't auto-close if we never started a conversation (pure idle open)
    if (!lastAssistantText) return;
    // Close promptly after response fades
    const t = setTimeout(() => onClose(), 300);
    return () => clearTimeout(t);
  }, [open, isListening, isProcessing, isSpeaking, responseVisible, lastAssistantText, onClose]);

  const handleOrbTap = useCallback(() => {
    if (isSpeaking) {
      stopSpeaking();
    } else if (isListening) {
      stopListening();
    } else if (!isProcessing) {
      // Tap to manually start listening (fallback if wake word isn't used)
      clearHistoryRef.current();
      startListening();
    }
  }, [isListening, isProcessing, isSpeaking, startListening, stopListening, stopSpeaking]);

  // Mini orb overlay — Alfred speaking over a modal when sheet is closed
  const miniOrbPortal = (!open && miniOrbVisible) ? createPortal(
    <div
      onClick={() => { setMiniOrbVisible(false); openVoice(); }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10002,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.7)',
        cursor: 'pointer',
      }}
    >
      <div style={{ transform: 'scale(0.55)' }}>
        <LivingOrb
          isListening={isListening}
          isProcessing={isProcessing}
          isSpeaking={isSpeaking}
          analyserNode={analyserNode}
          onTap={() => { setMiniOrbVisible(false); openVoice(); }}
        />
      </div>
    </div>,
    document.body,
  ) : null;

  // Main sheet — ALWAYS rendered (orb stays alive, canvas never re-inits).
  // Hidden via visibility+pointer-events when closed so the animation loop
  // keeps running and opening is instant with zero jank.
  const mainPortal = createPortal(
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10001,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.85)',
        overflow: 'hidden',
        visibility: open ? 'visible' : 'hidden',
        pointerEvents: open ? 'auto' : 'none',
        opacity: open ? 1 : 0,
        transition: open ? 'none' : 'opacity 0.2s ease-out, visibility 0s 0.2s',
      }}
    >
      {/* Content */}
      <div
        className="flex flex-col items-center justify-center gap-6 px-8 w-full"
        style={{
          opacity: entered ? 1 : 0,
          transform: entered ? 'scale(1)' : 'scale(0.85)',
          transition: 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.1s',
        }}
      >
        {/* The orb */}
        <LivingOrb
          isListening={isListening || pendingListen}
          isProcessing={isProcessing}
          isSpeaking={isSpeaking}
          analyserNode={analyserNode}
          onTap={handleOrbTap}
        />

        {/* Transcript & response text */}
        <div style={{ minHeight: 80, maxWidth: 640, width: '100%', fontFamily: EMBER_FAMILY }}>
          {/* Listening indicator — only when no response text is showing */}
          {(isListening || pendingListen) && !responseVisible && (
            <p
              className="text-center text-[20px] text-white/50 mt-4"
              style={{
                fontWeight: 300,
                animation: 'listeningPulse 2s ease-in-out infinite',
              }}
            >
              Listening
            </p>
          )}
          {/* Alfred response — poster + text side by side for confirmations */}
          {lastAssistantText && responseVisible && (
            <div
              style={{
                display: 'flex',
                flexDirection: lastPosterUrl ? 'row' : 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: lastPosterUrl ? 24 : 0,
                marginTop: 16,
                animation: 'voiceTextFadeIn 0.5s ease-out',
              }}
            >
              {lastPosterUrl && (
                <img
                  src={lastPosterUrl}
                  alt="Poster"
                  style={{
                    width: 150,
                    height: 225,
                    objectFit: 'cover',
                    borderRadius: 10,
                    flexShrink: 0,
                    boxShadow: '0 8px 32px rgba(0,0,0,0.8), 0 2px 8px rgba(0,0,0,0.5)',
                    border: '1px solid rgba(255,255,255,0.12)',
                  }}
                />
              )}
              <p
                style={{
                  fontSize: lastPosterUrl ? 22 : 24,
                  textAlign: lastPosterUrl ? 'left' : 'center',
                  color: 'rgba(255,255,255,0.92)',
                  fontWeight: 400,
                  letterSpacing: '0.01em',
                  lineHeight: 1.45,
                  margin: 0,
                  maxWidth: lastPosterUrl ? 360 : '100%',
                }}
              >
                {lastAssistantText}
              </p>
            </div>
          )}
        </div>

        {/* Error only */}
        {error && (
          <div style={{ minHeight: 48, fontFamily: EMBER_FAMILY, fontStyle: 'italic' }}>
            <p className="text-center text-[26px] tracking-wide text-red-400" style={{ fontWeight: 400 }}>
              {error}
            </p>
          </div>
        )}
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes voiceTextFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes thinkingDot {
          0%, 80%, 100% { transform: scale(1); opacity: 0.3; }
          40% { transform: scale(1.8); opacity: 1; }
        }
        @keyframes listeningPulse {
          0%, 100% { opacity: 0.35; }
          50% { opacity: 0.7; }
        }
      `}</style>
    </div>,
    document.body,
  );

  return <>{miniOrbPortal}{mainPortal}</>;
}
