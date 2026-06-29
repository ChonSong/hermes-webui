// ── Tiling chat interface ──────────────────────────────────────────────────
// Managed by window.TILES. Each tile is a self-contained chat pane. Tiles
// are arranged in a CSS Grid that replaces #msgInner when tiling mode is on.
// Maximize makes one tile fill the grid; unmaximize returns to grid view.

(function(){
  const T = {
    tiles: [],          // {id, sid, session, messages, busy, activeStreamId, maximized, el}
    activeTileId: null,
    maxTiles: 6,
    nextId: 1,
    gridEl: null,
    _tilingMode: false,
    _msgInner: null,     // reference to original #msgInner element
    _msgInnerParent: null,
  };

  function tileById(id) { return T.tiles.find(t => t.id === id) || null; }
  function tileBySid(sid) { return T.tiles.find(t => t.sid === sid) || null; }
  function activeTile() { return tileById(T.activeTileId); }

  // ── DOM helpers ──────────────────────────────────────────────────────────

  function _createTileEl(tile) {
    const el = document.createElement('div');
    el.className = 'tile';
    el.dataset.tileId = String(tile.id);
    el.innerHTML =
      '<div class="tile-header">' +
        '<div class="tile-header-left">' +
          '<span class="tile-dot" hidden></span>' +
          '<span class="tile-title"></span>' +
        '</div>' +
        '<div class="tile-header-actions">' +
          '<button class="tile-btn tile-maximize-btn" data-tooltip="Maximize" aria-label="Maximize">' +
            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>' +
          '</button>' +
          '<button class="tile-btn tile-unmaximize-btn" data-tooltip="Restore" aria-label="Restore" hidden>' +
            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>' +
          '</button>' +
          '<button class="tile-btn tile-close-btn" data-tooltip="Close" aria-label="Close">' +
            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
          '</button>' +
        '</div>' +
      '</div>' +
      '<div class="tile-body">' +
        '<div class="tile-messages-shell">' +
          '<div class="tile-empty-state">What can I help with?</div>' +
          '<div class="tile-messages" hidden></div>' +
        '</div>' +
      '</div>' +
      '<div class="tile-composer">' +
        '<div class="tile-composer-status"></div>' +
        '<div class="tile-composer-row">' +
          '<textarea class="tile-input" placeholder="Message …" rows="1"></textarea>' +
          '<button class="tile-send-btn" aria-label="Send">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>' +
          '</button>' +
        '</div>' +
      '</div>';

    // Wire controls
    el.querySelector('.tile-maximize-btn').onclick = () => maximizeTile(tile.id);
    el.querySelector('.tile-unmaximize-btn').onclick = () => unmaximizeTile(tile.id);
    el.querySelector('.tile-close-btn').onclick = () => closeTile(tile.id);
    el.querySelector('.tile-send-btn').onclick = () => _tileSend(tile.id);
    el.querySelector('.tile-input').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _tileSend(tile.id); }
    });
    el.querySelector('.tile-input').addEventListener('input', function() {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
    // Click body or header to focus
    el.querySelector('.tile-body').onclick = () => focusTile(tile.id);
    el.querySelector('.tile-header').addEventListener('click', e => {
      if (!e.target.closest('.tile-btn')) focusTile(tile.id);
    });

    return el;
  }

  // ── Render ───────────────────────────────────────────────────────────────

  function _renderTileMessages(tile) {
    const el = tile.el || T.gridEl.querySelector(`.tile[data-tile-id="${tile.id}"]`);
    if (!el) return;
    const shell = el.querySelector('.tile-messages-shell');
    if (!shell) return;
    const empty = shell.querySelector('.tile-empty-state');
    const container = shell.querySelector('.tile-messages');
    if (!container) return;

    const msgs = tile.messages || [];
    if (!msgs.length) {
      if (empty) empty.hidden = false;
      container.innerHTML = '';
      container.hidden = true;
      return;
    }
    if (empty) empty.hidden = true;
    container.hidden = false;

    // Use _createMessageElement for consistent look with main chat
    const createMsg = window._createMessageElement;
    container.innerHTML = '';
    for (const msg of msgs) {
      if (!msg || !msg.role || msg.role === 'tool') continue;
      if (typeof createMsg === 'function') {
        const msgEl = createMsg(msg);
        if (msgEl) { msgEl.classList.add('tile-msg'); container.appendChild(msgEl); }
      } else {
        const d = document.createElement('div');
        d.className = 'tile-msg tile-msg--' + (msg.role === 'user' ? 'user' : 'assistant');
        d.textContent = typeof msg.content === 'string' ? msg.content.slice(0, 500) : '(content)';
        container.appendChild(d);
      }
    }

    // Auto-scroll to bottom
    shell.scrollTop = shell.scrollHeight;
  }

  function _updateTileHeader(tile) {
    const el = tile.el || T.gridEl.querySelector(`.tile[data-tile-id="${tile.id}"]`);
    if (!el) return;
    const title = tile.session ? (tile.session.title || 'New Chat') : 'No session';
    el.querySelector('.tile-title').textContent = title;
    el.querySelector('.tile-dot').hidden = !tile.busy;
  }

  // ── Tile lifecycle ───────────────────────────────────────────────────────

  function openTileForSession(sid, sessionData) {
    if (!sid) return;
    const existing = tileBySid(sid);
    if (existing) { focusTile(existing.id); return; }

    if (T.tiles.length >= T.maxTiles) {
      const evict = T.tiles.find(t => !t.busy);
      if (evict) closeTile(evict.id);
      else return;
    }

    const id = T.nextId++;
    const tile = {
      id, sid,
      session: sessionData || null,
      messages: (sessionData && sessionData.messages) || [],
      busy: false,
      activeStreamId: null,
      maximized: false,
      el: null,
    };
    T.tiles.push(tile);

    const tileEl = _createTileEl(tile);
    tile.el = tileEl;
    T.gridEl.appendChild(tileEl);

    _renderTileMessages(tile);
    _updateTileHeader(tile);
    _updateSidebarBadge(sid, 1);
    _refreshGrid();
    focusTile(tile.id);
  }

  function focusTile(id) {
    const tile = tileById(id);
    if (!tile) return;
    T.activeTileId = id;

    for (const t of T.tiles) {
      if (t.el) t.el.classList.toggle('tile--focused', t.id === id);
    }

    // Sync global S state so existing code reads from the active tile
    if (tile.session) {
      if (typeof S !== 'undefined') {
        S.session = tile.session;
        S.messages = tile.messages;
        S.busy = tile.busy;
        S.activeStreamId = tile.activeStreamId;
      }
    }
    if (typeof syncTopbar === 'function') syncTopbar();
    if (typeof syncModelChip === 'function') syncModelChip();

    const input = tile.el.querySelector('.tile-input');
    if (input) setTimeout(() => input.focus(), 50);
  }

  function maximizeTile(id) {
    const tile = tileById(id);
    if (!tile) return;
    // If another tile is maximized, unmaximize it first
    const curMax = T.tiles.find(t => t.maximized);
    if (curMax && curMax.id !== id) {
      curMax.maximized = false;
      if (curMax.el) {
        curMax.el.classList.remove('tile--maximized');
        curMax.el.querySelector('.tile-maximize-btn').hidden = false;
        curMax.el.querySelector('.tile-unmaximize-btn').hidden = true;
      }
    }
    tile.maximized = true;
    if (tile.el) {
      tile.el.classList.add('tile--maximized');
      tile.el.querySelector('.tile-maximize-btn').hidden = true;
      tile.el.querySelector('.tile-unmaximize-btn').hidden = false;
    }
    // Hide non-maximized tiles
    for (const t of T.tiles) {
      if (t.el) t.el.classList.toggle('tile--hidden', !t.maximized);
    }
  }

  function unmaximizeTile(id) {
    const tile = tileById(id);
    if (!tile) return;
    tile.maximized = false;
    if (tile.el) {
      tile.el.classList.remove('tile--maximized');
      tile.el.querySelector('.tile-maximize-btn').hidden = false;
      tile.el.querySelector('.tile-unmaximize-btn').hidden = true;
    }
    // Show all tiles (none hidden by maximize)
    for (const t of T.tiles) {
      if (t.el) t.el.classList.remove('tile--hidden');
    }
  }

  function closeTile(id) {
    const idx = T.tiles.findIndex(t => t.id === id);
    if (idx < 0) return;
    const tile = T.tiles[idx];

    // Cancel stream
    if (tile.busy && tile.activeStreamId && typeof cancelSessionStream === 'function') {
      cancelSessionStream(tile.session);
    }
    // Cleanup INFLIGHT
    if (tile.sid && typeof INFLIGHT !== 'undefined' && INFLIGHT[tile.sid]) {
      delete INFLIGHT[tile.sid];
      if (typeof clearInflightState === 'function') clearInflightState(tile.sid);
    }
    // Remove DOM
    if (tile.el) tile.el.remove();
    T.tiles.splice(idx, 1);

    _updateSidebarBadge(tile.sid, -1);

    if (T.activeTileId === id) {
      T.activeTileId = null;
      const next = T.tiles[0]; // pick first remaining
      if (next) focusTile(next.id);
      else {
        if (typeof S !== 'undefined') { S.session = null; S.messages = []; S.busy = false; S.activeStreamId = null; }
        if (typeof syncTopbar === 'function') syncTopbar();
      }
    }
    _refreshGrid();
  }

  // ── Grid layout ──────────────────────────────────────────────────────────

  function _refreshGrid() {
    const count = T.tiles.length;
    T.gridEl.classList.toggle('tile-grid--empty', count === 0);
    if (count <= 1) {
      T.gridEl.style.gridTemplateColumns = '1fr';
      T.gridEl.style.gridTemplateRows = '1fr';
    } else if (count === 2) {
      T.gridEl.style.gridTemplateColumns = '1fr 1fr';
      T.gridEl.style.gridTemplateRows = '1fr';
    } else if (count === 3) {
      T.gridEl.style.gridTemplateColumns = '1fr 1fr 1fr';
      T.gridEl.style.gridTemplateRows = '1fr';
    } else if (count === 4) {
      T.gridEl.style.gridTemplateColumns = '1fr 1fr';
      T.gridEl.style.gridTemplateRows = '1fr 1fr';
    } else {
      T.gridEl.style.gridTemplateColumns = '1fr 1fr 1fr';
      T.gridEl.style.gridTemplateRows = '1fr 1fr';
    }
  }

  // ── Tile send ────────────────────────────────────────────────────────────

  async function _tileSend(tileId) {
    const tile = tileById(tileId);
    if (!tile) return;
    const input = tile.el.querySelector('.tile-input');
    const text = (input && input.value.trim()) || '';
    if (!text) return;
    if (input) { input.value = ''; input.style.height = 'auto'; }

    // If tile has no session, create one
    if (!tile.session) {
      try {
        const body = { model: window._defaultModel || '', model_provider: null, workspace: null, profile: 'default' };
        const data = await api('/api/session/new', { method: 'POST', body: JSON.stringify(body) });
        tile.session = data.session;
        tile.messages = data.session.messages || [];
        tile.sid = data.session.session_id;
        if (typeof S !== 'undefined') S.session = tile.session;
        if (typeof syncTopbar === 'function') syncTopbar();
        _updateTileHeader(tile);
      } catch(e) {
        if (typeof showToast === 'function') showToast('Failed to create session', 3000, 'error');
        return;
      }
    }

    // Push user message
    tile.messages.push({ role: 'user', content: text });
    if (typeof S !== 'undefined') S.messages = tile.messages;
    _renderTileMessages(tile);
    tile.busy = true;
    _updateTileHeader(tile);

    try {
      const body = {
        session_id: tile.sid,
        message: text,
        model: tile.session.model || window._defaultModel || '',
        model_provider: tile.session.model_provider || null,
        profile: 'default',
      };
      const res = await fetch(new URL('/api/chat', document.baseURI || location.href).href, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      tile.activeStreamId = data.stream_id || null;
      if (tile.session) tile.session.active_stream_id = tile.activeStreamId;
      if (typeof S !== 'undefined') S.activeStreamId = tile.activeStreamId;
      if (typeof INFLIGHT !== 'undefined') {
        INFLIGHT[tile.sid] = { messages: [...tile.messages], uploaded: [], toolCalls: [] };
      }

      // Poll for stream completion — check tile's own state, not global S
      const poll = setInterval(async () => {
        try {
          const status = await fetch(`/api/session?session_id=${encodeURIComponent(tile.sid)}&messages=1&fields=busy,active_stream_id`).then(r => r.json());
          const sess = status && status.session;
          if (!sess || (!sess.busy && !sess.active_stream_id)) {
            clearInterval(poll);
            tile.messages = (sess && sess.messages) || tile.messages;
            tile.busy = false;
            tile.activeStreamId = null;
            _renderTileMessages(tile);
            _updateTileHeader(tile);
          }
        } catch(_) { /* retry on next tick */ }
      }, 1000);
    } catch(e) {
      tile.busy = false;
      _updateTileHeader(tile);
      if (typeof showToast === 'function') showToast('Send failed: ' + (e.message || ''), 3000, 'error');
    }
  }

  // ── Sidebar badge ────────────────────────────────────────────────────────

  const _tileCounts = {};
  function _updateSidebarBadge(sid, delta) {
    if (!sid) return;
    _tileCounts[sid] = (_tileCounts[sid] || 0) + delta;
    const count = _tileCounts[sid];
    const row = document.querySelector(`[data-session-id="${CSS.escape(sid)}"]`);
    if (!row) return;
    let badge = row.querySelector('.session-tile-badge');
    if (count > 0) {
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'session-tile-badge';
        (row.querySelector('.session-row-right') || row.querySelector('.session-meta') || row).appendChild(badge);
      }
      badge.textContent = count > 9 ? '9+' : String(count);
    } else if (badge) {
      badge.remove();
    }
  }

  // ── Tiling mode toggle ───────────────────────────────────────────────────

  function toggleTilingMode() {
    T._tilingMode = !T._tilingMode;
    document.body.classList.toggle('tiling-mode', T._tilingMode);
    try { localStorage.setItem('hermes-tiling-mode', T._tilingMode ? '1' : '0'); } catch(_) {}

    // When tiling on: replace #msgInner with tile grid
    if (T._tilingMode) {
      _showTileGrid();
    } else {
      _hideTileGrid();
    }

    if (typeof showToast === 'function') {
      showToast(T._tilingMode ? 'Tiling mode on — click sessions to open in new tiles' : 'Tiling mode off', 2500);
    }
    const btn = document.getElementById('btnTilingMode');
    if (btn) btn.classList.toggle('active', T._tilingMode);
  }

  function _showTileGrid() {
    T._msgInner = document.getElementById('msgInner');
    if (!T._msgInner) return;
    T._msgInnerParent = T._msgInner.parentNode;
    if (!T._msgInnerParent) return;

    // Hide the original messages and show tile grid in its place
    T._msgInner.style.display = 'none';

    // Create grid if not yet created
    if (!T.gridEl) {
      T.gridEl = document.createElement('div');
      T.gridEl.id = 'tileGrid';
      T.gridEl.className = 'tile-grid tile-grid--empty';
      T._msgInnerParent.appendChild(T.gridEl);
    }
    T.gridEl.style.display = '';

    // Hide the empty state since tile grid is now the content area
    const emptyState = document.getElementById('emptyState');
    if (emptyState) emptyState.style.display = 'none';
  }

  function _hideTileGrid() {
    if (T.gridEl) T.gridEl.style.display = 'none';
    if (T._msgInner) T._msgInner.style.display = '';

    // Restore empty state if there are no messages in the main view
    const emptyState = document.getElementById('emptyState');
    if (emptyState && T._msgInner && !T._msgInner.children.length) {
      emptyState.style.display = '';
    }
  }

  function isTilingMode() { return !!T._tilingMode; }

  // ── Init ─────────────────────────────────────────────────────────────────

  function initTiles() {
    // Create tile grid DOM early but hidden
    const mainChat = document.getElementById('mainChat');
    if (!mainChat) return;

    // Create grid element adjacent to #msgInner, initially hidden
    T.gridEl = document.createElement('div');
    T.gridEl.id = 'tileGrid';
    T.gridEl.className = 'tile-grid tile-grid--empty';
    T.gridEl.style.display = 'none';
    const msgInner = document.getElementById('msgInner');
    if (msgInner && msgInner.parentNode) {
      msgInner.parentNode.appendChild(T.gridEl);
    }

    // Restore preference
    try {
      if (localStorage.getItem('hermes-tiling-mode') === '1') {
        setTimeout(() => { if (!isTilingMode()) toggleTilingMode(); }, 100);
      }
    } catch(_) {}
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initTiles);
  else initTiles();

  // Expose
  window.TILES = T;
  window.openTileForSession = openTileForSession;
  window.focusTile = focusTile;
  window.maximizeTile = maximizeTile;
  window.unmaximizeTile = unmaximizeTile;
  window.closeTile = closeTile;
  window.toggleTilingMode = toggleTilingMode;
  window.isTilingMode = isTilingMode;
})();
