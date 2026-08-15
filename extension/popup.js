"use strict";

(() => {
  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '';
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return hours > 0
      ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
      : `${minutes}:${String(remainder).padStart(2, '0')}`;
  }

  function sortFormats(formats) {
    return [...formats].sort((left, right) => {
      if (left.choiceId === 'best') return -1;
      if (right.choiceId === 'best') return 1;
      if (left.audioOnly !== right.audioOnly) return left.audioOnly ? 1 : -1;
      return (right.height || 0) - (left.height || 0);
    });
  }

  function supportedLesson(rawUrl) {
    try {
      const url = new URL(rawUrl);
      const host = url.hostname.toLowerCase();
      return ['http:', 'https:'].includes(url.protocol)
        && (host === 'eduonline.io' || host.endsWith('.eduonline.io'))
        && url.pathname.startsWith('/learn/');
    } catch {
      return false;
    }
  }

  function friendlyConnectionError(error) {
    const message = String(error?.message || error || '');
    if (message.includes('Receiving end does not exist')) {
      return {
        code: 'CONTENT_SCRIPT_UNAVAILABLE',
        message: 'Reload this lesson tab after reloading the unpacked extension, then open the popup again.',
      };
    }
    return error;
  }

  function selectedVideo(state) {
    const videos = state?.videos || [];
    return videos.find((video) => video.videoId === state.selectedVideoId) || videos[0] || null;
  }

  function probeSummaryText(state) {
    const summary = state?.probeSummary;
    const videoCount = state?.videos?.length || 0;
    if (!summary || summary.candidateCount <= videoCount) return '';
    const reasons = [];
    if (summary.duplicatePlayerCount) reasons.push(`${summary.duplicatePlayerCount} duplicate player`);
    if (summary.duplicateMediaCount) reasons.push(`${summary.duplicateMediaCount} duplicate media`);
    const staged = Object.entries(summary.failureStages || {});
    if (staged.length) {
      for (const [stageAndCode, count] of staged) reasons.push(`${count} ${stageAndCode}`);
    } else {
      for (const [code, count] of Object.entries(summary.failures || {})) {
        reasons.push(`${count} ${code}`);
      }
    }
    const suffix = reasons.length ? `: ${reasons.join(', ')}` : '';
    return `${videoCount} of ${summary.candidateCount} player frames supported${suffix}`;
  }

  const api = {
    formatDuration,
    sortFormats,
    supportedLesson,
    friendlyConnectionError,
    selectedVideo,
    probeSummaryText,
  };
  globalThis.EduonlinePopup = api;
  if (!globalThis.document || !globalThis.chrome?.tabs) return;

  const elements = {
    status: document.querySelector('#status'),
    media: document.querySelector('#media'),
    videoField: document.querySelector('#video-field'),
    video: document.querySelector('#video'),
    poster: document.querySelector('#poster'),
    title: document.querySelector('#title'),
    duration: document.querySelector('#duration'),
    quality: document.querySelector('#quality'),
    download: document.querySelector('#download'),
    progress: document.querySelector('#progress'),
    progressBar: document.querySelector('#progress-bar'),
    progressLabel: document.querySelector('#progress-label'),
    error: document.querySelector('#error'),
  };
  let active = null;

  function reset() {
    elements.media.hidden = true;
    elements.videoField.hidden = true;
    elements.progress.hidden = true;
    elements.error.hidden = true;
    elements.download.disabled = true;
  }

  function showError(error) {
    reset();
    elements.status.textContent = 'Unable to download this video';
    elements.error.textContent = error?.message || 'An unexpected error occurred.';
    elements.error.hidden = false;
  }

  function render(state) {
    reset();
    if (state.phase === 'loading') {
      elements.status.textContent = 'Inspecting lesson video…';
      return;
    }
    if (state.phase === 'error') return showError(state.error);
    if (state.phase === 'ready') {
      const videos = state.videos || [];
      const metadata = selectedVideo(state);
      if (!metadata) return showError({ message: 'No supported videos were returned.' });
      const formats = sortFormats(metadata.formats || []);
      const summary = probeSummaryText(state);
      elements.status.textContent = summary || (formats.length ? 'Choose a format' : 'No supported formats found');
      elements.title.textContent = metadata.title || 'Lesson video';
      elements.video.replaceChildren(...videos.map((video, index) => {
        const option = document.createElement('option');
        option.value = video.videoId;
        option.textContent = video.title || `Video ${index + 1}`;
        return option;
      }));
      elements.video.value = metadata.videoId;
      elements.videoField.hidden = videos.length <= 1;
      elements.duration.textContent = formatDuration(metadata.duration);
      elements.duration.hidden = !elements.duration.textContent;
      if (metadata.poster) {
        elements.poster.src = metadata.poster;
        elements.poster.hidden = false;
      } else {
        elements.poster.hidden = true;
      }
      elements.quality.replaceChildren(...formats.map((format) => {
        const option = document.createElement('option');
        option.value = format.choiceId;
        option.textContent = format.label;
        return option;
      }));
      elements.media.hidden = false;
      elements.download.disabled = formats.length === 0;
      return;
    }
    if (['downloading', 'complete'].includes(state.phase)) {
      const download = state.download || {};
      const percent = Number.isFinite(download.percent) ? download.percent : 0;
      elements.status.textContent = state.phase === 'complete'
        ? `Saved: ${download.filename || 'download complete'}`
        : 'Downloading…';
      elements.progressBar.value = percent;
      elements.progressLabel.textContent = `${Math.round(percent)}%`;
      elements.progress.hidden = false;
    }
  }

  function background(message) {
    return chrome.runtime.sendMessage(message).then((response) => {
      if (!response?.ok) throw response?.error || new Error('Extension request failed.');
      return response.state;
    });
  }

  async function initialize() {
    reset();
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !supportedLesson(tab.url)) {
      elements.status.textContent = 'Open an eduonline.io lesson to use this extension.';
      return;
    }
    let discovery;
    try {
      discovery = await chrome.tabs.sendMessage(tab.id, { type: 'collect-discovery' });
    } catch (error) {
      throw friendlyConnectionError(error);
    }
    if (!discovery?.lessonUrl || !discovery.candidates?.length) {
      elements.status.textContent = 'No supported AccelSite video was found on this lesson.';
      return;
    }
    active = {
      tabId: tab.id,
      lessonUrl: discovery.lessonUrl,
      discoveryFingerprint: discovery.candidateFingerprint,
    };
    const restored = await background({ type: 'get-state', ...active });
    if (['ready', 'downloading', 'complete'].includes(restored.phase)) {
      render(restored);
      return;
    }
    render({ phase: 'loading' });
    render(await background({ type: 'probe', ...active, candidates: discovery.candidates }));
  }

  elements.download.addEventListener('click', async () => {
    if (!active || !elements.quality.value) return;
    elements.download.disabled = true;
    try {
      render(await background({
        type: 'download',
        ...active,
        videoId: elements.video.value,
        choiceId: elements.quality.value,
      }));
    } catch (error) {
      showError(error);
    }
  });

  elements.video.addEventListener('change', async () => {
    if (!active || !elements.video.value) return;
    try {
      render(await background({
        type: 'select-video',
        ...active,
        videoId: elements.video.value,
      }));
    } catch (error) {
      showError(error);
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === 'state-updated' && active && message.state?.lessonKey === `${active.tabId}:${active.lessonUrl}`) {
      render(message.state);
    }
  });

  initialize().catch(showError);
})();
