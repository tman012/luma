/**
 * v173 (Designer): every tracker and origin chip is neutral glass. No per-tracker hue (the old IPT
 * blue / PHD purple / cycling palette), no per-origin hue (Genre Roulette violet): a white 8% fill,
 * 70% white text, a faint white edge.
 */
export type ChipColor = { bg: string; fg: string; border: string };

export const NEUTRAL_GLASS: ChipColor = Object.freeze({
  bg: 'rgba(255,255,255,0.08)',
  fg: 'rgba(255,255,255,0.7)',
  border: 'rgba(255,255,255,0.1)',
}) as ChipColor;
