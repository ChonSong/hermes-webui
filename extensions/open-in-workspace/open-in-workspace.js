(function () {
  'use strict';

  if (document.getElementById('ext-open-in-workspace--loaded')) return;
  var MARKER = document.createElement('meta');
  MARKER.id = 'ext-open-in-workspace--loaded';

  /* ── CSS injected via plain <style> block ── */
  var style = document.createElement('style');
  style.textContent =
    '.ext-ws-open-wrap{padding:4px 12px 8px}' +
    '.ext-ws-open-btn{background:none;border:none;color:var(--blue);font-size:10px;cursor:pointer;padding:0;opacity:.7;display:inline-flex;align-items:center;gap:3px;white-space:nowrap}' +
    '.ext-ws-open-btn:hover{opacity:1}' +
    '.ext-ws-open-btn svg{width:12px;height:12px;flex-shrink:0}';
  document.head.appendChild(style);

  /* ── SVG icon (Lucide folder-open, small) ── */
  var FOLDER_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 6a2 2 0 0 1 2-2h3.28a2 2 0 0 1 1.82 1.07l1.8 3.87A2 2 0 0 0 12.72 11H20a2 2 0 0 1 2 2v5"/></svg>';

  /* ── Inject button into a single tool-card-row ── */
  function injectButton(row) {
    if (row.getAttribute('data-ext-ws-open')) return;
    var tcData = row._tcData;
    if (!tcData || !tcData.args) return;

    var path =
      tcData.args.path ||
      tcData.args.file_path ||
      tcData.args.file ||
      tcData.args.target ||
      tcData.args.name;
    if (!path || typeof path !== 'string') return;

    /* Make sure we don't re-process */
    row.setAttribute('data-ext-ws-open', '1');

    /* Find the tool-card detail container or create a fallback */
    var detail = row.querySelector('.tool-card-detail');
    if (detail) {
      var wrap = document.createElement('div');
      wrap.className = 'ext-ws-open-wrap';
      var btn = document.createElement('button');
      btn.className = 'ext-ws-open-btn';
      btn.innerHTML = FOLDER_ICON;
      btn.appendChild(document.createTextNode(' Open in workspace'));
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        if (typeof openArtifactPath === 'function') {
          openArtifactPath(path);
        }
      });
      wrap.appendChild(btn);
      detail.appendChild(wrap);
    } else {
      /* Cards without .tool-card-detail (no snippet/args) — add below the card */
      var card = row.querySelector('.tool-card');
      if (card) {
        var wrap = document.createElement('div');
        wrap.className = 'ext-ws-open-wrap';
        wrap.style.padding = '2px 12px 8px';
        var btn = document.createElement('button');
        btn.className = 'ext-ws-open-btn';
        btn.innerHTML = FOLDER_ICON;
        btn.appendChild(document.createTextNode(' Open in workspace'));
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          if (typeof openArtifactPath === 'function') {
            openArtifactPath(path);
          }
        });
        wrap.appendChild(btn);
        card.appendChild(wrap);
      }
    }
  }

  /* ── Observe the entire chat for new tool-card-rows ── */
  var observer = new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      if (!added || added.length === 0) continue;
      for (var j = 0; j < added.length; j++) {
        var node = added[j];
        if (node.nodeType !== 1) continue;
        /* Direct match */
        if (
          node.matches &&
          node.matches('.tool-card-row[data-tool-kind="read"]')
        ) {
          injectButton(node);
        }
        /* Check children */
        if (node.querySelectorAll) {
          var cards = node.querySelectorAll(
            '.tool-card-row[data-tool-kind="read"]'
          );
          for (var k = 0; k < cards.length; k++) {
            injectButton(cards[k]);
          }
        }
      }
    }
  });

  /* ── Also process existing cards on startup ── */
  function processExisting() {
    var existing = document.querySelectorAll(
      '.tool-card-row[data-tool-kind="read"]'
    );
    for (var i = 0; i < existing.length; i++) {
      injectButton(existing[i]);
    }
  }

  /* ── Start observing after DOM is ready ── */
  function start() {
    document.head.appendChild(MARKER);
    var chat = document.querySelector('main') || document.body;
    observer.observe(chat, { childList: true, subtree: true });
    processExisting();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
