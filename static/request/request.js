(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const IMG = 'https://image.tmdb.org/t/p/';
  // URL base ('' or e.g. '/luma'), injected by the server into the page.
  const BASE = (document.querySelector('meta[name="url-base"]') || {}).content || '';
  const url = (path) => BASE + path;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ICON = {
    want: '', // plain blue orb (no clock)
    check: '<svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    checkG: '<svg viewBox="0 0 18 18"><path d="M4 9.5l3.2 3.2L14 5.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    clock: '<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="7" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M9 5v4.3l2.8 1.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    down: '<svg viewBox="0 0 18 18"><path d="M9 3v9M5 8.5l4 4 4-4M4 15h10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    chev: '<svg class="chev" viewBox="0 0 8 13"><path d="M1.5 1.5l5 5-5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    back: '<svg viewBox="0 0 11 18"><path d="M9.5 1.5L2 9l7.5 7.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  async function api(path, opts = {}) {
    const r = await fetch(url(path), {
      credentials: 'same-origin',
      headers: opts.body ? { 'Content-Type': 'application/json' } : {},
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (r.status === 401 && data.error === 'locked') { showGate(); throw new Error('locked'); }
    if (!r.ok) { const e = new Error(data.error || 'error'); e.data = data; e.status = r.status; throw e; }
    return data;
  }

  // ---------------- gate ----------------
  function showGate() {
    $('#home').hidden = true; closeSheet(true); $('#gate').hidden = false;
    setTimeout(() => $('#gate-pw').focus(), 50);
  }
  function showHome() {
    $('#gate').hidden = true; $('#home').hidden = false;
    loadDiscover();
    loadRecent();
  }
  $('#gate-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const password = $('#gate-pw').value;
    const err = $('#gate-err'), btn = $('#gate-go');
    btn.disabled = true; err.textContent = '';
    try {
      await api('/api/request/unlock', { method: 'POST', body: { password } });
      $('#gate-pw').value = '';
      showHome();
    } catch (e) {
      const f = $('#gate-form'); f.classList.remove('shake'); void f.offsetWidth; f.classList.add('shake');
      err.textContent = e.status === 429 ? `Too many tries. Try again in ${Math.ceil((e.data.retry_after || 60) / 60)} min.`
        : e.message === 'wrong_password' ? 'Wrong password' : 'Something went wrong';
      $('#gate-pw').select();
    } finally { btn.disabled = false; }
  });

  // ---------------- search (port of Luma's SearchShelf) ----------------
  let seq = 0, timer = 0, skind = 'movie';
  const results = $('#results'), ss = $('#ss');
  const qx = $('#qx');
  const syncX = () => { qx.hidden = !$('#q').value; };
  function openSearch() {
    skind = kind;
    $('#ss-pills').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.kind === skind));
    ss.hidden = false; ss.classList.remove('on');
    document.documentElement.style.background = '#000'; // like Luma: no safe-area bleed-through
    const fit = () => { results.style.top = $('#ss-head').getBoundingClientRect().height + 'px'; };
    requestAnimationFrame(() => { ss.classList.add('on'); fit(); });
    setTimeout(() => $('#q').focus({ preventScroll: true }), 100);
  }
  function closeSearch(then) {
    ss.classList.remove('on');
    $('#q').blur();
    setTimeout(() => { ss.hidden = true; document.documentElement.style.background = ''; if (then) then(); }, 300);
  }
  $('#search-open').addEventListener('click', openSearch);
  // Luma: touching/scrolling the results dismisses the keyboard
  const dismissKb = () => $('#q').blur();
  results.addEventListener('touchstart', dismissKb, { passive: true });
  results.addEventListener('scroll', dismissKb, { passive: true });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !ss.hidden) closeSearch(); });
  $('#ss-x').addEventListener('click', () => closeSearch());
  $('#q').addEventListener('input', () => {
    syncX(); clearTimeout(timer);
    if (!$('#q').value.trim()) { results.innerHTML = ''; return; }
    results.innerHTML = '<div class="ss-spin"><i></i></div>';
    timer = setTimeout(runSearch, 300);
  });
  // pointerdown + preventDefault keeps focus (and the keyboard) in the field
  qx.addEventListener('pointerdown', (e) => e.preventDefault());
  qx.addEventListener('click', () => {
    const q = $('#q'); q.value = ''; syncX(); clearTimeout(timer); results.innerHTML = ''; q.focus();
  });
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(timer); runSearch(); e.target.blur(); } });
  $('#ss-pills').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b || b.dataset.kind === skind) return;
    skind = b.dataset.kind;
    $('#ss-pills').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    if ($('#q').value.trim().length >= 2) runSearch();
  });

  let kind = 'movie';
  function rowHTML(r) {
    const thumb = r.poster ? `<img src="${IMG}w185${esc(r.poster)}" alt="" loading="lazy" draggable="false">`
      : '<span class="ss-ph"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="1.5"><rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/></svg></span>';
    const sub = `${r.year ? r.year + ' · ' : ''}${r.type === 'tv' ? 'TV Show' : 'Movie'}`;
    return `<button type="button" class="ss-row" data-type="${r.type}" data-id="${r.id}">
      <div class="ss-th">${thumb}${orb(r)}</div>
      <div class="ss-tx"><div class="ss-t">${esc(r.title)}</div><div class="ss-s">${esc(sub)}</div></div></button>`;
  }
  async function runSearch() {
    const q = $('#q').value.trim();
    const my = ++seq;
    if (q.length < 2) { results.innerHTML = ''; return; }
    results.innerHTML = '<div class="ss-spin"><i></i></div>';
    try {
      const { results: list } = await api(`/api/request/search?type=${skind}&q=` + encodeURIComponent(q));
      if (my !== seq) return;
      results.innerHTML = list.length
        ? `<div class="ss-count">${list.length} ${list.length === 1 ? 'Result' : 'Results'}</div>${list.map(rowHTML).join('')}<div class="ss-pad"></div>`
        : '<p class="ss-none">No results found</p>';
    } catch (_) { /* gate shown or transient */ }
  }
  const orb = (it) => (it.in_library ? `<span class="badge">${ICON.check}</span>` : it.wanted ? `<span class="badge want">${ICON.want}</span>` : '');
  function posterHTML(path, title, w = 'w342', size = '33vw') {
    if (!path) return `<span class="ph">${esc(title)}</span>`;
    const p = esc(path);
    if (w !== 'w342') return `<img loading="lazy" decoding="async" src="${IMG}${w}${p}" alt="">`;
    return `<img loading="lazy" decoding="async" src="${IMG}w342${p}" srcset="${IMG}w185${p} 185w, ${IMG}w342${p} 342w" sizes="${size}" alt="">`;
  }
  results.addEventListener('click', (e) => {
    const c = e.target.closest('.ss-row'); if (!c) return;
    closeSearch(); setTimeout(() => openDetail(c.dataset.type, +c.dataset.id), 320); // same hand-off timing as Luma
  });

  // ---------------- Movies | TV ----------------
  $('#seg').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b || b.dataset.kind === kind) return;
    kind = b.dataset.kind;
    $('#seg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    loadDiscover();
    scheduleProgress();
  });

  // ---------------- discover: hero + rails ----------------
  // One combined request (Movies + TV). Owned checks arrive later if the library index is still warming up.
  let home = null, homeP = null, heroTimer = 0, ownedTimer = 0;
  const fetchHome = () => (homeP = homeP || api('/api/request/home').then((d) => (home = d)).finally(() => { homeP = null; }));
  function skeleton() {
    $('#hero').innerHTML = '<div class="sk sk-hero"></div><div class="dots"><i class="on"></i><i></i><i></i></div>';
    $('#rails').innerHTML = [0, 1, 2].map(() => `<div class="rail"><div class="sk sk-h"></div><div class="rail-track">${'<div class="card"><div class="poster sk"></div></div>'.repeat(5)}</div></div>`).join('');
  }
  async function loadDiscover() {
    if (home) renderDiscover(home[kind]);
    else {
      skeleton();
      try { await fetchHome(); } catch (_) { return; }
      renderDiscover(home[kind]);
    }
    if (!home.owned_ready && !ownedTimer) pollOwned(0);
  }
  function pollOwned(n) {
    ownedTimer = setTimeout(async () => {
      try {
        const d = await api('/api/request/home');
        home = d;
        applyOwned(d[kind]);
        if (!d.owned_ready && n < 40) return pollOwned(n + 1);
      } catch (_) { /* gate or transient */ }
      ownedTimer = 0;
    }, n < 5 ? 3000 : 6000);
  }
  function applyOwned(d) {
    const own = new Set();
    [...d.hero, ...d.rails.flatMap((r) => r.items)].forEach((it) => { if (it.in_library) own.add(it.type + it.id); });
    $('#rails').querySelectorAll('.rail .card[data-id]').forEach((c) => {
      if (!own.has(c.dataset.type + c.dataset.id)) return;
      c.querySelector('.badge.want')?.remove();
      if (!c.querySelector('.badge')) c.querySelector('.poster').insertAdjacentHTML('beforeend', `<span class="badge">${ICON.check}</span>`);
    });
    $('#discover').querySelectorAll('.slide[data-id]').forEach((c) => {
      const m = c.querySelector('.m');
      if (own.has(c.dataset.type + c.dataset.id) && !m.querySelector('.ok')) m.insertAdjacentHTML('beforeend', `<span class="ok">${ICON.check}</span>Available`);
    });
  }
  // ---------------- Recently Added (downloading first, then newest in Plex) ----------------
  let recent = null, recentTimer = 0;
  // same wording as Luma Activity: "Processing…", "Copying to library… N%", else the download %
  const stageText = (x) => x.label || (x.progress + '%');
  // downloading: small "Getting Ready" above the % so it isn't read as "N% available"
  const tagHTML = (x) => x.eptag ? `<b class="tag">${esc(x.eptag)}</b>` : '';
  const stageHTML = (x) => (x.label ? esc(x.label) : `<small>Getting Ready</small>${x.progress}%${x.eta ? `<em class="eta">${esc(x.eta)}</em>` : ''}`);
  function paintStage(c, x) {
    const i = c.querySelector('.pbar i'), b = c.querySelector('.pbar'), t = c.querySelector('.pct');
    if (i) i.style.width = Math.max(3, x.progress) + '%';
    if (b) { b.hidden = x.bar === false; b.classList.toggle('done', !!x.importing); }
    if (t) { t.innerHTML = stageHTML(x); t.classList.toggle('lbl', !!x.label); }
  }
  // caption under the poster, same as Luma's poster rail: title / grey line (S09E02 · Mr. Jingles, Season 4, or the year)
  function recentCap(it) {
    const sub = it.type === 'tv' ? (it.eptag || it.year || '') : (it.year || '');
    return `<div class="cap"><p class="ct">${esc(it.title || '')}</p>${sub ? `<p class="cs">${esc(String(sub))}</p>` : ''}</div>`;
  }
  function recentCard(it) {
    const bar = it.progress != null ? `<span class="pct${it.label ? ' lbl' : ''}">${stageHTML(it)}</span><span class="pbar${it.importing ? ' done' : ''}"${it.bar === false ? ' hidden' : ''}><i style="width:${Math.max(3, it.progress)}%"></i></span>` : '';
    return `<button class="card" data-type="${it.type}" data-id="${it.id}">
      <div class="poster">${posterHTML(it.poster, it.title, 'w342', '112px')}${bar}</div>${recentCap(it)}</button>`; // no orb on Recently Added: the centered label/progress box says it
  }
  function renderRecent() {
    const el = $('#recent'); if (!el) return;
    const list = (recent && recent[kind]) || [];
    if (!list.length) { el.innerHTML = ''; return; }
    const track = el.querySelector('.rail-track');
    const sig = list.map((x) => x.type + x.id + (x.progress == null ? 'p' : 'd') + (x.eptag || '')).join(',');
    if (track && track.dataset.sig === sig) { // just move the bars
      list.forEach((x) => {
        const c = track.querySelector(`.card[data-id="${x.id}"]`); if (!c || x.progress == null) return;
        paintStage(c, x);
      });
      return;
    }
    const sl = track ? track.scrollLeft : 0;
    el.innerHTML = `<div class="rail"><h2>Recently Added</h2><div class="rail-track" data-sig="${sig}">${list.map(recentCard).join('')}</div></div>`;
    if (sl) el.querySelector('.rail-track').scrollLeft = sl;
  }
  // live progress: poll a tiny endpoint while something is downloading and the page is visible
  let progTimer = 0;
  function scheduleProgress() {
    clearTimeout(progTimer);
    const any = (recent && (recent[kind] || []).some((x) => x.progress != null)) || !!document.querySelector('#sheet-body .chip.dl, #sheet-body .st.dl');
    if (any && !document.hidden) progTimer = setTimeout(pollProgress, 4000);
  }
  // open sheet: keep its "Getting Ready N% · ETA" chip/button live with the same poll
  function paintSheetStage(p) {
    if (!current || !current.data) return;
    const n = (p[current.type] || []).find((y) => String(y.id) === String(current.id)); if (!n) return;
    const st = { progress: n.progress, importing: n.importing, label: n.label, bar: n.bar, eta: n.eta };
    if (current.type === 'tv') current.data.stage = st; else if (current.data.status) Object.assign(current.data.status, st);
    document.querySelectorAll('#sheet-body .chip.dl, #sheet-body .st.dl:not(.epst):not(.gr)').forEach((el) => { el.textContent = dlText(st); });
    const list = document.querySelector('#sheet-body .list[data-season]');
    if (list) list.querySelectorAll('.row.ep.dl').forEach((row) => {
      const es = (n.eps || {})[`${list.dataset.season}-${row.dataset.ep}`] || st;
      const old = row.querySelector('.epst'); if (old) old.outerHTML = epStageHTML(es);
    });
  }
  async function pollProgress() {
    let p;
    try { p = await api('/api/request/progress'); } catch (_) { return scheduleProgress(); }
    const track = $('#recent .rail-track');
    let gone = false;
    (recent[kind] || []).forEach((x) => {
      if (x.progress == null) return;
      const now = (p[kind] || []).find((y) => y.id === x.id);
      if (!now) { gone = true; return; }
      Object.assign(x, { progress: now.progress, importing: now.importing, label: now.label, bar: now.bar, eta: now.eta });
      const c = track && track.querySelector(`.card[data-id="${x.id}"]`); if (!c) return;
      paintStage(c, x);
    });
    ['movie', 'tv'].forEach((k) => { if (k !== kind && recent[k]) recent[k].forEach((x) => { const n = (p[k] || []).find((y) => y.id === x.id); if (n) Object.assign(x, { progress: n.progress, importing: n.importing, label: n.label, bar: n.bar, eta: n.eta }); }); });
    paintSheetStage(p);
    if (gone || (p[kind] || []).some((y) => !(recent[kind] || []).some((x) => x.id === y.id && x.progress != null))) return loadRecent(); // finished/new: refresh rail once (swaps to the green check)
    scheduleProgress();
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(progTimer); else scheduleProgress(); });
  async function loadRecent() {
    clearTimeout(recentTimer);
    try { recent = await api('/api/request/recent'); renderRecent(); scheduleProgress(); } catch (_) { /* gate or transient */ }
    recentTimer = setTimeout(loadRecent, 30000);
  }
  function renderDiscover(d) {
    renderRecent();
    clearInterval(heroTimer);
    const hero = $('#hero'), rails = $('#rails');
    const label = kind === 'tv' ? 'Trending Show' : 'Trending Movie';
    const bd = (b) => `src="${IMG}w780${esc(b)}" srcset="${IMG}w780${esc(b)} 780w, ${IMG}w1280${esc(b)} 1280w" sizes="(min-width:700px) 1100px, 100vw"`;
    hero.innerHTML = d.hero.length ? `<div class="hero-track">${d.hero.map((h, i) => `
      <button class="slide card" data-type="${h.type}" data-id="${h.id}">
        <img class="bd" ${bd(h.backdrop)} alt="" decoding="async" ${i ? 'loading="lazy"' : 'fetchpriority="high"'}>
        <div class="cap"><div class="k">${label}</div><div class="n">${esc(h.title)}</div>
        <div class="m">${h.year || ''}${h.in_library ? `<span class="ok">${ICON.check}</span>Available` : h.wanted ? `<span class="ok want">${ICON.want}</span>Requested` : ''}</div></div>
      </button>`).join('')}</div><div class="dots">${d.hero.map((_, i) => `<i class="${i ? '' : 'on'}"></i>`).join('')}</div>` : '';
    rails.innerHTML = d.rails.map((r) => `<div class="rail"><h2>${esc(r.title)}</h2>
      <div class="rail-track">${r.items.map((it) => `<button class="card" data-type="${it.type}" data-id="${it.id}">
        <div class="poster">${posterHTML(it.poster, it.title, 'w342', '(min-width:700px) 140px, 112px')}${orb(it)}</div></button>`).join('')}</div></div>`).join('');
    const track = hero.querySelector('.hero-track');
    if (!track) return;
    const dots = [...hero.querySelectorAll('.dots i')];
    let i = 0, paused = false;
    const go = (n) => { i = (n + dots.length) % dots.length; track.scrollTo({ left: i * track.clientWidth, behavior: 'smooth' }); };
    track.addEventListener('scroll', () => {
      const n = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      if (n !== i) i = n;
      dots.forEach((dt, k) => dt.classList.toggle('on', k === n));
    }, { passive: true });
    track.addEventListener('touchstart', () => { paused = true; }, { passive: true });
    track.addEventListener('touchend', () => { setTimeout(() => { paused = false; }, 4000); }, { passive: true });
    heroTimer = setInterval(() => { if (!paused && !document.hidden && sheet.hidden) go(i + 1); }, 6000);
    setTimeout(() => prefetchDiscover(), 800);
  }
  $('#discover').addEventListener('click', (e) => {
    const c = e.target.closest('.card'); if (!c) return;
    openDetail(c.dataset.type, +c.dataset.id);
  });

  // ---------------- sheet ----------------
  const sheet = $('#sheet'), scrim = $('#scrim'), body = $('#sheet-body');
  let current = null; // {type,id,data}
  function openSheet() {
    scrim.hidden = false; sheet.hidden = false; body.scrollTop = 0;
    requestAnimationFrame(() => { scrim.classList.add('on'); sheet.classList.add('on'); });
    document.body.style.overflow = 'hidden';
  }
  function closeSheet(instant) {
    if (sheet.hidden) return;
    sheet.classList.remove('on'); scrim.classList.remove('on');
    document.body.style.overflow = '';
    const done = () => { sheet.hidden = true; scrim.hidden = true; sheet.style.transform = ''; body.innerHTML = ''; current = null; };
    instant ? done() : setTimeout(done, 340);
  }
  scrim.addEventListener('click', () => closeSheet());
  $('#sheet-x').addEventListener('click', () => closeSheet());
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });

  // swipe down to dismiss (from the grabber, or anywhere while scrolled to top)
  let y0 = null, dy = 0;
  sheet.addEventListener('touchstart', (e) => {
    if (body.scrollTop > 0 && !e.target.closest('.grab')) { y0 = null; return; }
    y0 = e.touches[0].clientY; dy = 0;
  }, { passive: true });
  sheet.addEventListener('touchmove', (e) => {
    if (y0 == null) return;
    dy = e.touches[0].clientY - y0;
    if (dy > 0) { sheet.classList.add('drag'); sheet.style.transform = `translateY(${dy}px)`; if (e.cancelable) e.preventDefault(); }
  }, { passive: false });
  sheet.addEventListener('touchend', () => {
    if (y0 == null) return;
    sheet.classList.remove('drag');
    if (dy > 110) closeSheet(); else sheet.style.transform = '';
    y0 = null; dy = 0;
  });

  // ---------------- detail ----------------
  async function openDetail(type, id) {
    body.innerHTML = '<div class="spin"></div>';
    openSheet();
    getLook(type, id); getScores(type, id); // in parallel with the base detail
    try {
      const data = await api(`/api/request/${type}/${id}`);
      current = { type, id, data };
      renderDetail();
      scheduleProgress();
    } catch (_) { body.innerHTML = '<p class="empty">Couldn’t load this title</p>'; }
  }

  // never "Downloading": same stage wording as Activity (Getting Ready N% / Copying to library… N% / Processing…)
  function dlText(x) {
    const st = (x && typeof x === 'object') ? x : (current && current.data && current.data.stage) || (x != null ? { progress: x } : null);
    if (!st) return 'Getting Ready';
    return st.label || (st.progress != null ? `Getting Ready ${st.progress}%${st.eta ? ' · ' + st.eta : ''}` : 'Getting Ready');
  }
  function chip(state, extra) {
    if (state === 'available') return `<div class="chip ok">${ICON.checkG}Available</div>`;
    if (state === 'downloading') return `<div class="chip dl">${esc(dlText(extra))}</div>`;
    if (state === 'requested') return `<div class="chip wait"><span class="orb">${ICON.want}</span>Requested</div>`;
    return '';
  }
  // ---- Luma-style detail: backdrop, color blur, clearlogo, meta, ratings, people ----
  const RICON = {
    imdb: url('/static/request/icons/imdb-logo.png'), rtF: url('/static/request/icons/rotten-tomatoes-logo.png'), rtR: url('/static/request/icons/rotten-tomatoes-splat.png'),
    auG: url('/static/request/icons/rotten-tomatoes-popcorn-good.png'), auB: url('/static/request/icons/rotten-tomatoes-popcorn-bad.png'),
  };
  function metaLine(d) {
    const rt = d.runtime ? (d.runtime >= 60 ? `${Math.floor(d.runtime / 60)}h ${d.runtime % 60}m` : `${d.runtime}m`) : '';
    const items = d.type === 'tv'
      ? [d.year, d.seasons_count ? `${d.seasons_count} Season${d.seasons_count === 1 ? '' : 's'}` : '', d.cert, (d.genres || [])[0], d.network]
      : [d.year, rt, d.cert, ...(d.genres || []).slice(0, 2)];
    // dot comes from ::before; fixMetaDots() hides it on the first item of every wrapped line
    return items.filter((x) => x != null && String(x).trim()).map((x) => `<span class="mi">${esc(String(x).trim())}</span>`).join('');
  }
  function fixMetaDots(root) {
    (root || document).querySelectorAll('.dm-meta').forEach((m) => {
      for (let pass = 0; pass < 3; pass++) { // hiding a dot can let an item move up a line: settle
        let top = null, changed = false;
        m.querySelectorAll('.mi').forEach((s) => {
          const t = s.offsetTop, ln = top === null || Math.abs(t - top) > 4;
          if (s.classList.contains('ln') !== ln) { s.classList.toggle('ln', ln); changed = true; }
          top = t;
        });
        if (!changed) break;
      }
    });
  }
  {
    let raf = 0;
    const kick = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => fixMetaDots(document.getElementById('sheet'))); };
    const sb = document.getElementById('sheet-body');
    if (sb) new MutationObserver(kick).observe(sb, { childList: true, subtree: true });
    window.addEventListener('resize', kick);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(kick);
  }
  function airLine(d) {
    if (d.type !== 'tv') return '';
    const t = new Date(); const today = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
    const fmt = (iso) => {
      const dt = new Date(iso + 'T12:00:00');
      const o = { month: 'short', day: 'numeric' };
      if (dt.getFullYear() !== new Date().getFullYear()) o.year = 'numeric';
      return dt.toLocaleDateString('en-US', o);
    };
    if (d.first_air_date && d.first_air_date > today) return `Premieres ${fmt(d.first_air_date)}`;
    const n = d.next_ep;
    if (!n || !n.air_date || n.air_date < today) return '';
    if (n.episode === 1 && n.season === 1) return `Premieres ${fmt(n.air_date)}`;
    if (n.episode === 1 && n.season > 1) return `Season ${n.season} premieres ${fmt(n.air_date)}`;
    return `Next episode ${fmt(n.air_date)}${n.season && n.episode ? ` · S${n.season}E${n.episode}` : ''}`;
  }
  function ratingsHTML(r) {
    if (!r) return '';
    const it = [];
    if (r.imdb != null) it.push([RICON.imdb, 'IMDb', String(r.imdb)]);
    if (r.rotten_tomatoes_critic != null) it.push([+r.rotten_tomatoes_critic >= 60 ? RICON.rtF : RICON.rtR, 'RT Critic', `${r.rotten_tomatoes_critic}%`]);
    if (r.rotten_tomatoes_audience != null) it.push([+r.rotten_tomatoes_audience >= 60 ? RICON.auG : RICON.auB, 'RT Audience', `${r.rotten_tomatoes_audience}%`]);
    return it.map(([src, alt, v]) => `<span class="rt-i"><img src="${src}" alt="${alt}"><b>${esc(v)}</b></span>`).join('');
  }
  function blobs(c) {
    if (!c) return [];
    if (c.format === 'palette' && c.blobs) return c.blobs.map((b) => [b.color, b.x, b.y]);
    const out = [];
    if (c.topLeft) out.push([c.topLeft, 0.12, 0.2]);
    if (c.topRight) out.push([c.topRight, 0.88, 0.2]);
    if (c.bottomRight) out.push([c.bottomRight, 0.85, 0.85]);
    if (c.bottomLeft) out.push([c.bottomLeft, 0.15, 0.85]);
    return out;
  }
  function blurHTML(c) {
    const b = blobs(c);
    return b.length ? `<div class="ub">${b.map(([col, x, y]) => `<span style="background:${esc(col)};left:${x * 100}%;top:${y * 100}%"></span>`).join('')}</div>` : '';
  }
  function avgColor(c) {
    const b = blobs(c); if (!b.length) return null;
    let r = 0, g = 0, bl = 0, n = 0;
    b.forEach(([col]) => {
      const m = String(col).match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/), h = String(col).replace('#', '');
      const v = m ? [+m[1], +m[2], +m[3]] : h.length === 6 ? [0, 2, 4].map((i) => parseInt(h.substr(i, 2), 16)) : null;
      if (v) { r += v[0]; g += v[1]; bl += v[2]; n++; }
    });
    return n ? `rgb(${Math.round(r / n)},${Math.round(g / n)},${Math.round(bl / n)})` : null;
  }
  function peopleHTML(list) {
    if (!list || !list.length) return '';
    return `<h2 class="sec dm-sec">Cast &amp; Crew</h2><div class="ppl">${list.map((p) => `
      <div class="pp">${p.photo ? `<img loading="lazy" decoding="async" src="${IMG}w185${esc(p.photo)}" alt="">` : `<span class="pp-ph">${esc((p.name || '?').split(' ').map((w) => w[0]).slice(0, 2).join(''))}</span>`}
      <div class="pn">${esc(p.name)}</div><div class="pr">${esc(p.role)}</div></div>`).join('')}</div>`;
  }
  function frame(d, actHTML, extraHTML) {
    const bd = d.backdrop ? `<img src="${IMG}w1280${esc(d.backdrop)}" srcset="${IMG}w780${esc(d.backdrop)} 780w, ${IMG}w1280${esc(d.backdrop)} 1280w" sizes="(min-width:700px) 680px, 100vw" alt="" fetchpriority="high">`
      : d.poster ? `<img class="bd-p" src="${IMG}w780${esc(d.poster)}" alt="">` : '';
    return `<div class="dm-top"></div>
      <div class="dm-bd">${bd}<div class="dm-vig"></div></div>
      <div class="dm-c">
        <div class="dm-ub"></div>
        <div class="dm-logo"><h1 class="dm-title">${esc(d.title)}</h1></div>
        <div class="dm-meta">${metaLine(d)}</div>
        ${airLine(d) ? `<div class="dm-air">${esc(airLine(d))}</div>` : ''}
        <div class="dm-rt"></div>
        ${actHTML ? `<div class="act">${actHTML}</div>` : ''}
        ${d.overview ? `<p class="ov">${esc(d.overview)}</p>` : ''}
        <div class="dm-ppl">${peopleHTML(d.people)}</div>
        ${extraHTML || ''}
        <div style="height:36px"></div>
      </div>`;
  }
  const lookP = {}, scoresP = {}, logoImg = {};
  function getLook(type, id) {
    const k = type + id;
    return lookP[k] || (lookP[k] = api(`/api/request/look/${type}/${id}`).then((x) => {
      if (x.logo && !logoImg[k]) { const im = new Image(); im.decoding = 'async'; im.src = x.logo; logoImg[k] = im; } // preload
      return x;
    }).catch(() => { delete lookP[k]; return null; }));
  }
  function getScores(type, id) {
    const k = type + id;
    return scoresP[k] || (scoresP[k] = api(`/api/request/scores/${type}/${id}`).catch(() => { delete scoresP[k]; return null; }));
  }
  // background prefetch for hero + visible rail titles (idle, 2 at a time)
  const idle = window.requestIdleCallback || ((f) => setTimeout(f, 300));
  function prefetch(list) {
    const q = list.filter(([t, i]) => !lookP[t + i]);
    let running = 0;
    const next = () => {
      while (running < 2 && q.length) {
        const [t, i] = q.shift(); running++;
        Promise.all([getLook(t, i), getScores(t, i)]).finally(() => { running--; idle(next); });
      }
    };
    idle(next);
  }
  function prefetchDiscover() {
    const root = $('#discover'); if (!root) return;
    const list = [];
    root.querySelectorAll('.slide[data-id]').forEach((c) => list.push([c.dataset.type, c.dataset.id]));
    root.querySelectorAll('.rail').forEach((r) => [...r.querySelectorAll('.card[data-id]')].slice(0, 4).forEach((c) => list.push([c.dataset.type, c.dataset.id])));
    prefetch(list);
  }
  function decorate(d) {
    const live = () => current && current.data === d && body.querySelector('.dm-c');
    getLook(d.type, d.id).then((x) => {
      const c = live(); if (!c || !x) return;
      if (x.logo) {
        const img = logoImg[d.type + d.id] || new Image();
        const put = () => { const l = c.querySelector('.dm-logo'); if (l && live() === c) { l.innerHTML = ''; img.className = 'logo'; img.alt = d.title; l.appendChild(img); } };
        if (img.complete && img.naturalWidth) put(); else { img.addEventListener('load', put, { once: true }); if (!img.src) img.src = x.logo; }
      }
      const ub = c.querySelector('.dm-ub'); if (ub) ub.innerHTML = blurHTML(x.colors);
      const avg = avgColor(x.colors), top = body.querySelector('.dm-top');
      if (avg && top) top.style.setProperty('--tint', avg);
    });
    const paintScores = (x, tries) => {
      const c = live(); if (!c || !x) return;
      const rt = c.querySelector('.dm-rt'); if (rt) rt.innerHTML = ratingsHTML(x.ratings);
      if (x.ratings && x.ratings.partial && tries < 4) { // a slow source is still finishing: fill in progressively
        setTimeout(() => {
          delete scoresP[d.type + d.id];
          getScores(d.type, d.id).then((y) => paintScores(y, tries + 1));
        }, 1500);
      }
    };
    getScores(d.type, d.id).then((x) => paintScores(x, 0));
  }
  // scroll-top tint: fades in once the backdrop has scrolled away so content never shows through the status/grab area
  body.addEventListener('scroll', () => {
    const t = body.querySelector('.dm-top'), bd = body.querySelector('.dm-bd'); if (!t || !bd) return;
    const edge = bd.offsetHeight * 0.55;
    t.style.opacity = Math.max(0, Math.min(1, (body.scrollTop - edge) / 120));
  }, { passive: true });

  function renderDetail() {
    const d = current.data;
    if (d.type === 'movie') {
      const s = d.status.state;
      const act = s === 'requestable' ? '<button class="pill" id="req-movie">Request</button>' : (d.status.label ? `<div class="chip dl">${esc(d.status.label)}</div>` : chip(s, d.status));
      body.innerHTML = frame(d, act);
      const b = $('#req-movie'); if (b) b.onclick = () => submit(b, `/api/request/movie/${d.id}`, {}, d.title);
    } else {
      // show sheet: never any download status on the main button; in flight = it was requested
      const s = d.status.state === 'downloading' ? 'requested' : d.status.state;
      const act = s === 'available' || s === 'requested' ? chip(s) : '';
      const lim = d.busy_season != null
        ? `Season ${d.busy_season} is on its way. Come back to request the next one once it’s available.`
        : 'One season at a time. Come back to request the next one.';
      body.innerHTML = frame(d, act, `<h2 class="sec dm-sec">Seasons</h2><p class="limit">${lim}</p>
        <div class="list">${d.seasons.map(seasonRow).join('')}</div>`);
      body.querySelectorAll('[data-season]').forEach((r) => (r.onclick = () => openSeason(+r.dataset.season)));
    }
    body.querySelector('.ov')?.addEventListener('click', (e) => e.currentTarget.classList.toggle('open'));
    decorate(d);
  }
  function seasonState(s) {
    if (s.getting_ready) return `<span class="st dl gr">${s.getting_ready} episode${s.getting_ready === 1 ? '' : 's'} getting ready</span>`;
    switch (s.status) {
      case 'available': return `<span class="st ok">${ICON.checkG.replace('<svg', '<svg width="16" height="16"')}</span>`;
      case 'partial': return `<span class="st">${s.available} of ${s.aired}</span>`;
      case 'downloading': return `<span class="st dl">${esc(dlText())}</span>`;
      case 'requested': return '<span class="st">Requested</span>';
      case 'upcoming': return '<span class="st">Upcoming</span>';
      default: return '';
    }
  }
  // one in-flight episode row: Getting Ready N% + ETA / Copying to library N% / Processing…, thin bar
  function epStageHTML(st) {
    st = st || {};
    const txt = st.label ? esc(st.label) : (st.progress != null ? `Getting Ready ${st.progress}%` : 'Getting Ready');
    const w = st.progress != null ? Math.max(3, st.progress) : 3;
    return `<span class="st dl epst"><span class="t">${txt}</span>${!st.label && st.eta ? `<small>${esc(st.eta)}</small>` : ''}<span class="pbar thin${st.importing ? ' done' : ''}"${st.bar === false ? ' hidden' : ''}><i style="width:${w}%"></i></span></span>`;
  }
  function seasonRow(s) {
    const sub = [`${s.episodes} episode${s.episodes === 1 ? '' : 's'}`, s.year].filter(Boolean).join(' · ');
    return `<button class="row" data-season="${s.season}"><div class="main">${esc(s.name)}<div class="sub">${esc(sub)}</div></div>${seasonState(s)}${ICON.chev}</button>`;
  }

  // ---------------- season ----------------
  let selected = new Set();
  async function openSeason(n) {
    const d = current.data;
    selected = new Set();
    body.innerHTML = `<div class="nav"><button class="back" id="back">${ICON.back}${esc(d.title.length > 18 ? 'Back' : d.title)}</button><div class="ttl"></div></div><div class="spin"></div>`;
    $('#back').onclick = () => { renderDetail(); body.scrollTop = 0; };
    body.scrollTop = 0;
    try {
      const sd = await api(`/api/request/tv/${d.id}/season/${n}`);
      renderSeason(sd);
    } catch (_) { body.querySelector('.spin').outerHTML = '<p class="empty">Couldn’t load this season</p>'; }
  }
  function renderSeason(sd) {
    const d = current.data;
    const can = sd.episodes.filter((e) => e.status === 'requestable' || e.status === 'upcoming_requestable');
    const nav = body.querySelector('.nav');
    nav.querySelector('.ttl').textContent = sd.name;
    const rows = sd.episodes.map((e) => {
      const cls = e.status === 'available' ? 'have' : e.status === 'requested' ? 'wait' : e.status === 'downloading' ? 'dl' : '';
      const date = e.air_date ? new Date(e.air_date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
      const st = e.status === 'requested' ? '<span class="st">Requested</span>' : e.status === 'downloading' ? epStageHTML(e.stage) : '';
      return `<button class="row ep ${cls}" data-ep="${e.episode}" ${cls ? 'disabled' : ''}>
        <span class="tick">${ICON.check}</span>
        <div class="main"><span class="num">${e.episode}.</span>${esc(e.name)}<div class="sub">${esc(date)}</div></div>${st}</button>`;
    }).join('');
    body.innerHTML = '';
    body.appendChild(nav);
    const busy = d.busy_season != null && d.busy_season !== sd.season;
    body.insertAdjacentHTML('beforeend', `${can.length && !busy ? '<p class="hint">Select episodes or add the entire season below.</p>' : ''}<div class="list" data-season="${sd.season}" style="margin-top:8px">${rows}</div><div class="bar" id="bar"></div>`);
    $('#back').onclick = () => { renderDetail(); body.scrollTop = 0; };
    const bar = $('#bar');
    const paint = () => {
      if (busy && can.length) bar.innerHTML = `<p class="limit">Season ${d.busy_season} is on its way. Come back to request this one once it’s available.</p>`;
      else if (selected.size) bar.innerHTML = `<button class="pill" id="req-eps">Request ${selected.size} Episode${selected.size === 1 ? '' : 's'}</button>`;
      else if (can.length) bar.innerHTML = `<button class="pill" id="req-season">Request ${can.length === sd.episodes.length ? 'Season' : 'Rest of Season'}</button>`;
      else bar.innerHTML = '';
      const a = $('#req-eps'), b = $('#req-season');
      if (a) a.onclick = () => submit(a, `/api/request/tv/${d.id}`, { scope: 'episodes', season: sd.season, episodes: [...selected] },
        `${d.title} · ${epLabel(sd.season, [...selected])}`);
      if (b) b.onclick = () => submit(b, `/api/request/tv/${d.id}`, { scope: 'season', season: sd.season }, `${d.title} · ${sd.name}`);
    };
    if (!busy) body.querySelectorAll('.ep:not([disabled])').forEach((r) => (r.onclick = () => {
      const n = +r.dataset.ep;
      selected.has(n) ? selected.delete(n) : selected.add(n);
      r.classList.toggle('sel', selected.has(n));
      paint();
    }));
    paint();
  }
  function epLabel(s, eps) {
    eps.sort((a, b) => a - b);
    return eps.length === 1 ? `S${s} E${eps[0]}` : `S${s} E${eps.join(', E')}`;
  }

  // ---------------- request ----------------
  async function submit(btn, path, payload, label) {
    btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = '<div class="spin"></div>';
    try {
      const r = await api(path, { method: 'POST', body: payload });
      body.scrollTop = 0;
      body.innerHTML = `<div class="done">
        <svg class="ring" viewBox="0 0 84 84"><circle cx="42" cy="42" r="36" fill="none" stroke="#30d158" stroke-width="3"/><path d="M27 43l10 10 21-23" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <h2>${r.already && r.state === 'available' ? 'Already Available' : 'Requested'}</h2><p>${esc(label)}</p>
        ${r.already && r.state === 'available' ? '' : '<p class="hint done-hint">Check Recently Added to see when it’s ready.</p>'}
        <button class="pill" id="done">Done</button>
</div>`;
      $('#done').onclick = () => closeSheet();
      if (!(r.already && r.state === 'available')) setTimeout(loadRecent, 4000);
    } catch (e) {
      btn.disabled = false; btn.innerHTML = old;
      if (e.data && e.data.message) {
        let m = btn.parentElement.querySelector('.limit.err');
        if (!m) { m = document.createElement('p'); m.className = 'limit err'; btn.after(m); }
        m.textContent = e.data.message;
      }
      btn.classList.remove('shake'); void btn.offsetWidth; btn.classList.add('shake');
    }
  }

  // ---------------- pull to refresh (port of Luma's usePullToRefresh) ----------------
  (() => {
    const THRESHOLD = 92, MAX_PULL = 132, RESISTANCE = 0.42, TOP_SLOP = 2, AXIS_LOCK = 10, H_ABORT = 8, V_ENGAGE = 10;
    const ptr = $('#ptr'), spin = ptr.querySelector('i'), homeEl = $('#discover'); // only the content moves; the fixed header stays put
    let sx = 0, sy = 0, armed = false, pulling = false, axis = 'u', px = 0, busy = false;
    const top = () => window.scrollY || document.documentElement.scrollTop || 0;
    const owned = (e) => !sheet.hidden && sheet.classList.contains('on') || $('#home').hidden ||
      !ss.hidden || (e.target instanceof Element && e.target.closest('#sheet, #scrim, #ss, input'));
    const set = (v) => {
      px = v;
      // margin (not transform) so the sticky Movies/TV switch moves down with the content too
      homeEl.style.marginTop = v > 0 ? `${v}px` : '';
      homeEl.style.transition = v > 0 || busy ? 'none' : 'margin-top 180ms ease-out';
      ptr.classList.toggle('on', v > 0 || busy);
      // spinner sits centered in the gap the pull opens under the header (never over the header or switch)
      ptr.style.transform = `translateY(${Math.max(0, (Math.max(v, busy ? 56 : 0) - 40) / 2)}px)`;
      ptr.style.opacity = busy ? 1 : Math.max(0.25, Math.min(1, v / THRESHOLD));
      spin.classList.toggle('spin', busy || v >= THRESHOLD);
      if (!busy) spin.style.transform = `rotate(${v * 2.4}deg)`;
    };
    const reset = () => { armed = false; pulling = false; axis = 'u'; set(0); };
    document.addEventListener('touchstart', (e) => {
      if (busy || owned(e) || top() > TOP_SLOP) return reset();
      const t = e.touches[0]; sx = t.clientX; sy = t.clientY; armed = true; pulling = false; axis = 'u';
    }, { passive: true });
    document.addEventListener('touchmove', (e) => {
      if (!armed || busy || axis === 'h') return;
      if (owned(e) || top() > TOP_SLOP) return reset();
      const t = e.touches[0], dx = t.clientX - sx, dy = t.clientY - sy, ax = Math.abs(dx), ay = Math.abs(dy);
      if (axis === 'u') {
        if (ax < AXIS_LOCK && ay < AXIS_LOCK) return;
        if (ax > ay || ax >= H_ABORT) { axis = 'h'; armed = false; return set(0); }
        if (dy <= 0) { armed = false; return set(0); }
        axis = 'v';
      }
      if (dy <= 0) { pulling = false; return set(0); }
      if (dy < V_ENGAGE && !pulling) return;
      pulling = true;
      set(Math.min(dy * RESISTANCE, MAX_PULL));
      if (e.cancelable) e.preventDefault();
    }, { passive: false });
    const end = async () => {
      if (!armed) { axis = 'u'; pulling = false; return; }
      armed = false; const vert = axis === 'v'; axis = 'u'; pulling = false;
      if (!(vert && px >= THRESHOLD) || busy) return set(0);
      busy = true; set(56);
      try {
        const t0 = Date.now();
        const d = await api('/api/request/home?fresh=' + t0);
        home = d;
        renderDiscover(home[kind]);
        if (!d.owned_ready && !ownedTimer) pollOwned(0);
        await loadRecent();
        const wait = 500 - (Date.now() - t0); if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      } catch (_) { /* gate or transient */ }
      busy = false; set(0);
    };
    document.addEventListener('touchend', end, { passive: true });
    document.addEventListener('touchcancel', end, { passive: true });
  })();

  // ---------------- boot ----------------
  // start the home payload alongside the session check (a returning visitor sees rails right away)
  fetch(url('/api/request/home'), { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)).then((d) => { if (d && !home) home = d; }).catch(() => {});
  api('/api/request/session').then((s) => (s.unlocked ? showHome() : showGate())).catch(() => showGate());
})();

// iOS PWA: resume/rotation can leave a stale viewport or a stuck drag -> sheet stops short. Reset both.
(function(){
  const fixVp=()=>{document.documentElement.style.setProperty('--vh', window.innerHeight+'px');
    const sh=document.getElementById('sheet'); if(sh){sh.classList.remove('drag'); if(sh.classList.contains('on')) sh.style.transform='';}};
  ['resize','orientationchange','pageshow','focus'].forEach(e=>window.addEventListener(e,()=>setTimeout(fixVp,60)));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden) setTimeout(fixVp,60);});
  if(window.visualViewport) visualViewport.addEventListener('resize',fixVp);
  document.addEventListener('touchcancel',()=>{const sh=document.getElementById('sheet'); if(sh){sh.classList.remove('drag'); sh.style.transform='';}},{passive:true});
  fixVp();
})();
