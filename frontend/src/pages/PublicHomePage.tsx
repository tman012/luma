/**
 * PublicHomePage — Landing page for Luma (redesigned with real library content).
 *
 * Layout:
 *   - Sticky top bar: Luma icon + "Luma" text, user icon
 *   - Welcome section: "Welcome to Luma" at top, left-aligned
 *   - Recently Added Movies carousel: backdrop carousel with title overlay
 *   - Recently Added TV carousel: backdrop carousel with title overlay
 */

import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router';
import { UserMenu } from '@/components/UserMenu';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CarouselItem {
  ratingKey: number | string;
  title: string;
  backdropUrl: string;
}

// ---------------------------------------------------------------------------
// Top bar
// ---------------------------------------------------------------------------

function TopBar() {
  const navigate = useNavigate();

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 50,
        height: 60,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        paddingLeft: 20,
        paddingRight: 20,
        background: 'rgba(15,15,15,0.92)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      {/* Logo + name */}
      <button
        type="button"
        onClick={() => navigate('/')}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
          flexShrink: 0,
          opacity: 1,
          transition: 'opacity 0.15s',
        }}
      >
        <img
          src="/static/images/icon-512.png"
          alt="Luma"
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            objectFit: 'cover',
          }}
        />
        <span
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: 'rgba(255,255,255,0.95)',
            letterSpacing: '-0.4px',
          }}
        >
          Luma
        </span>
      </button>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* User menu with dropdown */}
      <UserMenu />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Welcome section (compact, left-aligned)
// ---------------------------------------------------------------------------

function WelcomeSection() {
  return (
    <div
      style={{
        paddingTop: 90,
        paddingBottom: 48,
        paddingLeft: 24,
        paddingRight: 24,
        background: '#1a0a1e',
      }}
    >
      <h1
        style={{
          margin: 0,
          fontSize: 'clamp(32px, 5vw, 48px)',
          fontWeight: 900,
          color: 'rgba(255,255,255,0.98)',
          letterSpacing: '-1.2px',
          lineHeight: 1.1,
          textAlign: 'left',
        }}
      >
        Welcome to Luma
      </h1>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Backdrop carousel card
// ---------------------------------------------------------------------------

function BackdropCard({ item }: { item: CarouselItem }) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  return (
    <div
      style={{
        position: 'relative',
        width: 320,
        height: 180,
        borderRadius: 12,
        overflow: 'hidden',
        background: 'rgba(255,255,255,0.06)',
        flexShrink: 0,
        cursor: 'pointer',
        transition: 'transform 0.3s ease',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.transform = 'scale(1.02)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.transform = 'scale(1)';
      }}
    >
      {item.backdropUrl && !imgError ? (
        <img
          src={item.backdropUrl}
          alt={item.title}
          loading="lazy"
          onLoad={() => setImgLoaded(true)}
          onError={() => setImgError(true)}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            opacity: imgLoaded ? 1 : 0,
            transition: 'opacity 0.3s ease',
            display: 'block',
          }}
        />
      ) : (
        <div
          style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'rgba(255,255,255,0.15)',
            fontSize: 12,
          }}
        >
          No Backdrop
        </div>
      )}

      {/* Dark overlay + title at bottom */}
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          background: 'linear-gradient(transparent, rgba(0,0,0,0.7))',
          padding: '16px',
          display: 'flex',
          alignItems: 'flex-end',
          height: '100%',
        }}
      >
        <h4
          style={{
            margin: 0,
            fontSize: 14,
            fontWeight: 600,
            color: 'rgba(255,255,255,0.95)',
            lineHeight: 1.3,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {item.title}
        </h4>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Media carousel section
// ---------------------------------------------------------------------------

interface CarouselSectionProps {
  title: string;
  apiEndpoint: string;
  mediaType: 'movies' | 'tv';
}

function CarouselSection({ title, apiEndpoint, mediaType }: CarouselSectionProps) {
  const [items, setItems] = useState<CarouselItem[]>([]);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function fetchContent(): Promise<void> {
      try {
        const res = await fetch(apiEndpoint);
        if (!res.ok) {
          console.error(`API error: ${res.status}`);
          setLoading(false);
          return;
        }
        const data = await res.json();

        // Parse response based on endpoint
        let contentArray: any[] = [];
        
        // Handle movies endpoint: returns { items: [...] }
        if (mediaType === 'movies') {
          contentArray = data.items || data.movies || [];
        } 
        // Handle TV episodes endpoint: returns { episodes: [...] }
        else if (mediaType === 'tv') {
          contentArray = data.episodes || data.tv_shows || [];
        } 
        // Fallback for array responses
        else if (Array.isArray(data)) {
          contentArray = data;
        }

        // Convert to CarouselItem format
        const carouselItems = contentArray.map((item: any) => {
          // For movies: use title and backdrop_url
          // For TV: use show_title and poster_url
          const itemTitle = item.title || item.show_title || '';
          const itemRatingKey = item.rating_key || item.show_rating_key || item.ratingKey || item.id || '';
          const itemBackdropUrl = item.backdrop_url || item.poster_url || item.backdropUrl || '';

          return {
            ratingKey: itemRatingKey,
            title: itemTitle,
            backdropUrl: itemBackdropUrl,
          };
        });

        setItems(carouselItems);
        setLoading(false);
      } catch (error) {
        console.error('Fetch error:', error);
        setLoading(false);
      }
    }

    fetchContent();
  }, [apiEndpoint, mediaType]);

  if (loading) {
    return (
      <div
        style={{
          paddingLeft: 24,
          paddingRight: 24,
          paddingBottom: 48,
          background: '#1a0a1e',
        }}
      >
        <h2
          style={{
            margin: '0 0 20px',
            fontSize: 20,
            fontWeight: 700,
            color: 'rgba(255,255,255,0.88)',
          }}
        >
          {title}
        </h2>
        <div style={{ display: 'flex', gap: 12, overflowX: 'auto' }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="skeleton-shimmer"
              style={{
                width: 320,
                height: 180,
                borderRadius: 12,
                flexShrink: 0,
              }}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        paddingLeft: 24,
        paddingRight: 24,
        paddingBottom: 48,
        background: '#1a0a1e',
      }}
    >
      <h2
        style={{
          margin: '0 0 20px',
          fontSize: 20,
          fontWeight: 700,
          color: 'rgba(255,255,255,0.88)',
        }}
      >
        {title}
      </h2>

      <div
        ref={scrollRef}
        style={{
          display: 'flex',
          gap: 12,
          overflowX: 'auto',
          paddingBottom: 8,
          scrollbarWidth: 'thin',
          scrollBehavior: 'smooth',
        }}
      >
        {items.length > 0 ? (
          items.map((item) => <BackdropCard key={item.ratingKey} item={item} />)
        ) : (
          <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: 14 }}>No content available</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function PublicHomePage() {
  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#1a0a1e',
        color: 'white',
        overflowX: 'hidden',
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <TopBar />
      <WelcomeSection />
      <CarouselSection
        title="Recently Added Movies"
        apiEndpoint="/api/library/recent/cached?type=movie&limit=20"
        mediaType="movies"
      />
      <CarouselSection
        title="Recently Added TV"
        apiEndpoint="/api/library/episodes/cached?limit=20"
        mediaType="tv"
      />
      <div style={{ paddingBottom: 48 }} />
    </div>
  );
}
