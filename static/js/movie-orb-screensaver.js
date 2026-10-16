/**
 * Movie Orb Screensaver
 *
 * A premium idle screensaver featuring slowly drifting "movie orbs" -
 * circular backdrop images with clearlogo overlays of recent movies.
 *
 * Configuration (set before this script loads):
 *   window.SCREENSAVER_ENABLED = true/false
 *   window.SCREENSAVER_IDLE_TIMEOUT_MS = 10000 (10s for testing, 90000 for production)
 *   window.SCREENSAVER_ORB_COUNT = 10
 */

(function() {
    'use strict';

    // =============================================================================
    // Configuration
    // =============================================================================

    const CONFIG = {
        enabled: window.SCREENSAVER_ENABLED !== false,
        idleTimeoutMs: window.SCREENSAVER_IDLE_TIMEOUT_MS || 10000,
        orbCount: window.SCREENSAVER_ORB_COUNT || 8,
        minRadius: 150,
        maxRadius: 150,  // All orbs same size
        speedMin: 50,           // Moderate drift speed
        speedMax: 110,          // Moderate max speed
        edgeMargin: 25,
        collisionSoftness: 2.5, // Stronger bounce to prevent bunching
        repulsionDistance: 80,  // Repel orbs before they touch
        repulsionStrength: 30,  // Strength of repulsion force
        exitFadeMs: 200,
        enterFadeMs: 400,
        cooldownMs: 3000,
        backdropOpacity: 0.3,   // 30% opacity for orb backdrop
        slideshowInterval: 5000, // 5 seconds between backdrop changes
        slideshowOpacity: 0.25,  // Background slideshow opacity
        apiEndpoint: '/api/screensaver/movies'
    };

    // =============================================================================
    // State
    // =============================================================================

    let isActive = false;
    let lastActivityTime = Date.now();
    let lastExitTime = 0;
    let idleCheckInterval = null;
    let animationFrameId = null;
    let movies = [];
    let orbs = [];
    let overlay = null;
    let canvas = null;
    let ctx = null;
    let preloadedImages = new Map();
    let slideshowInterval = null;
    let currentBackdropIndex = 0;
    let backdropContainerA = null;
    let backdropContainerB = null;
    let activeBackdrop = 'A';
    let homeControl = null;
    let homeControlTimer = null;
    let longPressIndicator = null;
    let longPressStartTime = 0;
    let longPressAnimFrame = null;
    const LONG_PRESS_MS = 1500;
    const HOME_REVEAL_MS = 3500;

    // =============================================================================
    // Activity Tracking
    // =============================================================================

    // Only keyboard exits screensaver automatically - touch is handled separately for orb interaction
    const TRACKED_EVENTS = ['keydown'];
    let moveDebounceTimeout = null;

    function recordActivity() {
        lastActivityTime = Date.now();
    }

    function handleActivityEvent(e) {
        recordActivity();
        if (e.type === 'keydown' && isActive) {
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                exitScreensaver();
            }
        }
    }

    function startActivityTracking() {
        TRACKED_EVENTS.forEach(type => {
            document.addEventListener(type, handleActivityEvent, { capture: true });
        });
        // Track mouse/touch for idle detection but don't exit screensaver
        ['pointerdown', 'pointermove', 'touchstart', 'touchmove', 'click', 'wheel'].forEach(type => {
            document.addEventListener(type, () => { lastActivityTime = Date.now(); }, { passive: true, capture: true });
        });
    }

    // =============================================================================
    // Exit UI Controls (Home button + Long-press)
    // =============================================================================

    function showHomeControl() {
        if (!homeControl) return;
        homeControl.style.opacity = '1';
        homeControl.style.pointerEvents = 'auto';
        if (homeControlTimer) clearTimeout(homeControlTimer);
        homeControlTimer = setTimeout(hideHomeControl, HOME_REVEAL_MS);
    }

    function hideHomeControl() {
        if (!homeControl) return;
        homeControl.style.opacity = '0';
        homeControl.style.pointerEvents = 'none';
        if (homeControlTimer) {
            clearTimeout(homeControlTimer);
            homeControlTimer = null;
        }
    }

    function startLongPress(clientX, clientY) {
        if (!longPressIndicator) return;
        longPressIndicator.style.left = (clientX - 28) + 'px';
        longPressIndicator.style.top = (clientY - 28) + 'px';
        longPressIndicator.style.opacity = '1';
        longPressStartTime = performance.now();
        const circumference = 2 * Math.PI * 24;
        const progressCircle = longPressIndicator.querySelector('#longpress-progress');

        function animateProgress() {
            const elapsed = performance.now() - longPressStartTime;
            const progress = Math.min(elapsed / LONG_PRESS_MS, 1);
            if (progressCircle) {
                progressCircle.setAttribute('stroke-dashoffset', String(circumference * (1 - progress)));
            }
            if (progress >= 1) {
                cancelLongPress();
                exitScreensaver();
                return;
            }
            longPressAnimFrame = requestAnimationFrame(animateProgress);
        }

        longPressAnimFrame = requestAnimationFrame(animateProgress);
    }

    function cancelLongPress() {
        if (longPressAnimFrame) {
            cancelAnimationFrame(longPressAnimFrame);
            longPressAnimFrame = null;
        }
        if (longPressIndicator) {
            longPressIndicator.style.opacity = '0';
            const progressCircle = longPressIndicator.querySelector('#longpress-progress');
            if (progressCircle) {
                const circumference = 2 * Math.PI * 24;
                progressCircle.setAttribute('stroke-dashoffset', String(circumference));
            }
        }
        longPressStartTime = 0;
    }

    // =============================================================================
    // Touch/Drag Interaction State
    // =============================================================================

    let draggedOrb = null;
    let dragOffset = { x: 0, y: 0 };
    let touchStartTime = 0;
    let touchStartPos = { x: 0, y: 0 };
    let isDragging = false;

    // =============================================================================
    // Idle Detection
    // =============================================================================

    function checkIdle() {
        if (isActive || !CONFIG.enabled) return;
        const idleTime = Date.now() - lastActivityTime;
        const sinceExit = Date.now() - lastExitTime;
        if (sinceExit < CONFIG.cooldownMs) return;
        if (idleTime >= CONFIG.idleTimeoutMs) {
            enterScreensaver();
        }
    }

    function startIdleCheck() {
        if (!idleCheckInterval) {
            idleCheckInterval = setInterval(checkIdle, 1000);
        }
    }

    // =============================================================================
    // Data Fetching
    // =============================================================================

    let fetchPromise = null;

    async function fetchMovies() {
        if (fetchPromise) return fetchPromise;
        fetchPromise = (async () => {
            try {
                const res = await fetch(`${CONFIG.apiEndpoint}?limit=20`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                if (data.success && Array.isArray(data.movies)) {
                    movies = data.movies.filter(m => m.backdropUrl);
                    console.log(`[OrbScreensaver] Loaded ${movies.length} movies`);
                }
            } catch (e) {
                console.error('[OrbScreensaver] Fetch error:', e);
            } finally {
                fetchPromise = null;
            }
        })();
        return fetchPromise;
    }

    function preloadImage(url) {
        if (!url) return Promise.resolve(null);
        if (preloadedImages.has(url)) return Promise.resolve(preloadedImages.get(url));

        return new Promise(resolve => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => {
                preloadedImages.set(url, img);
                resolve(img);
            };
            img.onerror = () => {
                console.warn('[OrbScreensaver] Failed to load:', url);
                resolve(null);
            };
            img.src = url;
        });
    }

    // =============================================================================
    // Orb Class
    // =============================================================================

    class Orb {
        constructor(movie, x, y, radius, vx, vy) {
            this.movie = movie;
            this.x = x;
            this.y = y;
            this.radius = radius;
            this.vx = vx;
            this.vy = vy;
            this.backdropImage = null;
            this.overlayImage = null;  // clearlogo
            this.overlayType = null;   // 'clearlogo' or 'text'
            this.glowPhase = Math.random() * Math.PI * 2;
            this.loadImages();
        }

        async loadImages() {
            // Load backdrop
            if (this.movie.backdropUrl) {
                this.backdropImage = await preloadImage(this.movie.backdropUrl);
            }

            // Load clearlogo, fallback to text
            if (this.movie.clearlogoUrl) {
                this.overlayImage = await preloadImage(this.movie.clearlogoUrl);
                if (this.overlayImage) {
                    this.overlayType = 'clearlogo';
                    console.log('[OrbScreensaver] Loaded clearlogo for:', this.movie.title);
                }
            }

            if (!this.overlayImage) {
                this.overlayType = 'text';
                console.log('[OrbScreensaver] Using text fallback for:', this.movie.title);
            }
        }

        update(dt, width, height, otherOrbs) {
            this.x += this.vx * dt;
            this.y += this.vy * dt;

            const margin = CONFIG.edgeMargin;
            if (this.x - this.radius < margin) {
                this.x = margin + this.radius;
                this.vx = Math.abs(this.vx) * 0.85;
            }
            if (this.x + this.radius > width - margin) {
                this.x = width - margin - this.radius;
                this.vx = -Math.abs(this.vx) * 0.85;
            }
            if (this.y - this.radius < margin) {
                this.y = margin + this.radius;
                this.vy = Math.abs(this.vy) * 0.85;
            }
            if (this.y + this.radius > height - margin) {
                this.y = height - margin - this.radius;
                this.vy = -Math.abs(this.vy) * 0.85;
            }

            for (const other of otherOrbs) {
                if (other === this) continue;
                const dx = other.x - this.x;
                const dy = other.y - this.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                const minDist = this.radius + other.radius;

                // Repulsion force at distance to prevent clustering
                const repulsionDist = minDist + CONFIG.repulsionDistance * 0.3;
                if (dist < repulsionDist && dist > 0) {
                    const nx = dx / dist;
                    const ny = dy / dist;

                    if (dist < minDist) {
                        // Direct collision - transfer momentum and separate
                        const overlap = (minDist - dist) / 2;
                        this.x -= nx * overlap * CONFIG.collisionSoftness;
                        this.y -= ny * overlap * CONFIG.collisionSoftness;

                        // Calculate relative velocity
                        const dvx = this.vx - other.vx;
                        const dvy = this.vy - other.vy;
                        const dvn = dvx * nx + dvy * ny;

                        // Only apply if moving toward each other
                        if (dvn > 0) {
                            // Elastic collision - transfer momentum
                            this.vx -= dvn * nx * 1.2;
                            this.vy -= dvn * ny * 1.2;
                            other.vx += dvn * nx * 1.2;
                            other.vy += dvn * ny * 1.2;
                        }
                    } else {
                        // Soft repulsion to keep orbs apart
                        const repulsionFactor = 1 - (dist - minDist) / CONFIG.repulsionDistance;
                        const force = repulsionFactor * CONFIG.repulsionStrength * dt;
                        this.vx -= nx * force;
                        this.vy -= ny * force;
                    }
                }
            }

            const speed = Math.sqrt(this.vx * this.vx + this.vy * this.vy);
            if (speed > 0 && speed < CONFIG.speedMin) {
                // Boost up to minimum speed
                const factor = CONFIG.speedMin / speed;
                this.vx *= factor;
                this.vy *= factor;
            } else if (speed > CONFIG.speedMax) {
                // Gradually slow down high-speed orbs (from flings) with friction
                // This allows momentum transfer before settling to normal speed
                const friction = 0.98;
                this.vx *= friction;
                this.vy *= friction;
            }

            this.glowPhase += dt * 0.5;
        }

        draw(ctx) {
            const r = this.radius;

            ctx.save();
            ctx.translate(this.x, this.y);

            // Clip to circle
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.closePath();
            ctx.clip();

            // Draw dark base
            ctx.fillStyle = '#0a0a12';
            ctx.fillRect(-r, -r, r * 2, r * 2);

            // Draw backdrop image at reduced opacity, zoomed out more
            if (this.backdropImage) {
                ctx.globalAlpha = CONFIG.backdropOpacity;

                const img = this.backdropImage;
                const imgAspect = img.width / img.height;

                // Zoom to fill orb without black bars
                const scale = 1.0;
                let drawW, drawH;

                if (imgAspect > 1) {
                    drawH = r * 2 * scale;
                    drawW = drawH * imgAspect;
                } else {
                    drawW = r * 2 * scale;
                    drawH = drawW / imgAspect;
                }

                ctx.drawImage(img, -drawW / 2, -drawH / 2, drawW, drawH);
                ctx.globalAlpha = 1.0;
            }

            // Draw overlay: clearlogo or text fallback
            if (this.overlayImage) {
                const overlay = this.overlayImage;
                const overlayAspect = overlay.width / overlay.height;

                // Scale clearlogo to fit nicely in the orb - bigger sizing
                const maxLogoWidth = r * 1.8;
                const maxLogoHeight = r * 1.1;
                const minLogoWidth = r * 1.2;  // Minimum size for small logos

                let overlayW, overlayH;
                if (overlayAspect > maxLogoWidth / maxLogoHeight) {
                    overlayW = maxLogoWidth;
                    overlayH = overlayW / overlayAspect;
                } else {
                    overlayH = maxLogoHeight;
                    overlayW = overlayH * overlayAspect;
                }

                // Ensure small logos are scaled up
                if (overlayW < minLogoWidth) {
                    const scale = minLogoWidth / overlayW;
                    overlayW *= scale;
                    overlayH *= scale;
                }

                // Draw with shadow for visibility
                ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
                ctx.shadowBlur = 20;
                ctx.shadowOffsetX = 0;
                ctx.shadowOffsetY = 5;

                ctx.drawImage(overlay, -overlayW / 2, -overlayH / 2, overlayW, overlayH);

                ctx.shadowColor = 'transparent';
                ctx.shadowBlur = 0;

            } else if (this.overlayType === 'text') {
                // Text fallback - faded movie title
                const title = this.movie.title || 'Unknown';
                const maxWidth = r * 1.6;

                ctx.fillStyle = 'rgba(255, 255, 255, 0.6)';
                ctx.font = `bold ${Math.floor(r * 0.25)}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                // Add shadow for readability
                ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
                ctx.shadowBlur = 10;
                ctx.shadowOffsetX = 0;
                ctx.shadowOffsetY = 2;

                // Word wrap if needed
                const words = title.split(' ');
                const lines = [];
                let currentLine = '';

                for (const word of words) {
                    const testLine = currentLine ? currentLine + ' ' + word : word;
                    const metrics = ctx.measureText(testLine);
                    if (metrics.width > maxWidth && currentLine) {
                        lines.push(currentLine);
                        currentLine = word;
                    } else {
                        currentLine = testLine;
                    }
                }
                if (currentLine) lines.push(currentLine);

                const lineHeight = r * 0.3;
                const startY = -((lines.length - 1) * lineHeight) / 2;

                for (let i = 0; i < lines.length; i++) {
                    ctx.fillText(lines[i], 0, startY + i * lineHeight);
                }

                ctx.shadowColor = 'transparent';
                ctx.shadowBlur = 0;
            }

            ctx.restore();

            // Draw 3D orb effects
            this.drawOrbEffects(ctx);
        }

        drawOrbEffects(ctx) {
            const r = this.radius;

            ctx.save();
            ctx.translate(this.x, this.y);

            // Radial edge darkening
            const edgeGrad = ctx.createRadialGradient(0, 0, r * 0.5, 0, 0, r);
            edgeGrad.addColorStop(0, 'rgba(0,0,0,0)');
            edgeGrad.addColorStop(0.6, 'rgba(0,0,0,0.1)');
            edgeGrad.addColorStop(0.85, 'rgba(0,0,0,0.35)');
            edgeGrad.addColorStop(1, 'rgba(0,0,0,0.65)');
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fillStyle = edgeGrad;
            ctx.fill();

            // Top-left specular highlight
            const highlightX = -r * 0.35;
            const highlightY = -r * 0.35;
            const highlightR = r * 0.5;
            const specGrad = ctx.createRadialGradient(highlightX, highlightY, 0, highlightX, highlightY, highlightR);
            specGrad.addColorStop(0, 'rgba(255,255,255,0.35)');
            specGrad.addColorStop(0.5, 'rgba(255,255,255,0.1)');
            specGrad.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fillStyle = specGrad;
            ctx.fill();

            // Animated shine sweep
            const shineAngle = this.glowPhase % (Math.PI * 2);
            const shineX = Math.cos(shineAngle) * r * 0.3;
            const shineY = Math.sin(shineAngle) * r * 0.3 - r * 0.2;
            const shineGrad = ctx.createRadialGradient(shineX, shineY, 0, shineX, shineY, r * 0.4);
            shineGrad.addColorStop(0, 'rgba(255,255,255,0.12)');
            shineGrad.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fillStyle = shineGrad;
            ctx.fill();

            // Inner rim highlight
            ctx.beginPath();
            ctx.arc(0, 0, r - 1.5, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(255,255,255,0.25)';
            ctx.lineWidth = 2;
            ctx.stroke();

            // Outer border
            ctx.beginPath();
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(0,0,0,0.5)';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            ctx.restore();
        }

        containsPoint(px, py) {
            const dx = px - this.x;
            const dy = py - this.y;
            return (dx * dx + dy * dy) <= (this.radius * this.radius);
        }
    }

    // =============================================================================
    // Backdrop Slideshow
    // =============================================================================

    function startSlideshow() {
        if (movies.length === 0) return;

        // Set initial backdrop
        currentBackdropIndex = 0;
        const firstMovie = movies[currentBackdropIndex];
        if (firstMovie && firstMovie.backdropUrl) {
            backdropContainerA.style.backgroundImage = `url(${firstMovie.backdropUrl})`;
            backdropContainerA.style.opacity = CONFIG.slideshowOpacity;
        }

        // Start cycling
        slideshowInterval = setInterval(() => {
            cycleBackdrop();
        }, CONFIG.slideshowInterval);
    }

    function cycleBackdrop() {
        if (movies.length === 0) return;

        // Move to next backdrop
        currentBackdropIndex = (currentBackdropIndex + 1) % movies.length;
        const movie = movies[currentBackdropIndex];
        if (!movie || !movie.backdropUrl) return;

        // Crossfade between A and B containers
        if (activeBackdrop === 'A') {
            backdropContainerB.style.backgroundImage = `url(${movie.backdropUrl})`;
            backdropContainerB.style.opacity = CONFIG.slideshowOpacity;
            backdropContainerA.style.opacity = 0;
            activeBackdrop = 'B';
        } else {
            backdropContainerA.style.backgroundImage = `url(${movie.backdropUrl})`;
            backdropContainerA.style.opacity = CONFIG.slideshowOpacity;
            backdropContainerB.style.opacity = 0;
            activeBackdrop = 'A';
        }
    }

    function stopSlideshow() {
        if (slideshowInterval) {
            clearInterval(slideshowInterval);
            slideshowInterval = null;
        }
        currentBackdropIndex = 0;
        activeBackdrop = 'A';
    }

    // =============================================================================
    // Screensaver Lifecycle
    // =============================================================================

    async function enterScreensaver() {
        if (isActive || !CONFIG.enabled) return;
        if (movies.length === 0) {
            await fetchMovies();
            if (movies.length === 0) {
                console.warn('[OrbScreensaver] No movies available');
                return;
            }
        }

        console.log('[OrbScreensaver] Entering screensaver');
        isActive = true;
        createOverlay();
        initOrbs();
        startAnimation();

        requestAnimationFrame(() => {
            overlay.style.opacity = '1';
        });

        document.body.style.overflow = 'hidden';
    }

    function exitScreensaver() {
        if (!isActive) return;
        console.log('[OrbScreensaver] Exiting screensaver');
        isActive = false;
        lastExitTime = Date.now();

        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
            animationFrameId = null;
        }

        if (overlay) {
            overlay.style.opacity = '0';
            setTimeout(() => {
                if (overlay && overlay.parentNode) {
                    overlay.parentNode.removeChild(overlay);
                }
                overlay = null;
                canvas = null;
                ctx = null;
                orbs = [];
            }, CONFIG.exitFadeMs);
        }

        cancelLongPress();
        if (homeControlTimer) {
            clearTimeout(homeControlTimer);
            homeControlTimer = null;
        }
        homeControl = null;
        longPressIndicator = null;
        document.body.style.overflow = '';
    }

    function createOverlay() {
        overlay = document.createElement('div');
        overlay.id = 'orb-screensaver-overlay';
        overlay.style.cssText = `
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            z-index: 99999;
            background: #000000;
            opacity: 0;
            transition: opacity ${CONFIG.enterFadeMs}ms ease-out;
            cursor: default;
            touch-action: none;
        `;

        canvas = document.createElement('canvas');
        canvas.style.cssText = 'width: 100%; height: 100%; display: block;';
        overlay.appendChild(canvas);

        // --- Exit UI styles ---
        const exitStyleEl = document.createElement('style');
        const _circ = 2 * Math.PI * 24;
        exitStyleEl.textContent = `
            #screensaver-home-control {
                position: absolute;
                bottom: 40px;
                left: 50%;
                transform: translateX(-50%);
                z-index: 20;
                display: flex;
                align-items: center;
                gap: 14px;
                padding: 20px 44px;
                min-height: 64px;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 999px;
                background: rgba(18, 18, 22, 0.65);
                -webkit-backdrop-filter: blur(24px) saturate(180%);
                backdrop-filter: blur(24px) saturate(180%);
                color: rgba(255,255,255,0.85);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                font-size: 24px;
                font-weight: 600;
                letter-spacing: 0.3px;
                cursor: pointer;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.4s ease;
                -webkit-tap-highlight-color: transparent;
                outline: none;
                box-shadow: 0 4px 24px rgba(0,0,0,0.4);
            }
            #screensaver-home-control:active {
                transform: translateX(-50%) scale(0.96);
            }
            #screensaver-home-control svg {
                flex-shrink: 0;
                width: 28px;
                height: 28px;
            }
            #screensaver-longpress {
                position: absolute;
                width: 56px;
                height: 56px;
                pointer-events: none;
                opacity: 0;
                transition: opacity 0.2s ease;
                z-index: 15;
            }
        `;
        overlay.appendChild(exitStyleEl);

        // Home control — floating pill button at bottom center
        homeControl = document.createElement('button');
        homeControl.id = 'screensaver-home-control';
        homeControl.setAttribute('aria-label', 'Return to Home');
        homeControl.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                <polyline points="9 22 9 12 15 12 15 22"></polyline>
            </svg>
            <span>Home</span>
        `;
        homeControl.addEventListener('pointerdown', (e) => {
            e.stopPropagation();
        });
        homeControl.addEventListener('pointerup', (e) => {
            e.stopPropagation();
            exitScreensaver();
            setTimeout(() => {
                window.location.href = '/kiosk/';
            }, CONFIG.exitFadeMs + 50);
        });
        overlay.appendChild(homeControl);

        // Long-press radial progress indicator
        longPressIndicator = document.createElement('div');
        longPressIndicator.id = 'screensaver-longpress';
        longPressIndicator.innerHTML = `
            <svg width="56" height="56" viewBox="0 0 56 56">
                <circle cx="28" cy="28" r="24" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="3"/>
                <circle id="longpress-progress" cx="28" cy="28" r="24" fill="none" stroke="rgba(255,255,255,0.6)" stroke-width="3"
                    stroke-dasharray="${_circ}"
                    stroke-dashoffset="${_circ}"
                    stroke-linecap="round"
                    transform="rotate(-90 28 28)"/>
            </svg>
        `;
        overlay.appendChild(longPressIndicator);

        // Reveal Home control on pointer movement
        overlay.addEventListener('pointermove', () => {
            showHomeControl();
        }, { passive: true });

        document.body.appendChild(overlay);

        resizeCanvas();
        window.addEventListener('resize', resizeCanvas);

        // Touch/pointer event handlers for orb interaction
        overlay.addEventListener('pointerdown', handlePointerDown);
        overlay.addEventListener('pointermove', handlePointerMove);
        overlay.addEventListener('pointerup', handlePointerUp);
        overlay.addEventListener('pointercancel', handlePointerUp);
    }

    function resizeCanvas() {
        if (!canvas) return;
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
    }

    function initOrbs() {
        orbs = [];
        const width = canvas.getBoundingClientRect().width;
        const height = canvas.getBoundingClientRect().height;

        const shuffled = [...movies].sort(() => Math.random() - 0.5);
        const count = Math.min(CONFIG.orbCount, shuffled.length);

        for (let i = 0; i < count; i++) {
            const movie = shuffled[i];
            const radius = CONFIG.minRadius + Math.random() * (CONFIG.maxRadius - CONFIG.minRadius);
            const margin = radius + CONFIG.edgeMargin;

            let x, y, attempts = 0;
            // Require more initial separation to prevent bunching at start
            const initialSeparation = radius * 2 + CONFIG.repulsionDistance;
            do {
                x = margin + Math.random() * (width - margin * 2);
                y = margin + Math.random() * (height - margin * 2);
                attempts++;
            } while (attempts < 100 && orbs.some(o => {
                const dx = o.x - x;
                const dy = o.y - y;
                return Math.sqrt(dx * dx + dy * dy) < o.radius + initialSeparation;
            }));

            const angle = Math.random() * Math.PI * 2;
            const speed = CONFIG.speedMin + Math.random() * (CONFIG.speedMax - CONFIG.speedMin);
            const vx = Math.cos(angle) * speed;
            const vy = Math.sin(angle) * speed;

            orbs.push(new Orb(movie, x, y, radius, vx, vy));
        }
    }

    // =============================================================================
    // Animation Loop
    // =============================================================================

    let lastTime = 0;

    function startAnimation() {
        lastTime = performance.now();
        animationFrameId = requestAnimationFrame(animate);
    }

    function animate(time) {
        if (!isActive) return;

        const dt = Math.min((time - lastTime) / 1000, 0.1);
        lastTime = time;

        const width = canvas.getBoundingClientRect().width;
        const height = canvas.getBoundingClientRect().height;

        ctx.clearRect(0, 0, width, height);

        for (const orb of orbs) {
            orb.update(dt, width, height, orbs);
        }

        const sortedOrbs = [...orbs].sort((a, b) => a.y - b.y);
        for (const orb of sortedOrbs) {
            orb.draw(ctx);
        }

        animationFrameId = requestAnimationFrame(animate);
    }

    // =============================================================================
    // Interaction - Touch/Flick for orbs
    // =============================================================================

    let lastPointerPos = { x: 0, y: 0 };

    function getPointerPos(e) {
        const rect = canvas.getBoundingClientRect();
        return {
            x: (e.clientX || 0) - rect.left,
            y: (e.clientY || 0) - rect.top
        };
    }

    function handlePointerDown(e) {
        // Ignore if clicking on Home control
        if (e.target.closest('#screensaver-home-control')) return;

        e.preventDefault();
        const pos = getPointerPos(e);
        touchStartTime = Date.now();
        touchStartPos = { x: pos.x, y: pos.y };
        lastPointerPos = { x: pos.x, y: pos.y };
        isDragging = false;
        draggedOrb = null;

        // Show Home control on any interaction
        showHomeControl();

        // Start long-press detection
        startLongPress(e.clientX, e.clientY);

        // Check if touching an orb
        for (const orb of orbs) {
            if (orb.containsPoint(pos.x, pos.y)) {
                draggedOrb = orb;
                orb.vx *= 0.3;
                orb.vy *= 0.3;
                break;
            }
        }
    }

    let velocityHistory = [];

    function handlePointerMove(e) {
        const pos = getPointerPos(e);
        const dx = pos.x - touchStartPos.x;
        const dy = pos.y - touchStartPos.y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        // Cancel long-press if moved beyond threshold
        if (longPressStartTime > 0 && distance > 10) {
            cancelLongPress();
        }

        if (!draggedOrb) return;
        e.preventDefault();

        const now = performance.now();

        // Mark as dragging/swiping if moved more than 10px
        if (distance > 10) {
            isDragging = true;

            // Calculate push direction from movement
            const moveDx = pos.x - lastPointerPos.x;
            const moveDy = pos.y - lastPointerPos.y;

            // Track velocity history for fling calculation
            velocityHistory.push({ dx: moveDx, dy: moveDy, time: now });
            // Keep only last 100ms of movement history
            velocityHistory = velocityHistory.filter(v => now - v.time < 100);

            // Apply force to orb based on swipe movement
            draggedOrb.vx += moveDx * 3;
            draggedOrb.vy += moveDy * 3;

            // Allow higher velocity during fling (5x max for playful physics)
            const speed = Math.sqrt(draggedOrb.vx * draggedOrb.vx + draggedOrb.vy * draggedOrb.vy);
            if (speed > CONFIG.speedMax * 5) {
                const factor = (CONFIG.speedMax * 5) / speed;
                draggedOrb.vx *= factor;
                draggedOrb.vy *= factor;
            }
        }

        lastPointerPos = { x: pos.x, y: pos.y };
    }

    function handlePointerUp(e) {
        cancelLongPress();

        if (draggedOrb && isDragging) {
            // Calculate fling velocity from recent movement history
            if (velocityHistory.length > 0) {
                let totalDx = 0, totalDy = 0;
                for (const v of velocityHistory) {
                    totalDx += v.dx;
                    totalDy += v.dy;
                }
                const flingMultiplier = 2;
                draggedOrb.vx += (totalDx / velocityHistory.length) * flingMultiplier;
                draggedOrb.vy += (totalDy / velocityHistory.length) * flingMultiplier;
            }

            const speed = Math.sqrt(draggedOrb.vx * draggedOrb.vx + draggedOrb.vy * draggedOrb.vy);
            if (speed > CONFIG.speedMax * 6) {
                const factor = (CONFIG.speedMax * 6) / speed;
                draggedOrb.vx *= factor;
                draggedOrb.vy *= factor;
            }

            if (speed < CONFIG.speedMin) {
                const angle = Math.atan2(draggedOrb.vy, draggedOrb.vx) || Math.random() * Math.PI * 2;
                draggedOrb.vx = Math.cos(angle) * CONFIG.speedMin;
                draggedOrb.vy = Math.sin(angle) * CONFIG.speedMin;
            }
        } else if (draggedOrb && !isDragging) {
            // Tap (not drag) on an orb — open movie modal
            const movie = draggedOrb.movie;
            if (movie && (movie.rating_key || movie.id)) {
                exitScreensaver();
                setTimeout(() => {
                    window.dispatchEvent(new CustomEvent('screensaver-orb-tap', {
                        detail: {
                            ratingKey: movie.rating_key || null,
                            tmdbId: movie.id || null,
                            title: movie.title || '',
                        }
                    }));
                }, CONFIG.exitFadeMs + 50);
            }
        }

        draggedOrb = null;
        isDragging = false;
        velocityHistory = [];
    }

    // =============================================================================
    // Initialization
    // =============================================================================

    function init() {
        if (!CONFIG.enabled) {
            console.log('[OrbScreensaver] Disabled');
            return;
        }

        console.log('[OrbScreensaver] Initializing', {
            idleTimeoutMs: CONFIG.idleTimeoutMs,
            orbCount: CONFIG.orbCount
        });

        setTimeout(fetchMovies, 2000);

        startActivityTracking();
        startIdleCheck();
        recordActivity();
    }

    window.OrbScreensaver = {
        config: CONFIG,
        enter: enterScreensaver,
        exit: exitScreensaver,
        isActive: () => isActive,
        getIdleTime: () => Date.now() - lastActivityTime,
        enable: () => { CONFIG.enabled = true; startIdleCheck(); },
        disable: () => { CONFIG.enabled = false; exitScreensaver(); },
        recordActivity
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
