// ---------------------------------------------------------------------------
// Layout tokens — MovieDetailModal ExpandedLayout
//
// Minimal cinema-style: full-bleed backdrop, content anchored at bottom,
// clean typography, pill actions. Think Google TV / Apple TV+.
// ---------------------------------------------------------------------------

const CARD_BG = '#111318';

export const TOKENS = {
  tablet: {
    overlay: {
      alignItems: 'stretch' as const,
      background: 'transparent',
      padding: 0 as number | string,
    },
    card: {
      maxWidth: undefined as string | undefined,
      height: '100%',
      borderRadius: 0 as number | string,
      boxShadow: 'none',
      bgUltraBlur: CARD_BG,
    },
    close: {
      top: '24px',
      right: '24px',
      svgSize: '28',
    },
    ultraBlurVariant: 'tablet' as 'tablet' | 'web',
    ultraBlurMask: 'linear-gradient(to bottom, transparent 20%, black 50%)',
    scrim: 'linear-gradient(to bottom, transparent 30%, rgba(0,0,0,0.4) 50%, rgba(0,0,0,0.85) 100%)',
    backdrop: {
      imgFilter: undefined as string | undefined,
    },
    logo: {
      maxWidth: '700px',
      maxHeight: '200px',
      marginBottom: '14px',
      filter: 'drop-shadow(0 3px 12px rgba(0,0,0,0.7))',
    },
    titleFontSize: '34px',
    contentAlign: 'center' as 'left' | 'center',
    contentPadding: '0 36px 32px',
    meta: {
      gap: '8px',
      fontSize: '15px',
      mbWithRatings: '10px',
      mbNoRatings: '14px',
    },
    ratingsSize: 'sm' as 'sm' | 'md' | 'lg',
    plot: {
      fontSize: '16px',
      lineHeight: 1.55,
      lineClamp: 2,
      maxWidth: '540px',
      wrapperMb: '18px',
    },
    pill: {
      gap: '14px',
      padding: '16px 32px',
      fontSize: '18px',
      iconSize: '20',
    },
    upgrade: {
      padding: '24px 32px 200px',
      mask: 'linear-gradient(to bottom, transparent 0%, black 8%)',
      headingFontSize: '20px',
      headingMargin: '0 0 12px',
      optionsGap: '12px',
      optMaxWidth: '200px',
      optPadding: '18px 12px',
      optNameFontSize: '18px',
      optDescFontSize: '14px',
    },
  },

  desktop: {
    overlay: {
      alignItems: 'center' as const,
      background: '#111318',
      padding: '20px' as number | string,
    },
    card: {
      maxWidth: '1500px' as string | undefined,
      height: '90vh',
      borderRadius: '20px' as number | string,
      boxShadow: '0 32px 100px rgba(0,0,0,0.8)',
      bgUltraBlur: '#111318',
    },
    close: {
      top: '28px',
      right: '36px',
      svgSize: '28',
    },
    ultraBlurVariant: 'web' as 'tablet' | 'web',
    ultraBlurMask: 'linear-gradient(to bottom, transparent 25%, black 55%)',
    scrim: 'linear-gradient(to bottom, transparent 35%, rgba(21,16,42,0.4) 55%, rgba(21,16,42,0.88) 100%)',
    backdrop: {
      imgFilter: 'contrast(1.08) saturate(1.12)' as string | undefined,
    },
    logo: {
      maxWidth: '800px',
      maxHeight: '240px',
      marginBottom: '16px',
      filter: 'drop-shadow(0 4px 18px rgba(0,0,0,0.7))',
    },
    titleFontSize: '48px',
    contentAlign: 'center' as 'left' | 'center',
    contentPadding: '0 64px 48px',
    meta: {
      gap: '10px',
      fontSize: '16px',
      mbWithRatings: '12px',
      mbNoRatings: '18px',
    },
    ratingsSize: 'sm' as 'sm' | 'md' | 'lg',
    plot: {
      fontSize: '16px',
      lineHeight: 1.65,
      lineClamp: 2,
      maxWidth: '650px',
      wrapperMb: '24px',
    },
    pill: {
      gap: '16px',
      padding: '16px 34px',
      fontSize: '20px',
      iconSize: '22',
    },
    upgrade: {
      padding: '32px 48px 280px',
      mask: 'linear-gradient(to bottom, transparent 0%, black 10%)',
      headingFontSize: '24px',
      headingMargin: '0 0 16px',
      optionsGap: '16px',
      optMaxWidth: '220px',
      optPadding: '22px 16px',
      optNameFontSize: '22px',
      optDescFontSize: '15px',
    },
  },
};
