"use strict";

const API = window.location.origin;
let config = null;
let results = [];
let library = [];
let blocked = [];
let adminToken = '';
let latestAdminAccessLink = '';
let currentVideo = null;
let currentPlaybackToken = '';
let currentPlaybackSources = [];
let libraryLoaded = false;
let dmcaLoaded = false;

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const banner = (id, msg = '', tone = 'info') => { const el = $(id); el.textContent = msg; el.className = msg ? `banner show ${tone}` : 'banner info'; };
const req = async (path, opts = {}) => {
  try {
    const r = await fetch(API + path, { credentials: 'same-origin', ...opts });
    const contentType = r.headers.get('content-type') || '';
    const isJson = contentType.includes('application/json');
    const data = isJson ? await r.json().catch(() => ({})) : {};
    const rawText = isJson ? '' : await r.text().catch(() => '');

    if (!r.ok) {
      const detail = data.detail || rawText.trim() || `${r.status} ${r.statusText}` || 'Request failed.';
      const err = new Error(detail);
      err.status = r.status;
      err.detail = detail;
      throw err;
    }

    return isJson ? data : rawText;
  } catch (err) {
    if (err && typeof err.status === 'number') throw err;
    throw new Error(err && err.message ? err.message : 'Network request failed.');
  }
};

const emailVerificationRequired = () => Boolean(config && config.security && config.security.email_verification_required);
const accessPromptMessage = () => emailVerificationRequired()
  ? 'Verify your email before using Spreview.'
  : 'Sign up with a valid email before using Spreview.';
const accessTabPromptMessage = () => emailVerificationRequired()
  ? 'Verify your email in the Access tab before searching or previewing videos.'
  : 'Use the Access tab to sign up with a valid email before searching or previewing videos.';

async function activateTab(tabName) {
  setActiveTab(tabName);
  if (tabName === 'library' && !libraryLoaded) {
    await loadLibrary();
  }
  if (tabName === 'dmca' && !dmcaLoaded) {
    await loadDmca();
  }
}

function setActiveTab(tabName) {
  const btn = document.querySelector(`.tab[data-tab="${tabName}"]`);
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === tabName));
}

async function showAccessRequired(message) {
  await activateTab('access');
  banner('access-banner', message || accessPromptMessage(), 'error');
}

function isProtectedAccessError(err) {
  return Boolean(err && (err.status === 401 || err.status === 403));
}

document.querySelectorAll('.tab').forEach((btn) => btn.addEventListener('click', async () => {
  setActiveTab(btn.dataset.tab);
  if (btn.dataset.tab === 'library' && !libraryLoaded) {
    try {
      await loadLibrary();
    } catch (err) {
      if (isProtectedAccessError(err)) {
        await showAccessRequired(err.message);
        return;
      }
      banner('search-banner', err.message, 'error');
    }
  }
  if (btn.dataset.tab === 'dmca' && !dmcaLoaded) {
    await loadDmca();
  }
}));

function syncConfig(data) {
  config = data;
  const accessState = data.access || {
    authenticated: false,
    service_access: false,
    reason: accessPromptMessage(),
    user: null,
  };
  $('cap-chip').textContent = `${data.active_user_count} / ${data.max_active_users} active users`;
  $('cap-detail').textContent = `${data.active_user_count} / ${data.max_active_users} active users`;
  $('cap-state').textContent = data.registration_open ? 'Open' : 'Waitlist only';
  $('public-message').textContent = data.disclaimers.public_message;
  $('player-disclaimer').textContent = data.disclaimers.player;
  $('registration-disclaimer').textContent = data.disclaimers.registration;
  $('footer-disclaimer').textContent = data.disclaimers.footer;
  $('active-users').textContent = data.active_user_count;
  $('max-users').textContent = data.max_active_users;
  $('open-state').textContent = data.registration_open ? 'Open' : 'Waitlist';
  $('saved-count').textContent = data.library.saved_videos;
  $('dmca-agent').textContent = data.dmca_contact.agent_name;
  $('dmca-email').textContent = data.dmca_contact.agent_email;
  $('dmca-contact-name').textContent = data.dmca_contact.agent_name;
  $('dmca-contact-email').textContent = data.dmca_contact.agent_email;
  $('dmca-window').textContent = `${data.dmca_contact.response_window_hours} hours`;
  $('modal-player-disclaimer').textContent = data.disclaimers.player;
  applyAccessState(accessState);
}

function applyAccessState(access) {
  const reason = access.reason || accessPromptMessage();
  const user = access.user;
  $('logout-button').style.display = access.service_access ? 'inline-flex' : 'none';

  if (access.service_access && user) {
    banner('access-banner', `Signed in as ${user.email}. Access is active.`);
    return;
  }

  library = [];
  libraryLoaded = false;
  renderLibrary();
  if (!results.length) {
    banner('search-banner', accessTabPromptMessage());
  }

  if (access.authenticated && user) {
    banner('access-banner', `${user.email}: ${reason}`);
    return;
  }

  banner('access-banner', reason);
}

function renderVerificationAction(token) {
  const box = $('verify-banner');
  if (!emailVerificationRequired()) {
    banner('verify-banner');
    return;
  }
  if (!token) {
    box.className = 'banner show info';
    box.textContent = 'Secure mode is enabled. Demo verification links are hidden. If outbound email is not configured yet, ask the admin for a test access link.';
    return;
  }

  box.className = 'banner show info';
  box.textContent = 'Prototype mode: verify the request directly.';
  const btn = document.createElement('button');
  btn.className = 'btn ghost';
  btn.type = 'button';
  btn.id = 'verify-now';
  btn.style.marginTop = '.7rem';
  btn.textContent = 'Complete demo verification';
  box.appendChild(document.createElement('br'));
  box.appendChild(btn);

  btn.addEventListener('click', async () => {
    try {
      const vr = await req(`/register/verify?token=${encodeURIComponent(token)}`);
      banner('verify-banner', vr.message);
      await loadConfig();
      if (vr.status === 'active') {
        banner('search-banner', 'Email verified. Search and preview are now available.');
        await activateTab('search');
      }
      if (adminToken) await refreshAdmin();
    } catch (err) {
      banner('verify-banner', err.message, 'error');
    }
  });
}

function videoCard(v, i, mode) {
  const save = mode === 'search' ? `<button class="btn primary" data-act="save" data-i="${i}">Save to library</button>` : '';
  const remove = mode === 'library' ? `<button class="btn ghost" data-act="remove" data-id="${esc(v.video_id)}">Remove</button>` : '';
  return `<article class="video">
    <img class="thumb" src="${esc(v.thumbnail_url)}" alt="Thumbnail for ${esc(v.title)}" loading="lazy" decoding="async" onerror="this.style.opacity='0.35'">
    <div>
      <div class="title">${esc(v.title)}</div>
      <div class="channel">${esc(v.channel)}</div>
      <div class="meta"><span class="pill">${esc(v.duration)}</span><span class="pill">${mode === 'search' ? 'Search result' : 'Saved in library'}</span></div>
      <div class="desc">${esc(v.description)}</div>
      <div class="actions">
        <button class="btn ghost" data-act="preview" data-i="${i}">Preview on-site</button>
        ${save}
        ${remove}
        <a class="btn secondary" href="${esc(v.video_url)}" target="_blank" rel="noopener noreferrer">Watch on YouTube - Support the Creator</a>
      </div>
    </div>
  </article>`;
}

function renderResults() {
  $('search-results').innerHTML = results.length ? results.map((v, i) => videoCard(v, i, 'search')).join('') : '<div class="empty">No results matched this query. Try another search term or paste a direct YouTube URL.</div>';
}

function renderLibrary() {
  $('library-count').textContent = `${library.length} saved previews`;
  $('saved-count').textContent = library.length;
  $('library-list').innerHTML = library.length ? library.map((v, i) => videoCard(v, i, 'library')).join('') : '<div class="empty">Save videos from search to keep a watchable preview library inside Spreview.</div>';
}

function renderBlocked() {
  $('blocked-videos').innerHTML = blocked.length ? blocked.map((v) => `<div class="item"><div><strong>${esc(v.title)}</strong><span>${esc(v.reason || 'Blocked from preview')}</span></div><span class="tag">${esc(v.source || 'blocklist')}</span></div>`).join('') : '<div class="empty">No blocked videos.</div>';
}

function resetVideoPlayer() {
  const video = $('modal-video');
  video.pause();
  video.removeAttribute('src');
  while (video.firstChild) video.removeChild(video.firstChild);
  video.load();
  currentPlaybackSources = [];
  $('modal-quality-options').innerHTML = '';
  $('modal-quality-wrap').style.display = 'none';
}

function sourceLabel(source, index) {
  const parts = [];
  if (source.quality) parts.push(source.quality);
  if (source.mime_type && source.mime_type.startsWith('video/')) parts.push(source.mime_type.replace('video/', '').toUpperCase());
  return parts.join(' / ') || (index === 0 ? 'Best available' : `Option ${index + 1}`);
}

function setPlaybackSource(index, { resumeAt = 0, autoplay = true } = {}) {
  const video = $('modal-video');
  const source = currentPlaybackSources[index];
  if (!source || !source.url) return;

  video.pause();
  video.removeAttribute('src');
  while (video.firstChild) video.removeChild(video.firstChild);

  const el = document.createElement('source');
  el.src = source.url;
  if (source.mime_type) el.type = source.mime_type;
  video.appendChild(el);

  if (resumeAt > 0) {
    video.addEventListener('loadedmetadata', () => {
      try {
        video.currentTime = resumeAt;
      } catch (_) {}
      if (autoplay) video.play().catch(() => {});
    }, { once: true });
  }

  video.load();
  if (resumeAt <= 0 && autoplay) video.play().catch(() => {});
  renderQualityPicker(currentPlaybackSources, index);
}

function renderQualityPicker(sources, activeIndex = 0) {
  const options = $('modal-quality-options');
  options.innerHTML = '';
  sources.forEach((source, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `quality-option${index === activeIndex ? ' active' : ''}`;
    button.textContent = sourceLabel(source, index);
    button.setAttribute('aria-pressed', index === activeIndex ? 'true' : 'false');
    button.addEventListener('click', () => {
      const video = $('modal-video');
      setPlaybackSource(index, { resumeAt: video.currentTime || 0, autoplay: !video.paused });
    });
    options.appendChild(button);
  });
  $('modal-quality-wrap').style.display = sources.length ? 'flex' : 'none';
}

function applyPlayback(playback) {
  const video = $('modal-video');
  resetVideoPlayer();
  $('modal-title').textContent = playback.title || (currentVideo ? currentVideo.title : 'Preview');
  $('modal-channel').textContent = playback.channel || (currentVideo ? currentVideo.channel : 'Unknown creator');
  $('modal-desc').textContent = playback.description || (currentVideo ? currentVideo.description : 'Open this video on YouTube for the full watch experience and creator support.');
  video.poster = playback.thumbnail_url || (currentVideo ? currentVideo.thumbnail_url : '');

  currentPlaybackSources = (playback.sources || []).length
    ? playback.sources
    : (playback.stream_url ? [{ url: playback.stream_url, mime_type: playback.mime_type || '' }] : []);
  renderQualityPicker(currentPlaybackSources);
  if (playback.preview_available === false || currentPlaybackSources.length === 0) {
    banner('modal-video-banner', playback.preview_error || 'Preview unavailable. Use Watch on YouTube instead.', 'error');
    return;
  }
  banner('modal-video-banner');
  setPlaybackSource(0);

  if (playback.video_url) $('modal-youtube').href = playback.video_url;
}

async function requestVideoFullscreen() {
  const video = $('modal-video');
  if (video.requestFullscreen) {
    await video.requestFullscreen();
    return;
  }
  if (video.webkitEnterFullscreen) {
    video.webkitEnterFullscreen();
    return;
  }
  if (video.webkitRequestFullscreen) {
    video.webkitRequestFullscreen();
  }
}

async function openVideo(v) {
  currentVideo = v;
  const playbackToken = `${v.video_id}:${Date.now()}`;
  currentPlaybackToken = playbackToken;
  $('modal-title').textContent = v.title;
  $('modal-channel').textContent = v.channel;
  $('modal-player-disclaimer').textContent = config ? config.disclaimers.player : 'This is a preview. Support the creator by watching the full video on YouTube.';
  $('modal-desc').textContent = v.description || 'Open this video on YouTube for the full watch experience and creator support.';
  $('modal-youtube').href = v.video_url;
  $('modal-video').poster = v.thumbnail_url || '';
  banner('modal-video-banner', 'Resolving preview stream...');
  $('modal').classList.add('open');
  $('modal').setAttribute('aria-hidden', 'false');

  try {
    const playback = await req(v.playback_url || `/playback/${encodeURIComponent(v.video_id)}`);
    if (currentPlaybackToken !== playbackToken) return;
    applyPlayback(playback);
  } catch (err) {
    if (currentPlaybackToken !== playbackToken) return;
    resetVideoPlayer();
    if (isProtectedAccessError(err)) {
      closeVideo();
      await showAccessRequired(err.message);
      return;
    }
    banner('modal-video-banner', err.message || 'Preview unavailable. Use Watch on YouTube instead.', 'error');
  }
}

function closeVideo() {
  currentPlaybackToken = '';
  banner('modal-video-banner');
  resetVideoPlayer();
  $('modal').classList.remove('open');
  $('modal').setAttribute('aria-hidden', 'true');
}

async function loadConfig() { syncConfig(await req('/config')); }
async function loadLibrary() { library = await req('/library'); libraryLoaded = true; renderLibrary(); }
async function loadDmca() {
  const d = await req('/dmca');
  blocked = d.blocked_videos || [];
  dmcaLoaded = true;
  renderBlocked();
  $('dmca-agent').textContent = d.contact.agent_name;
  $('dmca-email').textContent = d.contact.agent_email;
  $('dmca-contact-name').textContent = d.contact.agent_name;
  $('dmca-contact-email').textContent = d.contact.agent_email;
  $('dmca-window').textContent = `${d.contact.response_window_hours} hours`;
}

$('search-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  banner('search-banner');
  const q = $('search-input').value.trim();
  if (!q) return banner('search-banner', 'Enter a search term or paste a YouTube URL.', 'error');
  banner('search-banner', 'Searching for up to 10 videos...');
  try {
    results = await req(`/search?q=${encodeURIComponent(q)}`);
    renderResults();
    banner('search-banner', `Loaded ${results.length} result${results.length === 1 ? '' : 's'}.`);
  } catch (err) {
    results = [];
    renderResults();
    if (isProtectedAccessError(err)) {
      await showAccessRequired(err.message);
      return;
    }
    banner('search-banner', err.message, 'error');
  }
});

async function saveVideo(v) {
  try {
    const r = await req('/library', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(v) });
    await Promise.all([loadLibrary(), loadConfig()]);
    banner('search-banner', r.status === 'exists' ? 'This video is already in the library.' : 'Saved to the library. It remains watchable inside Spreview.');
    await activateTab('library');
  } catch (err) {
    if (isProtectedAccessError(err)) {
      await showAccessRequired(err.message);
      return;
    }
    banner('search-banner', err.message, 'error');
  }
}

$('search-results').addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const v = results[Number(el.dataset.i)];
  if (!v) return;
  if (el.dataset.act === 'preview') await openVideo(v);
  if (el.dataset.act === 'save') await saveVideo(v);
});

$('library-list').addEventListener('click', async (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  if (el.dataset.act === 'remove') {
    try {
      await req(`/library/${encodeURIComponent(el.dataset.id)}`, { method: 'DELETE' });
      await Promise.all([loadLibrary(), loadConfig()]);
    } catch (err) {
      if (isProtectedAccessError(err)) {
        await showAccessRequired(err.message);
        return;
      }
      banner('search-banner', err.message, 'error');
    }
    return;
  }
  const v = library[Number(el.dataset.i)];
  if (v && el.dataset.act === 'preview') await openVideo(v);
});

$('close-modal').addEventListener('click', closeVideo);
$('modal').addEventListener('click', (e) => { if (e.target === $('modal')) closeVideo(); });
$('modal-save').addEventListener('click', async () => { if (currentVideo) await saveVideo(currentVideo); });
$('modal-fullscreen').addEventListener('click', async () => { await requestVideoFullscreen(); });
$('modal-video').addEventListener('error', () => {
  banner('modal-video-banner', 'Preview stream failed to load. Use Watch on YouTube instead.', 'error');
});
window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('modal').classList.contains('open')) closeVideo(); });

$('access-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  banner('access-banner');
  banner('verify-banner');
  const email = $('access-email').value.trim();
  if (!email) return banner('access-banner', 'Enter an email address to request access.', 'error');
  try {
    const r = await req('/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
    await loadConfig();
    banner('access-banner', r.message || 'Request received.');
    renderVerificationAction(r.verification_token);
    if (r.status === 'active') {
      banner('search-banner', 'Signup complete. Search and preview are now available.');
      await activateTab('search');
    }
  } catch (err) {
    banner('access-banner', err.message, 'error');
  }
});

$('logout-button').addEventListener('click', async () => {
  try {
    await req('/session/logout', { method: 'POST' });
    results = [];
    library = [];
    libraryLoaded = false;
    currentVideo = null;
    renderResults();
    renderLibrary();
    banner('verify-banner');
    await loadConfig();
    banner('access-banner', emailVerificationRequired() ? 'Signed out. Verify your email again to restore service access.' : 'Signed out. Enter your email again to restore service access.');
    await activateTab('access');
  } catch (err) {
    banner('access-banner', err.message, 'error');
  }
});

$('dmca-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  banner('dmca-banner');
  const payload = {
    reporter_name: $('dmca-name').value.trim(),
    reporter_email: $('dmca-reporter-email').value.trim(),
    video_url: $('dmca-video-url').value.trim(),
    work_description: $('dmca-work').value.trim(),
    statement: $('dmca-statement').value.trim()
  };
  try {
    const r = await req('/dmca/notices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    banner('dmca-banner', r.message);
    $('dmca-form').reset();
    await Promise.all([loadDmca(), loadLibrary(), loadConfig()]);
    if (adminToken) await refreshAdmin();
  } catch (err) {
    banner('dmca-banner', err.message, 'error');
  }
});

const adminReq = (path, opts = {}) => req(path, { ...opts, headers: { 'X-Admin-Token': adminToken, ...(opts.headers || {}) } });

function adminStats(s) {
  $('admin-stats').innerHTML = `<div class="mini-card"><span>Active users</span><strong>${s.active_user_count}</strong></div><div class="mini-card"><span>Waitlist</span><strong>${s.waitlisted_user_count}</strong></div><div class="mini-card"><span>Blocked emails</span><strong>${s.blocked_email_count}</strong></div><div class="mini-card"><span>Blocked videos</span><strong>${s.dmca_blocked_video_count}</strong></div>`;
}

function adminUsers(users) {
  $('admin-users').innerHTML = users.length ? users.map((u) => `<div class="item"><div><strong>${esc(u.email)}</strong><span>Status: ${esc(u.status)} | Verified: ${u.email_verified ? 'yes' : 'no'}</span></div><div class="mini"><span class="pill">${esc(u.status)}</span>${u.status === 'active' && u.email_verified ? `<button class="btn ghost" data-link="${esc(u.email)}">Test link</button>` : ''}<button class="btn ghost" data-del="${esc(u.email)}">Delete</button></div></div>`).join('') : '<div class="empty">No users found.</div>';
}

function adminBlocked(emails) {
  $('admin-blocked').innerHTML = emails.length ? emails.map((email) => `<div class="item"><div><strong>${esc(email)}</strong><span>Blocked from registration and approval.</span></div><button class="btn ghost" data-unblock="${esc(email)}">Unblock</button></div>`).join('') : '<div class="empty">No blocked emails.</div>';
}

function adminNotices(notices) {
  $('admin-notices').innerHTML = notices.length ? notices.map((n) => `<div class="item"><div><strong>${esc(n.reporter_name)}</strong><span>${esc(n.video_url)}</span></div><span class="tag">${esc(n.id)}</span></div>`).join('') : '<div class="empty">No notices yet.</div>';
}

function adminCookieSourceLabel(source) {
  if (source === 'env_file') return 'Environment cookie file';
  if (source === 'env_text') return 'Environment cookie text';
  if (source === 'runtime_file') return 'Admin-managed cookie file';
  if (source === 'browser') return 'Browser cookie profile';
  return 'Not configured';
}

function adminCookieStatus(status) {
  const source = status && status.active_source ? status.active_source : 'none';
  $('admin-cookie-source').value = adminCookieSourceLabel(source);
  if (status && status.active_cookie_file) {
    $('admin-cookie-status').textContent = `Active cookie file: ${status.active_cookie_file}`;
  } else if (status && status.active_browser) {
    $('admin-cookie-status').textContent = `Active browser profile: ${status.active_browser}`;
  } else {
    $('admin-cookie-status').textContent = 'No active cookie source. Preview lookups will likely fail on hosted server IPs.';
  }

  $('admin-cookie-runtime').textContent = status && status.runtime_cookie_present
    ? `Managed runtime file is present at ${status.runtime_cookie_file}.`
    : 'No managed runtime cookie file stored.';
}

function setAdminAccessLink(link = '', email = '') {
  latestAdminAccessLink = link;
  $('admin-access-link-output').value = link;
  $('admin-copy-access-link').disabled = !link;
  banner('admin-access-link-banner', link ? `Share this link with ${email}. It creates a browser session for that active user.` : '');
}

async function createAdminAccessLink(email) {
  const r = await adminReq('/admin/users/access-link', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
  const absoluteLink = `${window.location.origin}${r.access_url}`;
  setAdminAccessLink(absoluteLink, r.email);
  banner('admin-banner', `Test access link created for ${r.email}.`);
  return absoluteLink;
}

async function refreshAdmin() {
  const [summary, users, blockedEmails, notices, cookieStatus] = await Promise.all([
    adminReq('/admin/summary'),
    adminReq('/admin/users'),
    adminReq('/admin/blocked-emails'),
    adminReq('/admin/dmca/notices'),
    adminReq('/admin/ytdlp/cookies')
  ]);
  $('admin-panel').style.display = 'block';
  if (!latestAdminAccessLink) setAdminAccessLink();
  adminStats(summary);
  adminUsers(users);
  adminBlocked(blockedEmails);
  adminNotices(notices);
  adminCookieStatus(cookieStatus);
}

$('admin-connect').addEventListener('submit', async (e) => {
  e.preventDefault();
  banner('admin-banner');
  adminToken = $('admin-token').value.trim();
  if (!adminToken) return banner('admin-banner', 'Enter the admin password first.', 'error');
  try {
    await refreshAdmin();
    banner('admin-banner', 'Admin password accepted.');
  } catch (err) {
    $('admin-panel').style.display = 'none';
    banner('admin-banner', err.message, 'error');
  }
});

async function adminPost(path, payload, msg) {
  try {
    await adminReq(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    banner('admin-banner', msg);
    await Promise.all([refreshAdmin(), loadConfig(), loadDmca(), loadLibrary()]);
  } catch (err) {
    banner('admin-banner', err.message, 'error');
  }
}

$('admin-add-form').addEventListener('submit', async (e) => { e.preventDefault(); await adminPost('/admin/users', { email: $('admin-add-email').value.trim(), status: $('admin-add-status').value }, 'User saved.'); e.target.reset(); });
$('admin-approve-form').addEventListener('submit', async (e) => { e.preventDefault(); await adminPost('/admin/users/approve', { email: $('admin-approve-email').value.trim() }, 'User approved.'); e.target.reset(); });
$('admin-access-link-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = $('admin-access-link-email').value.trim();
  if (!email) return banner('admin-access-link-banner', 'Enter an active user email first.', 'error');
  try {
    await createAdminAccessLink(email);
    e.target.reset();
  } catch (err) {
    banner('admin-access-link-banner', err.message, 'error');
  }
});
$('admin-cookie-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  banner('admin-cookie-banner');
  const cookieText = $('admin-cookie-text').value.trim();
  if (!cookieText) return banner('admin-cookie-banner', 'Paste Netscape cookies.txt content before saving.', 'error');
  try {
    const r = await adminReq('/admin/ytdlp/cookies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cookie_text: cookieText })
    });
    adminCookieStatus(r);
    $('admin-cookie-text').value = '';
    banner('admin-cookie-banner', `Cookies saved. Cleared ${r.cleared_cache_entries || 0} cached preview entries.`);
  } catch (err) {
    banner('admin-cookie-banner', err.message, 'error');
  }
});
$('admin-cookie-clear').addEventListener('click', async () => {
  banner('admin-cookie-banner');
  try {
    const r = await adminReq('/admin/ytdlp/cookies', { method: 'DELETE' });
    adminCookieStatus(r);
    banner('admin-cookie-banner', `Managed cookies cleared. Cleared ${r.cleared_cache_entries || 0} cached preview entries.`);
  } catch (err) {
    banner('admin-cookie-banner', err.message, 'error');
  }
});
$('admin-block-email-form').addEventListener('submit', async (e) => { e.preventDefault(); await adminPost('/admin/users/block', { email: $('admin-block-email').value.trim() }, 'Email blocked.'); e.target.reset(); });
$('admin-block-video-form').addEventListener('submit', async (e) => { e.preventDefault(); await adminPost('/admin/dmca/blocked-videos', { video_url: $('admin-video-url').value.trim(), title: $('admin-video-title').value.trim(), reason: 'Blocked by admin' }, 'Video blocked from preview.'); e.target.reset(); });
$('admin-copy-access-link').addEventListener('click', async () => {
  if (!latestAdminAccessLink) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(latestAdminAccessLink);
      banner('admin-access-link-banner', 'Test access link copied to clipboard.');
      return;
    }
    $('admin-access-link-output').focus();
    $('admin-access-link-output').select();
    banner('admin-access-link-banner', 'Copy the selected test access link manually.');
  } catch (err) {
    banner('admin-access-link-banner', 'Copy failed. Select the link field and copy it manually.', 'error');
  }
});

$('admin-users').addEventListener('click', async (e) => {
  const linkEl = e.target.closest('[data-link]');
  if (linkEl) {
    try {
      await createAdminAccessLink(linkEl.dataset.link);
    } catch (err) {
      banner('admin-banner', err.message, 'error');
    }
    return;
  }
  const el = e.target.closest('[data-del]');
  if (!el) return;
  try {
    await adminReq(`/admin/users/${encodeURIComponent(el.dataset.del)}`, { method: 'DELETE' });
    banner('admin-banner', 'User deleted.');
    await Promise.all([refreshAdmin(), loadConfig()]);
  } catch (err) {
    banner('admin-banner', err.message, 'error');
  }
});

$('admin-blocked').addEventListener('click', async (e) => {
  const el = e.target.closest('[data-unblock]');
  if (!el) return;
  try {
    await adminReq(`/admin/blocked-emails/${encodeURIComponent(el.dataset.unblock)}`, { method: 'DELETE' });
    banner('admin-banner', 'Email unblocked.');
    await Promise.all([refreshAdmin(), loadConfig()]);
  } catch (err) {
    banner('admin-banner', err.message, 'error');
  }
});

loadConfig().catch((err) => banner('search-banner', err.message, 'error'));

