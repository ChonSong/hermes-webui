// ── Tiling chat interface ──────────────────────────────────────────────────
// Each tile owns a .tile-msg-inner container. When a tile is focused,
// its container gets id="msgInner" so all existing rendering targets it.
// Content stays where it was rendered — no snapshotting needed.
// Non-focused tiles keep their DOM content; just the id moves.

(function(){
  const T = {
    tiles: [],
    activeTileId: null,
    maxTiles: 6,
    nextId: 1,
    gridEl: null,
    _tilingMode: false,
    _busyWatcher: null,
  };

  function tileById(id) { return T.tiles.find(t => t.id === id) || null; }
  function tileBySid(sid) { return T.tiles.find(t => t.sid === sid) || null; }
  function activeTile() { return tileById(T.activeTileId); }

  // ── Composer state helpers ──────────────────────────────────────────────

  function _saveComposerTo(tile) {
    if (!tile) return;
    const msg = document.getElementById('msg');
    if (msg) tile._composerText = msg.value;
    const modelSel = document.getElementById('modelSelect');
    if (modelSel) tile._modelVal = modelSel.value;
  }

  function _restoreComposerFrom(tile) {
    if (!tile) return;
    const msg = document.getElementById('msg');
    if (msg) msg.value = tile._composerText || '';
    if (typeof triggerMsgh === 'function') triggerMsgh();
    const modelSel = document.getElementById('modelSelect');
    if (modelSel && tile._modelVal && tile._modelVal !== modelSel.value) {
      modelSel.value = tile._modelVal;
      if (typeof _onModelSelectChange === 'function') _onModelSelectChange();
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
        '<div class="tile-msg-inner messages-inner"></div>' +
      '</div>';

    el.querySelector('.tile-maximize-btn').onclick = () => maximizeTile(tile.id);
    el.querySelector('.tile-unmaximize-btn').onclick = () => unmaximizeTile(tile.id);
    el.querySelector('.tile-close-btn').onclick = () => closeTile(tile.id);
    el.querySelector('.tile-body').addEventListener('click', () => focusTile(tile.id));
    el.querySelector('.tile-header').addEventListener('click', e => {
      if (!e.target.closest('.tile-btn')) focusTile(tile.id);
    });

    return el;
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
    const msgs = (sessionData && sessionData.messages) || [];
    const tile = {
      id, sid,
      session: sessionData || null,
      messages: msgs,
      busy: false,
      activeStreamId: null,
      maximized: false,
      el: null,
      _composerText: '',
      _modelVal: null,
    };
    T.tiles.push(tile);

    const tileEl = _createTileEl(tile);
    tile.el = tileEl;
    T.gridEl.appendChild(tileEl);

    // Reset stale maximize/hidden state
    for (const t of T.tiles) {
      t.maximized = false;
      if (t.el) {
        t.el.classList.remove('tile--hidden', 'tile--maximized');
        t.el.querySelector('.tile-maximize-btn').hidden = false;
        t.el.querySelector('.tile-unmaximize-btn').hidden = true;
      }
    }

    _updateTileHeader(tile);
    _updateSidebarBadge(sid, 1);
    _refreshGrid();
    focusTile(tile.id);

    // If messages came with session data OR loaded async, render them
    if (msgs.length > 0) {
      _renderMessagesToTile(tile);
    } else if (sid) {
      // Load messages async
      (async () => {
        try {
          const full = await api(`/api/session?session_id=${encodeURIComponent(sid)}&resolve_model=0`);
          if (full && full.session && full.session.messages) {
            tile.messages = full.session.messages;
            tile.session = full.session;
            if (T.activeTileId === id) {
              if (typeof S !== 'undefined') { S.messages = tile.messages; S.session = tile.session; }
              _renderMessagesToTile(tile);
            }
          }
        } catch(_) {}
      })();
    }
  }

  function focusTile(id) {
    const tile = tileById(id);
    if (!tile) return;

    // Save old tile S state + composer
    if (T.activeTileId && T.activeTileId !== id) {
      const oldTile = activeTile();
      if (oldTile) {
        _saveComposerTo(oldTile);
        if (typeof S !== 'undefined') {
          oldTile.messages = [...(S.messages || [])];
          oldTile.busy = !!S.busy;
          oldTile.activeStreamId = S.activeStreamId || null;
          oldTile.session = S.session;
        }
      }
    }

    // Move #msgInner id from old container to this tile's container.
    // Content stays where rendered — we only move the id attribute.
    // Tile containers keep their innerHTML: re-focusing restores the id
    // and the existing pipeline picks up from S.messages.
    const cur = document.getElementById('msgInner');
    if (cur && cur !== tile.el.querySelector('.tile-msg-inner')) {
      cur.removeAttribute('id');
    }

    T.activeTileId = id;

    // Highlight focused tile
    for (const t of T.tiles) {
      if (t.el) t.el.classList.toggle('tile--focused', t.id === id);
    }

    // Set this tile's container as the render target
    const newInner = tile.el.querySelector('.tile-msg-inner');
    if (newInner) {
      newInner.id = 'msgInner';
    }

    // Sync global S
    if (typeof S !== 'undefined') {
      S.session = tile.session;
      S.messages = tile.messages || [];
      S.busy = tile.busy || false;
      S.activeStreamId = tile.activeStreamId || null;
    }

    _restoreComposerFrom(tile);

    if (typeof syncTopbar === 'function') syncTopbar();
    if (typeof syncModelChip === 'function') syncModelChip();

    _startBusyWatcher();
  }

  // ── Render messages into the focused tile's container ────────────────────

  function _renderMessagesToTile(tile) {
    const mi = tile.el && tile.el.querySelector('.tile-msg-inner');
    if (!mi) return;
    mi.innerHTML = '';
    const createMsg = window._createMessageElement;
    const msgs = tile.messages || [];
    for (const msg of msgs) {
      if (!msg || !msg.role || msg.role === 'tool') continue;
      if (typeof createMsg === 'function') {
        const el = createMsg(msg);
        if (el) mi.appendChild(el);
      } else {
        const d = document.createElement('div');
        d.textContent = typeof msg.content === 'string' ? msg.content.slice(0, 500) : '(content)';
        mi.appendChild(d);
      }
    }
    if (mi.scrollTop !== undefined) mi.scrollTop = mi.scrollHeight;
  }

  // ── Maximize / Unmaximize ────────────────────────────────────────────────

  function maximizeTile(id) {
    const tile = tileById(id);
    if (!tile) return;
    if (tile.maximized) { unmaximizeTile(id); return; }
    const curMax = T.tiles.find(t => t.maximized);
    if (curMax) {
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
    for (const t of T.tiles) {
      if (t.el) t.el.classList.toggle('tile--hidden', !t.maximized);
    }
    _refreshGrid();
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
    _refreshGrid();
  }

  // ── Close ────────────────────────────────────────────────────────────────

  function closeTile(id) {
    const idx = T.tiles.findIndex(t => t.id === id);
    if (idx < 0) return;
    const tile = T.tiles[idx];

    if (tile.busy && tile.activeStreamId && typeof cancelSessionStream === 'function') {
      cancelSessionStream(tile.session);
    }
    if (tile.sid && typeof INFLIGHT !== 'undefined' && INFLIGHT[tile.sid]) {
      delete INFLIGHT[tile.sid];
      if (typeof clearInflightState === 'function') clearInflightState(tile.sid);
    }

    // Remove DOM — if this tile had #msgInner, clear the id so it's not orphaned
    if (tile.el) {
      const mi = tile.el.querySelector('.tile-msg-inner');
      if (mi && mi.id === 'msgInner') mi.removeAttribute('id');
      tile.el.remove();
    }
    T.tiles.splice(idx, 1);

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
    const visible = T.tiles.filter(t => !t.maximized || t === activeTile());
    const visCount = visible.length;
    T.gridEl.classList.toggle('tile-grid--empty', count === 0);
    if (visCount <= 1) {
      T.gridEl.style.gridTemplateColumns = '1fr';
      T.gridEl.style.gridTemplateRows = '1fr';
    } else if (visCount === 2) {
      T.gridEl.style.gridTemplateColumns = '1fr 1fr';
      T.gridEl.style.gridTemplateRows = '1fr';
    } else if (visCount === 3) {
      T.gridEl.style.gridTemplateColumns = '1fr 1fr 1fr';
      T.gridEl.style.gridTemplateRows = '1fr';
    } else if (visCount === 4) {
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
      if (S.messages && S.messages.length > 0) tile.messages = [...S.messages];
      tile.busy = !!S.busy;
      tile.activeStreamId = S.activeStreamId || null;
      if (!S.busy && tile.session) tile.session = S.session;
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
    const origMsgInner = document.getElementById('msgInner');
    if (origMsgInner) {
      origMsgInner.removeAttribute('id');
      origMsgInner.classList.add('messages-inner--idle');
    }
    T.gridEl.style.display = '';
  }

  function _hideTileGrid() {
    _stopBusyWatcher();

    // Remove id from any tile container so the original can reclaim it
    document.querySelectorAll('.tile-msg-inner[id="msgInner"]').forEach(el => el.removeAttribute('id'));

    // Restore #msgInner to the original element
    const origMsgInner = document.querySelector('#messages > .messages-inner--idle');
    if (origMsgInner) {
      origMsgInner.id = 'msgInner';
      origMsgInner.classList.remove('messages-inner--idle');
    }

    T.gridEl.style.display = 'none';

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
    T.gridEl = document.createElement('div');
    T.gridEl.id = 'tileGrid';
    T.gridEl.className = 'tile-grid tile-grid--empty';
    T.gridEl.style.display = 'none';

    const msgInner = document.getElementById('msgInner');
    if (msgInner && msgInner.parentNode) {
      msgInner.parentNode.appendChild(T.gridEl);
    }

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
