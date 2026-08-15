"use strict";

(() => {
  const NATIVE_HOST_NAME = 'io.eduonline.ytdlp';
  const STORAGE_KEY = 'downloadState';
  let nativePort = null;
  let currentState = { phase: 'idle' };
  let restorePromise = null;
  const pending = new Map();
  const downloads = new Map();
  const probeSecrets = new Map();

  function lessonKey(tabId, lessonUrl) {
    return `${tabId}:${lessonUrl}`;
  }

  function requestId() {
    return globalThis.crypto?.randomUUID?.() || `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function restore() {
    if (!restorePromise) {
      restorePromise = chrome.storage.session.get(STORAGE_KEY).then((stored) => {
        const candidate = stored?.[STORAGE_KEY];
        if (candidate && typeof candidate === 'object') currentState = candidate;
      });
    }
    return restorePromise;
  }

  function persist(state) {
    currentState = structuredClone(state);
    chrome.storage.session.set({ [STORAGE_KEY]: currentState });
    chrome.runtime.sendMessage({ type: 'state-updated', state: currentState }).catch(() => {});
  }

  function publicProbe(payload, key, discoveryFingerprint) {
    const tokens = new Map();
    const videos = (payload?.videos || []).map((entry) => {
      const { probeToken, ...metadata } = entry;
      tokens.set(metadata.videoId, probeToken);
      return metadata;
    });
    if (!videos.length) throw { code: 'NO_FORMATS', message: 'No supported videos were returned.' };
    probeSecrets.set(key, tokens);
    return {
      phase: 'ready',
      lessonKey: key,
      discoveryFingerprint,
      videos,
      selectedVideoId: videos[0].videoId,
      probeSummary: payload.summary || { candidateCount: videos.length, failures: {} },
    };
  }

  function shouldRefreshPartialProbe(state) {
    if (state?.phase !== 'ready') return false;
    const summary = state.probeSummary;
    if (!summary || summary.candidateCount <= (state.videos?.length || 0)) return false;
    return Object.values(summary.failures || {}).some((count) => Number(count) > 0);
  }

  function ensureNativePort() {
    if (nativePort) return nativePort;
    nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    nativePort.onMessage.addListener(handleNativeMessage);
    nativePort.onDisconnect.addListener(handleNativeDisconnect);
    return nativePort;
  }

  function handleNativeMessage(message) {
    const tracked = pending.get(message?.requestId);
    const downloadContext = downloads.get(message?.requestId);
    if (message?.version !== 2) {
      const error = {
        code: 'UNSUPPORTED_VERSION',
        message: 'The extension and native host protocol versions do not match.',
      };
      const key = tracked?.key || downloadContext?.key || currentState.lessonKey;
      persist({ phase: 'error', lessonKey: key, error });
      tracked?.reject(error);
      pending.delete(message?.requestId);
      downloads.delete(message?.requestId);
      return;
    }
    if (message?.event === 'error') {
      const key = tracked?.key || downloadContext?.key || currentState.lessonKey;
      const state = { phase: 'error', lessonKey: key, error: message.error };
      persist(state);
      tracked?.reject(message.error);
      pending.delete(message.requestId);
      downloads.delete(message.requestId);
      return;
    }
    if (tracked?.kind === 'probe' && message.event === 'result') {
      const state = publicProbe(message.payload, tracked.key, tracked.discoveryFingerprint);
      persist(state);
      tracked.resolve(state);
      pending.delete(message.requestId);
      return;
    }
    if (tracked?.kind === 'download' && message.event === 'accepted') {
      const context = {
        key: tracked.key,
        videos: tracked.videos,
        selectedVideoId: tracked.selectedVideoId,
        discoveryFingerprint: tracked.discoveryFingerprint,
      };
      downloads.set(message.requestId, context);
      const state = {
        phase: 'downloading',
        lessonKey: tracked.key,
        videos: tracked.videos,
        selectedVideoId: tracked.selectedVideoId,
        discoveryFingerprint: tracked.discoveryFingerprint,
        download: message.payload,
      };
      persist(state);
      tracked.resolve(state);
      pending.delete(message.requestId);
      return;
    }
    if (downloadContext && ['progress', 'complete'].includes(message.event)) {
      const state = {
        phase: message.event === 'complete' ? 'complete' : 'downloading',
        lessonKey: downloadContext.key,
        videos: downloadContext.videos,
        selectedVideoId: downloadContext.selectedVideoId,
        discoveryFingerprint: downloadContext.discoveryFingerprint,
        download: message.payload,
      };
      persist(state);
      if (message.event === 'complete') downloads.delete(message.requestId);
    }
  }

  function handleNativeDisconnect() {
    nativePort = null;
    probeSecrets.clear();
    const error = {
      code: 'NATIVE_HOST_UNAVAILABLE',
      message: chrome.runtime.lastError?.message || 'The native host is unavailable.',
    };
    for (const tracked of pending.values()) tracked.reject(error);
    pending.clear();
    downloads.clear();
    persist({ phase: 'error', lessonKey: currentState.lessonKey, error });
  }

  function nativeRequest(kind, key, action, payload, context = {}) {
    return new Promise((resolve, reject) => {
      const id = requestId();
      pending.set(id, { kind, key, resolve, reject, ...context });
      try {
        ensureNativePort().postMessage({ version: 2, requestId: id, action, payload });
      } catch (error) {
        pending.delete(id);
        reject({ code: 'NATIVE_HOST_UNAVAILABLE', message: String(error?.message || error) });
      }
    });
  }

  async function handleClientMessage(message) {
    await restore();
    const key = lessonKey(message.tabId, message.lessonUrl);
    if (message.type === 'get-state') {
      if (currentState.lessonKey !== key) return { phase: 'idle', lessonKey: key };
      if (currentState.discoveryFingerprint !== message.discoveryFingerprint) {
        return { phase: 'idle', lessonKey: key };
      }
      if (shouldRefreshPartialProbe(currentState)) {
        return { phase: 'idle', lessonKey: key };
      }
      if (currentState.phase === 'ready' && !probeSecrets.has(key)) {
        return { phase: 'idle', lessonKey: key };
      }
      if (currentState.phase === 'downloading' && !nativePort) {
        return {
          phase: 'error',
          lessonKey: key,
          error: {
            code: 'NATIVE_HOST_UNAVAILABLE',
            message: 'The previous native-host session ended; start the download again.',
          },
        };
      }
      return currentState;
    }
    if (message.type === 'probe') {
      probeSecrets.delete(key);
      persist({
        phase: 'loading',
        lessonKey: key,
        discoveryFingerprint: message.discoveryFingerprint,
      });
      return nativeRequest('probe', key, 'probe', {
        lessonUrl: message.lessonUrl,
        candidates: message.candidates,
      }, { discoveryFingerprint: message.discoveryFingerprint });
    }
    if (message.type === 'select-video') {
      if (currentState.lessonKey !== key || currentState.phase !== 'ready') {
        throw { code: 'PROBE_EXPIRED', message: 'Video metadata expired; reload the popup.' };
      }
      const selected = currentState.videos.find((video) => video.videoId === message.videoId);
      if (!selected || !probeSecrets.get(key)?.has(message.videoId)) {
        throw { code: 'INVALID_FORMAT', message: 'The selected video is invalid.' };
      }
      const state = { ...currentState, selectedVideoId: selected.videoId };
      persist(state);
      return state;
    }
    if (message.type === 'download') {
      const tokens = probeSecrets.get(key);
      const videoId = message.videoId || currentState.selectedVideoId;
      const token = tokens?.get(videoId);
      if (!token) throw { code: 'PROBE_EXPIRED', message: 'Video metadata expired; reload the popup.' };
      return nativeRequest('download', key, 'download', {
        probeToken: token,
        choiceId: message.choiceId,
      }, {
        videos: currentState.videos,
        selectedVideoId: videoId,
        discoveryFingerprint: currentState.discoveryFingerprint,
      });
    }
    throw { code: 'INVALID_MESSAGE', message: 'Unsupported extension request.' };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!['get-state', 'probe', 'select-video', 'download'].includes(message?.type)) return false;
    handleClientMessage(message).then(
      (state) => sendResponse({ ok: true, state }),
      (error) => sendResponse({ ok: false, error }),
    );
    return true;
  });

  globalThis.EduonlineBackground = {
    nativeHostName: NATIVE_HOST_NAME,
    lessonKey,
    publicProbe,
    shouldRefreshPartialProbe,
    handleNativeMessage,
    handleClientMessage,
    getState: () => structuredClone(currentState),
  };
})();
