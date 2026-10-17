// ---------------------------------------------------------------------------
// Layout tokens — TvDetailModal ExpandedLayout (cinema-style)
//
// Mirrors MovieDetailModal token structure: full-bleed backdrop, content
// anchored at bottom, clearlogo centered in upper portion, pill actions.
// ---------------------------------------------------------------------------

const CARD_BG = 'var(--color-kiosk-black)';

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
      filter: 'drop-shadow(0 3px 12px rgba(0,0,0,0.7))',
    },
    titleFontSize: '34px',
    contentPadding: '0 36px 32px',
    meta: {
      fontSize: '22px',
      gap: '14px',
    },
    plot: {
      fontSize: '18px',
      lineHeight: 1.55,
      lineClamp: 2,
      maxWidth: '540px',
      marginBottom: '18px',
    },
    pill: {
      gap: '14px',
      padding: '16px 32px',
      fontSize: '18px',
      iconSize: '20',
    },
    episodeShelf: {
      padding: '32px 24px 32px',
      thumbnailWidth: '160px',
      thumbnailHeight: '90px',
      titleFontSize: '15px',
      metaFontSize: '13px',
      overviewFontSize: '13px',
      overviewClamp: 2,
      rowGap: '12px',
      rowPadding: '12px 16px',
    },
  },

  desktop: {
    overlay: {
      alignItems: 'center' as const,
      background: 'var(--color-kiosk-black)',
      padding: '20px' as number | string,
    },
    card: {
      maxWidth: '1500px' as string | undefined,
      height: '90vh',
      borderRadius: '20px' as number | string,
      boxShadow: '0 32px 100px rgba(0,0,0,0.8)',
      bgUltraBlur: 'var(--color-kiosk-black)',
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
      filter: 'drop-shadow(0 4px 18px rgba(0,0,0,0.7))',
    },
    titleFontSize: '48px',
    contentPadding: '0 64px 48px',
    meta: {
      fontSize: '26px',
      gap: '16px',
    },
    plot: {
      fontSize: '22px',
      lineHeight: 1.6,
      lineClamp: 2,
      maxWidth: '1100px',
      marginBottom: '28px',
    },
    pill: {
      gap: '16px',
      padding: '16px 34px',
      fontSize: '20px',
      iconSize: '22',
    },
    episodeShelf: {
      padding: '40px 48px 48px',
      thumbnailWidth: '200px',
      thumbnailHeight: '113px',
      titleFontSize: '17px',
      metaFontSize: '14px',
      overviewFontSize: '14px',
      overviewClamp: 2,
      rowGap: '16px',
      rowPadding: '14px 20px',
    },
  },
};
