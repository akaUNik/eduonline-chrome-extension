"use strict";

(() => {
  const MAX_CANDIDATES = 8;
  const PLAYER_PATH = /^\/v\/[A-Za-z0-9_-]{6,128}$/;

  function canonicalLessonUrl(rawUrl) {
    try {
      const url = new URL(rawUrl);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      const host = url.hostname.toLowerCase();
      if (host !== 'eduonline.io' && !host.endsWith('.eduonline.io')) return null;
      if (!url.pathname.startsWith('/learn/')) return null;
      url.hash = '';
      return url.href;
    } catch {
      return null;
    }
  }

  function playerCandidate(rawUrl) {
    try {
      const url = new URL(rawUrl);
      if (url.protocol !== 'https:' || url.hostname !== 'v.accelsite.io') return null;
      if (!PLAYER_PATH.test(url.pathname) || url.hash) return null;
      const allowed = new Set(['showTitle', 'showControls', 'muted', 'autoplay']);
      for (const [key, value] of url.searchParams) {
        if (!allowed.has(key) || !['true', 'false'].includes(value)) return null;
      }
      return url.href;
    } catch {
      return null;
    }
  }

  function candidateFingerprint(candidates) {
    let hash = 0x811c9dc5;
    for (const character of candidates.join('\n')) {
      hash ^= character.charCodeAt(0);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return `${candidates.length}:${hash.toString(16).padStart(8, '0')}`;
  }

  function collectDiscovery() {
    const lessonUrl = canonicalLessonUrl(window.location.href);
    if (!lessonUrl) return {
      lessonUrl: null,
      pageTitle: '',
      candidates: [],
      candidateFingerprint: candidateFingerprint([]),
    };
    const candidates = [];
    const seen = new Set();
    for (const frame of document.querySelectorAll('iframe[src]')) {
      const candidate = playerCandidate(frame.src);
      if (candidate && !seen.has(candidate)) {
        seen.add(candidate);
        candidates.push(candidate);
        if (candidates.length >= MAX_CANDIDATES) break;
      }
    }
    return {
      lessonUrl,
      pageTitle: document.title || '',
      candidates,
      candidateFingerprint: candidateFingerprint(candidates),
    };
  }

  const api = { canonicalLessonUrl, playerCandidate, candidateFingerprint, collectDiscovery };
  globalThis.EduonlineDiscovery = api;

  if (globalThis.chrome?.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type !== 'collect-discovery') return false;
      sendResponse(collectDiscovery());
      return false;
    });
  }
})();
