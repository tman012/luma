/**
 * Plex Mobile Deep Link Injector for SPA
 * Injects a "Play on this device" button into the PlaybackModal on mobile.
 */
(function() {
  'use strict';

  var PLEX_MACHINE_ID = 'a6ce22ee1305ed380244886f494bb43c97bbf227';

  function isMobileDevice() {
    if (window.innerWidth <= 1024 && 'ontouchstart' in window) return true;
    var ua = navigator.userAgent || '';
    if (/android/i.test(ua)) return true;
    if (/iPad|iPhone|iPod/.test(ua)) return true;
    if (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) return true;
    return false;
  }

  if (!isMobileDevice()) return;

  var lastKnownRatingKey = null;

  // Intercept fetch to capture ratingKey from various API calls
  var origFetch = window.fetch;
  window.fetch = function(input, init) {
    var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');

    // Capture from resume_info
    var resumeMatch = url.match(/\/api\/plex\/resume_info\/(\d+)/);
    if (resumeMatch) {
      lastKnownRatingKey = resumeMatch[1];
    }

    // Capture from tv seasons
    var tvMatch = url.match(/\/api\/plex\/tv\/(\d+)\/seasons/);
    if (tvMatch) {
      lastKnownRatingKey = tvMatch[1];
    }

    // Capture from POST body
    if (init && init.body && typeof init.body === 'string') {
      try {
        var body = JSON.parse(init.body);
        if (body.rating_key) {
          lastKnownRatingKey = String(body.rating_key);
        }
      } catch(e) {}
    }

    return origFetch.apply(this, arguments);
  };

  // Extract ratingKey from React fiber tree
  function extractRatingKeyFromFiber(domNode) {
    // React stores fiber on DOM nodes with keys like __reactFiber$xxx or __reactInternalInstance$xxx
    var keys = Object.keys(domNode);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactInternal') === 0) {
        var fiber = domNode[keys[i]];
        // Walk up the fiber tree looking for the PlaybackModal component with ratingKey prop
        var current = fiber;
        var depth = 0;
        while (current && depth < 30) {
          if (current.memoizedProps) {
            var props = current.memoizedProps;
            if (props.ratingKey && typeof props.ratingKey === 'string') {
              return props.ratingKey;
            }
            // Also check for numeric ratingKey
            if (props.ratingKey && typeof props.ratingKey === 'number') {
              return String(props.ratingKey);
            }
          }
          // Check pendingProps too
          if (current.pendingProps) {
            var pp = current.pendingProps;
            if (pp.ratingKey) return String(pp.ratingKey);
          }
          current = current.return;
          depth++;
        }
      }
    }
    return null;
  }

  function getRatingKey(modalEl) {
    // Try fiber extraction first
    var rk = extractRatingKeyFromFiber(modalEl);
    if (rk) return rk;

    // Try children
    var children = modalEl.querySelectorAll('*');
    for (var i = 0; i < Math.min(children.length, 50); i++) {
      rk = extractRatingKeyFromFiber(children[i]);
      if (rk) return rk;
    }

    // Fallback to intercepted value
    return lastKnownRatingKey;
  }

  function buildPlexDeepLink(ratingKey) {
    return 'plex://server/' + PLEX_MACHINE_ID +
      '/com.plexapp.plugins.library/library/metadata/' + ratingKey;
  }

  function createMobileButton(getRatingKeyFn) {
    var wrapper = document.createElement('div');
    wrapper.id = 'spa-plex-mobile-wrapper';
    wrapper.style.cssText = 'padding: 16px 24px 0; position: relative; z-index: 1;';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.style.cssText = [
      'display: flex',
      'align-items: center',
      'justify-content: center',
      'gap: 12px',
      'width: 100%',
      'padding: 18px 24px',
      'border-radius: 50px',
      'border: 2px solid #3b82f6',
      'background: linear-gradient(135deg, rgba(59,130,246,0.25), rgba(59,130,246,0.10))',
      'color: #fff',
      'font-size: 17px',
      'font-weight: 700',
      'cursor: pointer',
      'transition: all 0.2s ease',
      '-webkit-tap-highlight-color: transparent',
      'box-shadow: 0 4px 15px rgba(59,130,246,0.3)'
    ].join(';');

    btn.innerHTML = '<span style="font-size:22px">📱</span> <span>Play on this device</span>';

    btn.addEventListener('touchstart', function() {
      btn.style.transform = 'scale(0.97)';
    }, {passive: true});
    btn.addEventListener('touchend', function() {
      btn.style.transform = '';
    }, {passive: true});

    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      e.preventDefault();

      var rk = getRatingKeyFn();
      if (!rk) {
        btn.innerHTML = '<span style="font-size:22px">⚠️</span> <span>Could not detect media</span>';
        setTimeout(function() {
          btn.innerHTML = '<span style="font-size:22px">📱</span> <span>Play on this device</span>';
        }, 2000);
        return;
      }

      btn.innerHTML = '<span style="font-size:22px">⏳</span> <span>Opening Plex...</span>';
      btn.style.pointerEvents = 'none';

      var deepLink = buildPlexDeepLink(rk);
      console.log('[MobileDeepLink] Opening:', deepLink);

      window.location.href = deepLink;

      setTimeout(function() {
        btn.innerHTML = '<span style="font-size:22px">✅</span> <span>Opened in Plex</span>';
        btn.style.border = '2px solid #22c55e';
        btn.style.background = 'linear-gradient(135deg, rgba(34,197,94,0.25), rgba(34,197,94,0.10))';
        btn.style.boxShadow = '0 4px 15px rgba(34,197,94,0.3)';
        setTimeout(function() {
          btn.innerHTML = '<span style="font-size:22px">📱</span> <span>Play on this device</span>';
          btn.style.border = '2px solid #3b82f6';
          btn.style.background = 'linear-gradient(135deg, rgba(59,130,246,0.25), rgba(59,130,246,0.10))';
          btn.style.boxShadow = '0 4px 15px rgba(59,130,246,0.3)';
          btn.style.pointerEvents = '';
        }, 3000);
      }, 1500);
    });

    var sep = document.createElement('div');
    sep.style.cssText = [
      'text-align: center',
      'color: rgba(255,255,255,0.35)',
      'font-size: 13px',
      'font-weight: 500',
      'padding: 12px 0 4px'
    ].join(';');
    sep.textContent = 'or cast to a TV';

    wrapper.appendChild(btn);
    wrapper.appendChild(sep);
    return wrapper;
  }

  function injectMobileButton(modal) {
    if (modal.querySelector('#spa-plex-mobile-wrapper')) return;

    // Create a closure that will try to get ratingKey when button is clicked
    var modalRef = modal;
    var getRatingKeyFn = function() {
      return getRatingKey(modalRef);
    };

    // Find the modal container — second child of the portal (first is the backdrop overlay)
    var modalBox = null;
    var children = modal.children;
    for (var i = 0; i < children.length; i++) {
      var child = children[i];
      // The modal box has position: relative, borderRadius, overflow: hidden
      if (child.style && child.style.position === 'relative' &&
          child.style.overflow === 'hidden') {
        modalBox = child;
        break;
      }
    }

    if (!modalBox && children.length >= 2) {
      modalBox = children[1];
    }

    if (!modalBox) {
      console.log('[MobileDeepLink] Could not find modal container');
      return;
    }

    // Find the content div (has padding)
    var contentDiv = null;
    var kids = modalBox.children;
    for (var j = 0; j < kids.length; j++) {
      if (kids[j].style && kids[j].style.padding) {
        contentDiv = kids[j];
        break;
      }
    }

    if (!contentDiv) contentDiv = modalBox;

    // Insert the mobile button at the top
    var mobileBtn = createMobileButton(getRatingKeyFn);
    if (contentDiv.firstChild) {
      contentDiv.insertBefore(mobileBtn, contentDiv.firstChild);
    } else {
      contentDiv.appendChild(mobileBtn);
    }

    console.log('[MobileDeepLink] Button injected. RatingKey:', getRatingKeyFn());
  }

  // Watch for PlaybackModal to appear
  var observer = new MutationObserver(function(mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var node = added[j];
        if (node.nodeType !== 1) continue;

        if (node.getAttribute && node.getAttribute('data-modal') === 'PlaybackModal') {
          (function(n) {
            setTimeout(function() { injectMobileButton(n); }, 200);
          })(node);
        }

        if (node.querySelectorAll) {
          var modals = node.querySelectorAll('[data-modal="PlaybackModal"]');
          for (var k = 0; k < modals.length; k++) {
            (function(m) {
              setTimeout(function() { injectMobileButton(m); }, 200);
            })(modals[k]);
          }
        }
      }
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });

  console.log('[MobileDeepLink] Plex mobile deep link injector active');
})();
