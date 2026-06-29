// ── Tiling chat interface ──────────────────────────────────────────────────
// Managed by window.TILES. When tiling mode is on, #msgInner is moved into
// the focused tile's body so all existing message rendering targets the tile.
// The main #composerWrap sends to the focused tile's session via S.session.
// Maximize makes one tile fill the grid; unmaximize returns to grid view.

(function(){
  const T = {
    tiles: [],          // {id, sid, session, messages, busy, activeStreamId, maximized, el,
                        //  composerText, modelState}  -- saved per tile
    activeTileId: null,
    maxTiles: 6,
    nextId: 1,
    gridEl: null,
    _tilingMode: false,
    _msgInner: null,
    _msgInnerOriginalParent: null,
    _msgInnerOriginalNextSibling: null,
    _busyWatcher: null,
  };

  function tileById(id) { return T.tiles.find(t => t.id === id) || null; }
  function tileBySid(sid) { return T.tiles.find(t => t.sid === sid) || null; }
  function activeTile() { return tileById(T.activeTileId); }

  // ── Composer state helpers ──────────────────────────────────────────────

  function _readComposerState() {
    const msg = document.getElementById('msg');
    const modelSel = document.getElementById('modelSelect');
    return {
      text: msg ? msg.value : '',
      model: modelSel ? modelSel.value : (window._defaultModel || ''),
      modelProvider: (typeof _readActiveModelProvider === 'function') ? _readActiveModelProvider() : null,
    };
  }

  function _writeComposerState(state) {
    if (!state) return;
    const msg = document.getElementById('msg');
    if (msg) msg.value = state.text || '';
    if (typeof triggerMsgh === 'function') triggerMsgh();
    // Model state restore
    if (state.model && document.getElementById('modelSelect')) {
      const sel = document.getElementById('modelSelect');
      if (sel && state.model !== sel.value) {
        sel.value = state.model;
        if (typeof _onModelSelectChange === 'function') _onModelSelectChange();
      }
    }
  }

  function _saveActiveTileComposerState() {
    const tile = activeTile();
    if (!tile) return;
    tile.composerText = _readComposerState().text;
    // Also save current model state
    const modelSel = document.getElementById('modelSelect');
    if (modelSel) tile._model = modelSel.value;
  }

  function _restoreTileComposerState(tile) {
    if (!tile) return;
    const msg = document.getElementById('msg');
    if (msg) msg.value = tile.composerText || '';
    if (typeof triggerMsgh === 'function') triggerMsgh();
    if (tile._model && document.getElementById('modelSelect')) {
      const sel = document.getElementById('modelSelect');
      if (sel && tile._model !== sel.value) {
        sel.value = tile._model;
        if (typeof _onModelSelectChange === 'function') _onModelSelectChange();
      }
    }
  }

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
        '<div class="tile-body-placeholder"></div>' +
      '</div>';

    // Wire controls
    el.querySelector('.tile-maximize-btn').onclick = () => maximizeTile(tile.id);
    el.querySelector('.tile-unmaximize-btn').onclick = () => unmaximizeTile(tile.id);
    el.querySelector('.tile-close-btn').onclick = () => closeTile(tile.id);
    // Click body or header to focus
    el.querySelector('.tile-body').addEventListener('click', () => focusTile(tile.id));
    el.querySelector('.tile-header').addEventListener('click', e => {
      if (!e.target.closest('.tile-btn')) focusTile(tile.id);
    });

    return el;
  }

  // ── Render ───────────────────────────────────────────────────────────────

  function _renderTileMessages(tile) {
    // When a tile is focused and #msgInner is inside it, the existing
    // message rendering pipeline handles display. This function is called
    // when the tile is NOT focused (to show a preview) or after stream
    // completion. Since #msgInner renders messages via the main pipeline,
    // we just need to ensure the tile's S state is synced.
    // The main message area (#msgInner) handles rendering via messages.js.
    // Update the header dot for busy state.
    _updateTileHeader(tile);
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
      composerText: '',
      _model: null,
    };
    T.tiles.push(tile);

    const tileEl = _createTileEl(tile);
    tile.el = tileEl;
    T.gridEl.appendChild(tileEl);

    _updateTileHeader(tile);
    _updateSidebarBadge(sid, 1);
    _refreshGrid();
    focusTile(tile.id);
  }

  function focusTile(id) {
    const tile = tileById(id);
    if (!tile) return;

    // Save current tile state before switching
    _saveActiveTileComposerState();
    if (T.activeTileId && T.activeTileId !== id) {
      const oldTile = activeTile();
      if (oldTile && typeof S !== 'undefined') {
        oldTile.messages = [...(S.messages || [])];
        oldTile.busy = !!S.busy;
        oldTile.activeStreamId = S.activeStreamId || null;
        oldTile.session = S.session;
      }
      // Move #msgInner out of old tile
      if (T._msgInner && oldTile && oldTile.el) {
        const placeholder = oldTile.el.querySelector('.tile-body-placeholder');
        if (placeholder) placeholder.parentNode.insertBefore(T._msgInner, placeholder.nextSibling || placeholder);
        // Actually, move msgInner back to grid (the safe place)
        if (T.gridEl) T.gridEl.appendChild(T._msgInner);
      }
    }

    T.activeTileId = id;

    // Highlight
    for (const t of T.tiles) {
      if (t.el) t.el.classList.toggle('tile--focused', t.id === id);
    }

    // Move #msgInner into this tile's body
    if (T._msgInner && tile.el) {
      const body = tile.el.querySelector('.tile-body');
      if (body) body.appendChild(T._msgInner);
    }

    // Sync global S state
    if (typeof S !== 'undefined') {
      S.session = tile.session;
      S.messages = tile.messages || [];
      S.busy = tile.busy || false;
      S.activeStreamId = tile.activeStreamId || null;
    }

    // Restore this tile's composer state
    _restoreTileComposerState(tile);

    if (typeof syncTopbar === 'function') syncTopbar();
    if (typeof syncModelChip === 'function') syncModelChip();

    // Start busy watcher for this tile
    _startBusyWatcher();
  }

  function maximizeTile(id) {
    const tile = tileById(id);
    if (!tile) return;
    // Toggle: if already maximized, unmaximize
    if (tile.maximized) { unmaximizeTile(id); return; }
    // Unmaximize any currently maximized tile
    const curMax = T.tiles.find(t => t.maximized);
    if (curMax) {
      curMax.maximized = false;
      if (curMax.el) {
        curMax.el.classList.remove('tile--maximized');
        curMax.el.querySelector('.tile-maximize-btn').hidden = false;
        curMax.el.querySelector('.tile-unmaximize-btn').hidden = true;
      }
    }
    // Maximize this tile
    tile.maximized = true;
    if (tile.el) {
      tile.el.classList.add('tile--maximized');
      tile.el.querySelector('.tile-maximize-btn').hidden = true;
      tile.el.querySelector('.tile-unmaximize-btn').hidden = false;
    }
    // Hide all non-maximized tiles (exactly one tile is maximized)
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
    if (tile.sid && typeof INFLIGHT !== 'undefined' && INFLIGHT[tile.sid]) {
      delete INFLIGHT[tile.sid];
      if (typeof clearInflightState === 'function') clearInflightState(tile.sid);
    }

    // If this is the focused tile, move #msgInner out first
    if (T.activeTileId === id && T._msgInner) {
      if (T.gridEl) T.gridEl.appendChild(T._msgInner);
    }

    // Remove DOM
    if (tile.el) tile.el.remove();
    T.tiles.splice(idx, 1);

    // If the closed tile was maximized, un-hide remaining tiles
    if (tile.maximized) {
      for (const t of T.tiles) {
        t.maximized = false;
        if (t.el) {
          t.el.classList.remove('tile--hidden', 'tile--maximized');
          t.el.querySelector('.tile-maximize-btn').hidden = false;
          t.el.querySelector('.tile-unmaximize-btn').hidden = true;
        }
      }
    }

    _updateSidebarBadge(tile.sid, -1);

    if (T.activeTileId === id) {
      T.activeTileId = null;
      const next = T.tiles[0];
      if (next) focusTile(next.id);
      else {
        if (typeof S !== 'undefined') { S.session = null; S.messages = []; S.busy = false; S.activeStreamId = null; }
        if (typeof syncTopbar === 'function') syncTopbar();
        _stopBusyWatcher();
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

  // ── Busy watcher ────────────────────────────────────────────────────────

  function _startBusyWatcher() {
    _stopBusyWatcher();
    T._busyWatcher = setInterval(() => {
      const tile = activeTile();
      if (!tile || T.activeTileId === null) { _stopBusyWatcher(); return; }
      if (typeof S === 'undefined') return;

      // Sync S.messages back to tile
      if (S.messages && S.messages.length > 0) {
        tile.messages = [...S.messages];
      }
      tile.busy = !!S.busy;
      tile.activeStreamId = S.activeStreamId || null;

      // When stream completes (was busy, now not), sync header
      if (!S.busy && tile.session) {
        tile.session = S.session;
      }

      _updateTileHeader(tile);
    }, 500);
  }

  function _stopBusyWatcher() {
    if (T._busyWatcher) { clearInterval(T._busyWatcher); T._busyWatcher = null; }
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
    T._msgInnerOriginalParent = T._msgInner.parentNode;
    T._msgInnerOriginalNextSibling = T._msgInner.nextSibling;

    // Show the tile grid
    T.gridEl.style.display = '';

    // Move #msgInner to the grid (will be placed into focused tile on focusTile)
    T.gridEl.appendChild(T._msgInner);
  }

  function _hideTileGrid() {
    _stopBusyWatcher();

    // Move #msgInner back to its original position
    if (T._msgInner && T._msgInnerOriginalParent) {
      if (T._msgInnerOriginalNextSibling && T._msgInnerOriginalNextSibling.parentNode === T._msgInnerOriginalParent) {
        T._msgInnerOriginalParent.insertBefore(T._msgInner, T._msgInnerOriginalNextSibling);
      } else {
        T._msgInnerOriginalParent.appendChild(T._msgInner);
      }
    }

    // Hide grid
    T.gridEl.style.display = 'none';

    // Reset S to null/empty
    if (typeof S !== 'undefined') {
      S.session = null;
      S.messages = [];
      S.busy = false;
      S.activeStreamId = null;
    }
    if (typeof syncTopbar === 'function') syncTopbar();
  }

  function isTilingMode() { return !!T._tilingMode; }

  // ── Init ─────────────────────────────────────────────────────────────────

  function initTiles() {
    const mainChat = document.getElementById('mainChat');
    if (!mainChat) return;

    T.gridEl = document.createElement('div');
    T.gridEl.id = 'tileGrid';
    T.gridEl.className = 'tile-grid tile-grid--empty';
    T.gridEl.style.display = 'none';

    // Place grid after #messages (sibling of #msgInner)
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
