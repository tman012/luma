// Media Search App - Main JavaScript

// DOM Elements
const searchInput = document.getElementById('search-input');
const clearBtn = document.getElementById('clear-search');
const filterBtns = document.querySelectorAll('.filter-btn');
const searchResults = document.getElementById('search-results');
const mainContent = document.getElementById('main-content');
const resultsContainer = document.getElementById('results-container');
const closeResults = document.getElementById('close-results');
const loading = document.getElementById('loading');
const movieModal = document.getElementById('movie-modal');
const modalBody = document.getElementById('modal-body');

// State
let currentSearchType = 'all';
let searchTimeout = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    setupMediaCardListeners();
});

// Event Listeners
function setupEventListeners() {
    // Search input
    searchInput.addEventListener('input', handleSearchInput);
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            performSearch();
        }
    });

    // Clear button
    clearBtn.addEventListener('click', clearSearch);

    // Filter buttons
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentSearchType = btn.dataset.type;
            if (searchInput.value.trim()) {
                performSearch();
            }
        });
    });

    // Close results
    if (closeResults) {
        closeResults.addEventListener('click', () => {
            showMainContent();
        });
    }
}

// Setup media card click listeners
function setupMediaCardListeners() {
    document.addEventListener('click', (e) => {
        const card = e.target.closest('.media-card');
        if (card) {
            const tmdbId = card.dataset.tmdbId;
            if (tmdbId) {
                showMovieDetails(tmdbId);
            }
        }
    });
}

// Search Functions
function handleSearchInput() {
    const query = searchInput.value.trim();

    // Show/hide clear button
    clearBtn.style.display = query ? 'block' : 'none';

    // Clear previous timeout
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }

    // Debounce search
    if (query.length >= 2) {
        searchTimeout = setTimeout(() => {
            performSearch();
        }, 500);
    } else if (query.length === 0) {
        showMainContent();
    }
}

async function performSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    showLoading();

    try {
        const response = await fetch(`/search?q=${encodeURIComponent(query)}&type=${currentSearchType}`);
        const data = await response.json();
        displaySearchResults(data);
    } catch (error) {
        console.error('Search error:', error);
        showError('Failed to perform search. Please try again.');
    } finally {
        hideLoading();
    }
}

function displaySearchResults(data) {
    // Hide main content and show results
    mainContent.style.display = 'none';
    searchResults.style.display = 'block';

    // Clear previous results
    resultsContainer.innerHTML = '';

    // Combine and display results
    const allResults = [];

    if (data.movies && data.movies.length > 0) {
        data.movies.forEach(movie => {
            allResults.push({
                ...movie,
                media_type: 'movie',
                display_title: movie.title,
                display_date: movie.release_date
            });
        });
    }

    if (data.tv_shows && data.tv_shows.length > 0) {
        data.tv_shows.forEach(show => {
            allResults.push({
                ...show,
                media_type: 'tv',
                display_title: show.name,
                display_date: show.first_air_date
            });
        });
    }

    if (allResults.length === 0) {
        resultsContainer.innerHTML = `
            <div class="no-results">
                <i class="fas fa-search"></i>
                <h3>No results found</h3>
                <p>Try adjusting your search query</p>
            </div>
        `;
        return;
    }

    // Create result cards
    allResults.forEach(item => {
        const card = createMediaCard(item);
        resultsContainer.appendChild(card);
    });
}

function createMediaCard(item) {
    const card = document.createElement('div');
    card.className = 'media-card';
    card.dataset.tmdbId = item.id;
    card.dataset.mediaType = item.media_type;

    const posterUrl = item.poster_path
        ? `https://image.tmdb.org/t/p/w300${item.poster_path}`
        : '';

    const year = item.display_date ? item.display_date.substring(0, 4) : 'N/A';

    card.innerHTML = `
        <div class="media-poster">
            ${posterUrl
                ? `<img src="${posterUrl}" alt="${item.display_title}" loading="lazy">`
                : `<div class="no-poster"><i class="fas fa-${item.media_type === 'tv' ? 'tv' : 'film'}"></i></div>`
            }
            ${item.vote_average
                ? `<div class="media-rating">
                    <i class="fas fa-star"></i>
                    ${item.vote_average.toFixed(1)}
                   </div>`
                : ''
            }
        </div>
        <div class="media-info">
            <h3>${item.display_title}</h3>
            <p>${year} ${item.media_type === 'tv' ? '• TV Series' : ''}</p>
        </div>
    `;

    return card;
}

function clearSearch() {
    searchInput.value = '';
    clearBtn.style.display = 'none';
    showMainContent();
}

function showMainContent() {
    mainContent.style.display = 'block';
    searchResults.style.display = 'none';
}

// Movie Details
async function showMovieDetails(tmdbId) {
    showLoading();

    try {
        const response = await fetch(`/api/movie/${tmdbId}`);
        const movie = await response.json();
        displayMovieDetails(movie);
    } catch (error) {
        console.error('Error fetching movie details:', error);
        showError('Failed to load movie details.');
    } finally {
        hideLoading();
    }
}

function displayMovieDetails(movie) {
    const backdropUrl = movie.backdrop_path
        ? `https://image.tmdb.org/t/p/w1280${movie.backdrop_path}`
        : '';

    const posterUrl = movie.poster_path
        ? `https://image.tmdb.org/t/p/w500${movie.poster_path}`
        : '';

    modalBody.innerHTML = `
        ${backdropUrl ? `
            <div class="modal-backdrop">
                <img src="${backdropUrl}" alt="${movie.title}">
            </div>
        ` : ''}
        <div class="modal-details">
            <div class="modal-poster">
                ${posterUrl
                    ? `<img src="${posterUrl}" alt="${movie.title}">`
                    : `<div class="no-poster"><i class="fas fa-film"></i></div>`
                }
            </div>
            <div class="modal-info">
                <h2>${movie.title}</h2>
                <div class="modal-meta">
                    <span>${movie.release_date ? movie.release_date.substring(0, 4) : 'N/A'}</span>
                    <span>${movie.runtime ? `${movie.runtime} min` : ''}</span>
                    <span class="rating">
                        <i class="fas fa-star"></i>
                        ${movie.vote_average ? movie.vote_average.toFixed(1) : 'N/A'}
                    </span>
                </div>
                ${movie.genres ? `
                    <div class="modal-genres">
                        ${movie.genres.map(g => `<span class="genre-tag">${g.name}</span>`).join('')}
                    </div>
                ` : ''}
                <p class="modal-overview">${movie.overview || 'No overview available.'}</p>
                ${movie.credits && movie.credits.cast ? `
                    <div class="modal-cast">
                        <h3>Cast</h3>
                        <div class="cast-list">
                            ${movie.credits.cast.slice(0, 5).map(actor => `
                                <span style="cursor: pointer; text-decoration: underline;" onclick="closeModal(); window.location.href='/library?search=${encodeURIComponent(actor.name)}&filter=people'">${actor.name}</span>
                            `).join(', ')}
                        </div>
                    </div>
                ` : ''}
                <!-- RT Reviews Section -->
                <div class="desktop-rt-reviews" id="desktopRtReviews" style="display: none;">
                    <div class="desktop-rt-header" onclick="toggleDesktopReviews()">
                        <h3>Reviews</h3>
                        <i class="fas fa-chevron-down desktop-rt-toggle" id="desktopRtToggle"></i>
                    </div>
                    <div class="desktop-rt-content" id="desktopRtContent">
                        <div class="desktop-rt-subsection" id="desktopRtCriticSection" style="display: none;">
                            <h4>Critic Reviews</h4>
                            <div id="desktopRtCriticList"></div>
                        </div>
                        <div class="desktop-rt-subsection" id="desktopRtAudienceSection" style="display: none;">
                            <h4>Audience Reviews</h4>
                            <div id="desktopRtAudienceList"></div>
                        </div>
                        <a class="desktop-rt-link" id="desktopRtLink" href="#" target="_blank" rel="noopener noreferrer">
                            Read Reviews on Rotten Tomatoes <i class="fas fa-external-link-alt"></i>
                        </a>
                    </div>
                    <div class="desktop-rt-loading" id="desktopRtLoading" style="display: none;">
                        <i class="fas fa-circle-notch fa-spin"></i> Loading...
                    </div>
                </div>
            </div>
        </div>
    `;

    movieModal.style.display = 'flex';

    // Fetch RT reviews for desktop modal
    const year = movie.release_date ? movie.release_date.substring(0, 4) : null;
    fetchDesktopRTReviews(movie.title, year);
}

function closeModal() {
    movieModal.style.display = 'none';
    modalBody.innerHTML = '';
}

// Desktop RT Reviews Functions
async function fetchDesktopRTReviews(title, year) {
    const section = document.getElementById('desktopRtReviews');
    const loading = document.getElementById('desktopRtLoading');
    const content = document.getElementById('desktopRtContent');
    const toggleIcon = document.getElementById('desktopRtToggle');

    if (!section) return;

    // Reset state
    section.style.display = 'none';
    content.style.display = 'none';
    if (toggleIcon) toggleIcon.classList.remove('expanded');

    // Show loading
    loading.style.display = 'flex';
    section.style.display = 'block';

    try {
        const params = new URLSearchParams({ title: title });
        if (year) params.append('year', year);
        params.append('limit', '20');

        const response = await fetch(`/api/rt/reviews?${params.toString()}`);

        if (!response.ok) {
            throw new Error('Failed to fetch reviews');
        }

        const data = await response.json();

        if (!data.success) {
            section.style.display = 'none';
            return;
        }

        // Hide loading
        loading.style.display = 'none';

        // Populate reviews (starts collapsed)
        populateDesktopRTReviews(data);

    } catch (error) {
        console.error('Failed to fetch RT reviews:', error);
        section.style.display = 'none';
    }
}

function populateDesktopRTReviews(data) {
    // Critic Reviews
    const criticSection = document.getElementById('desktopRtCriticSection');
    const criticList = document.getElementById('desktopRtCriticList');
    if (criticList) {
        criticList.innerHTML = '';
        if (data.critic_reviews && data.critic_reviews.length > 0) {
            data.critic_reviews.forEach(review => {
                criticList.appendChild(createDesktopCriticCard(review));
            });
            if (criticSection) criticSection.style.display = 'block';
        } else {
            if (criticSection) criticSection.style.display = 'none';
        }
    }

    // Audience Reviews
    const audienceSection = document.getElementById('desktopRtAudienceSection');
    const audienceList = document.getElementById('desktopRtAudienceList');
    if (audienceList) {
        audienceList.innerHTML = '';
        if (data.audience_reviews && data.audience_reviews.length > 0) {
            data.audience_reviews.forEach(review => {
                audienceList.appendChild(createDesktopAudienceCard(review));
            });
            if (audienceSection) audienceSection.style.display = 'block';
        } else {
            if (audienceSection) audienceSection.style.display = 'none';
        }
    }

    // RT Link - prefer reviews_url
    const rtLink = document.getElementById('desktopRtLink');
    if ((data.reviews_url || data.url) && rtLink) {
        rtLink.href = data.reviews_url || data.url;
        rtLink.style.display = 'flex';
    } else if (rtLink) {
        rtLink.style.display = 'none';
    }
}

function createDesktopCriticCard(review) {
    const card = document.createElement('div');
    card.className = 'desktop-rt-review-card';

    const freshIcon = review.fresh
        ? '/static/images/rotten-tomatoes-logo.png'
        : '/static/images/rotten-tomatoes-splat.png';

    card.innerHTML = `
        <p class="desktop-rt-quote">"${escapeHtmlDesktop(review.quote)}"</p>
        <div class="desktop-rt-meta">
            <span class="desktop-rt-author">
                ${escapeHtmlDesktop(review.critic)}
                ${review.publication ? `<span class="desktop-rt-pub">- ${escapeHtmlDesktop(review.publication)}</span>` : ''}
            </span>
            <img class="desktop-rt-freshness" src="${freshIcon}" alt="${review.fresh ? 'Fresh' : 'Rotten'}">
        </div>
    `;

    return card;
}

function createDesktopAudienceCard(review) {
    const card = document.createElement('div');
    card.className = 'desktop-rt-review-card';

    let ratingHtml = '';
    if (review.rating !== null && review.rating !== undefined) {
        const stars = '★'.repeat(Math.round(review.rating));
        ratingHtml = `<span class="desktop-rt-rating">${stars} ${review.rating}/5</span>`;
    }

    card.innerHTML = `
        <p class="desktop-rt-quote">"${escapeHtmlDesktop(review.quote)}"</p>
        <div class="desktop-rt-meta">
            <span class="desktop-rt-author">${escapeHtmlDesktop(review.user)}</span>
            ${ratingHtml}
        </div>
    `;

    return card;
}

function toggleDesktopReviews() {
    const content = document.getElementById('desktopRtContent');
    const toggleIcon = document.getElementById('desktopRtToggle');

    if (content && toggleIcon) {
        const isExpanded = content.style.display !== 'none';
        content.style.display = isExpanded ? 'none' : 'block';
        toggleIcon.classList.toggle('expanded', !isExpanded);
    }
}

function escapeHtmlDesktop(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Modal close on outside click
movieModal.addEventListener('click', (e) => {
    if (e.target === movieModal) {
        closeModal();
    }
});

// Loading States
function showLoading() {
    loading.style.display = 'flex';
}

function hideLoading() {
    loading.style.display = 'none';
}

function showError(message) {
    // You can implement a toast notification here
    console.error(message);
    alert(message);
}

// Add modal-specific styles
const modalStyles = `
    <style>
    .modal-backdrop {
        position: relative;
        height: 300px;
        overflow: hidden;
        margin: -1px -1px 0 -1px;
    }

    .modal-backdrop img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        opacity: 0.3;
    }

    .modal-details {
        padding: 2rem;
        display: flex;
        gap: 2rem;
    }

    .modal-poster {
        flex-shrink: 0;
        width: 200px;
    }

    .modal-poster img {
        width: 100%;
        border-radius: var(--radius);
    }

    .modal-info {
        flex: 1;
    }

    .modal-info h2 {
        font-size: 2rem;
        margin-bottom: 1rem;
    }

    .modal-meta {
        display: flex;
        gap: 1rem;
        color: var(--text-secondary);
        margin-bottom: 1rem;
    }

    .modal-meta .rating {
        color: #ffd700;
    }

    .modal-genres {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        margin-bottom: 1.5rem;
    }

    .genre-tag {
        background: var(--bg-tertiary);
        padding: 0.25rem 0.75rem;
        border-radius: 20px;
        font-size: 0.85rem;
    }

    .modal-overview {
        line-height: 1.6;
        color: var(--text-secondary);
        margin-bottom: 2rem;
    }

    .modal-cast h3 {
        margin-bottom: 0.5rem;
    }

    .cast-list {
        color: var(--text-secondary);
    }

    /* Desktop RT Reviews Styles */
    .desktop-rt-reviews {
        margin-top: 1.5rem;
        border-top: 1px solid rgba(255, 255, 255, 0.1);
        padding-top: 1rem;
    }

    .desktop-rt-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        cursor: pointer;
        padding: 0.5rem 0;
    }

    .desktop-rt-header:hover {
        opacity: 0.8;
    }

    .desktop-rt-header h3 {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin: 0;
        font-size: 1.1rem;
    }

    .desktop-rt-icon {
        width: 20px;
        height: 20px;
        object-fit: contain;
    }

    .desktop-rt-toggle {
        color: var(--text-secondary);
        transition: transform 0.3s ease;
    }

    .desktop-rt-toggle.expanded {
        transform: rotate(180deg);
    }

    .desktop-rt-header-score {
        font-size: 0.9rem;
        font-weight: 600;
        color: #fa320a;
        margin-left: 0.5rem;
    }

    .desktop-rt-content {
        padding-top: 1rem;
    }

    .desktop-rt-scores-bar {
        display: flex;
        gap: 2rem;
        padding: 1rem 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        margin-bottom: 1rem;
    }

    .desktop-rt-score-item {
        display: flex;
        align-items: center;
        gap: 0.75rem;
    }

    .desktop-rt-score-icon {
        width: 32px;
        height: 32px;
        object-fit: contain;
    }

    .desktop-rt-score-info {
        display: flex;
        flex-direction: column;
    }

    .desktop-rt-score-value {
        font-size: 1.25rem;
        font-weight: 700;
        color: var(--text-primary, white);
    }

    .desktop-rt-score-label {
        font-size: 0.7rem;
        color: var(--text-secondary, rgba(255,255,255,0.6));
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    .desktop-rt-reviews-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1.5rem;
        margin-bottom: 1rem;
    }

    .desktop-rt-critic-reviews h4,
    .desktop-rt-audience-reviews h4 {
        color: var(--text-secondary);
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin: 0 0 0.75rem 0;
    }

    .desktop-rt-subsection {
        margin-bottom: 1.5rem;
    }

    .desktop-rt-subsection:last-of-type {
        margin-bottom: 1rem;
    }

    .desktop-rt-subsection h4 {
        color: var(--text-secondary, rgba(255,255,255,0.7));
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin: 0 0 0.75rem 0;
    }

    .desktop-rt-review-card {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 6px;
        padding: 0.75rem;
        margin-bottom: 0.75rem;
    }

    .desktop-rt-quote {
        color: var(--text-primary);
        font-size: 0.9rem;
        line-height: 1.5;
        margin: 0 0 0.5rem 0;
    }

    .desktop-rt-meta {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
    }

    .desktop-rt-author {
        color: var(--text-secondary);
        font-size: 0.8rem;
    }

    .desktop-rt-pub {
        color: var(--text-tertiary, rgba(255,255,255,0.4));
    }

    .desktop-rt-freshness {
        width: 16px;
        height: 16px;
        object-fit: contain;
    }

    .desktop-rt-rating {
        color: #ffd700;
        font-size: 0.8rem;
    }

    .desktop-rt-link {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        padding: 0.75rem;
        background: rgba(250, 50, 10, 0.1);
        border: 1px solid rgba(250, 50, 10, 0.3);
        border-radius: 6px;
        color: #fa320a;
        font-size: 0.9rem;
        text-decoration: none;
        transition: all 0.2s ease;
    }

    .desktop-rt-link:hover {
        background: rgba(250, 50, 10, 0.2);
        border-color: rgba(250, 50, 10, 0.5);
    }

    .desktop-rt-loading {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        padding: 1rem;
        color: var(--text-secondary);
    }

    @media (max-width: 768px) {
        .desktop-rt-reviews-grid {
            grid-template-columns: 1fr;
        }
    }

    .no-results {
        grid-column: 1 / -1;
        text-align: center;
        padding: 4rem 2rem;
        color: var(--text-secondary);
    }

    .no-results i {
        font-size: 4rem;
        margin-bottom: 1rem;
        opacity: 0.5;
    }

    .no-results h3 {
        margin-bottom: 0.5rem;
        color: var(--text-primary);
    }

    @media (max-width: 768px) {
        .modal-details {
            flex-direction: column;
        }

        .modal-poster {
            width: 150px;
            margin: 0 auto;
        }

        .modal-info h2 {
            font-size: 1.5rem;
        }
    }
    </style>
`;

// Inject modal styles
document.head.insertAdjacentHTML('beforeend', modalStyles);