(function (global) {
  'use strict';

  var BRAND_NAME = '__BRAND_NAME__';
  var BRAND_SLUG = '__BRAND_SLUG__';
  var loaderScript = document.currentScript;
  if (global[BRAND_NAME] && global[BRAND_NAME] === global.WeKnora) return;

  var HOST_SOURCE = 'weknora-host';
  var EMBED_SOURCE = 'weknora-embed';
  var POSITIONS = ['bottom-right', 'bottom-left', 'top-right', 'top-left'];
  var DEFAULT_POSITION = 'bottom-right';
  var DEFAULT_COLOR = '__BRAND_PRIMARY_COLOR__';
  var DEFAULT_TITLE = BRAND_NAME;
  var DEFAULT_WIDTH = 400;
  var DEFAULT_HEIGHT = 600;

  var instance = null;
  var listeners = {};

  function normalizePosition(pos) {
    if (!pos || POSITIONS.indexOf(pos) < 0) return DEFAULT_POSITION;
    return pos;
  }

  function positionStyles(position, kind) {
    var isLeft = position.indexOf('left') >= 0;
    var isTop = position.indexOf('top') >= 0;
    var horizontal = isLeft ? 'left:24px' : 'right:24px';
    if (kind === 'launcher') {
      return horizontal + ';' + (isTop ? 'top:24px' : 'bottom:24px');
    }
    return horizontal + ';' + (isTop ? 'top:88px' : 'bottom:88px');
  }

  function emit(event, payload) {
    var handlers = listeners[event];
    if (!handlers) return;
    handlers.slice().forEach(function (fn) {
      try { fn(payload); } catch (e) { console.error('[' + BRAND_NAME + ']', e); }
    });
  }

  function createWidget(opts) {
    var channelId = opts.channel || opts.channelId;
    var staticToken = opts.token;
    var tokenEndpoint = opts.tokenEndpoint || opts.token_endpoint || '';
    if (!channelId || (!staticToken && !tokenEndpoint)) {
      console.warn('[' + BRAND_NAME + '] channel and (token or tokenEndpoint) are required');
      return null;
    }

    var currentToken = staticToken || '';
    var tokenInFlight = null;
    var refreshTimer = null;

    function scheduleRefresh(expiresInSec) {
      if (!tokenEndpoint) return;
      if (refreshTimer) clearTimeout(refreshTimer);
      var ttl = Number(expiresInSec) > 0 ? Number(expiresInSec) : 1800;
      var delayMs = Math.max(Math.floor(ttl * 0.8), 30) * 1000;
      refreshTimer = setTimeout(function () {
        loadToken(true).then(function (tok) {
          if (tok) provideToken();
        }).catch(function () {});
      }, delayMs);
    }
    function loadToken(force) {
      if (staticToken) return Promise.resolve(staticToken);
      if (currentToken && !force) return Promise.resolve(currentToken);
      if (tokenInFlight) return tokenInFlight;
      tokenInFlight = fetch(tokenEndpoint, {
        method: 'GET',
        credentials: 'include',
        headers: { Accept: 'application/json' },
      })
        .then(function (res) {
          if (!res.ok) throw new Error('token endpoint HTTP ' + res.status);
          return res.json();
        })
        .then(function (data) {
          var d = data || {};
          var inner = d.data || d;
          var tok = inner.token || inner.session_token || '';
          var expiresIn = inner.expiresIn || inner.expires_in || 0;
          if (!tok) throw new Error('token endpoint returned no token');
          currentToken = tok;
          scheduleRefresh(expiresIn);
          return tok;
        })
        .catch(function (e) {
          console.error('[' + BRAND_NAME + '] failed to load token', e);
          throw e;
        })
        .then(function (tok) { tokenInFlight = null; return tok; }, function (e) { tokenInFlight = null; throw e; });
      return tokenInFlight;
    }

    var position = normalizePosition(opts.position);
    var primaryColor = opts.primaryColor || opts.primary_color || DEFAULT_COLOR;
    var title = opts.title || DEFAULT_TITLE;
    var baseUrl = (opts.baseUrl || opts.base || '').replace(/\/$/, '');
    if (!baseUrl) {
      var script = opts.scriptEl || loaderScript;
      if (script && script.src) {
        try {
          var scriptUrl = new URL(script.src, global.location ? global.location.href : undefined);
          var fileName = scriptUrl.pathname.split('/').pop();
          if (fileName === 'weknora-widget.js' || fileName === BRAND_SLUG + '-widget.js') {
            scriptUrl.pathname = scriptUrl.pathname.slice(0, -fileName.length);
            scriptUrl.search = '';
            scriptUrl.hash = '';
            baseUrl = scriptUrl.href.replace(/\/$/, '');
          }
        } catch (e) {}
      }
      if (!baseUrl) baseUrl = global.location ? global.location.origin : '';
    }

    var panelWidth = Number(opts.width) > 0 ? Number(opts.width) : DEFAULT_WIDTH;
    var panelHeight = Number(opts.height) > 0 ? Number(opts.height) : DEFAULT_HEIGHT;
    var embedUrl = baseUrl + '/embed/' + encodeURIComponent(channelId);
    var embedOrigin = baseUrl;
    try {
      embedOrigin = new URL(embedUrl, global.location ? global.location.href : undefined).origin;
    } catch (e) {
      embedOrigin = baseUrl;
    }
    var destroyed = false;
    var panelOpen = false;
    var iframeReady = false;
    var iframeOrigin = '';

    var launcher = document.createElement('button');
    launcher.type = 'button';
    launcher.setAttribute('aria-label', title);
    launcher.textContent = '💬';
    launcher.style.cssText = [
      'position:fixed',
      'z-index:2147483000',
      'width:56px',
      'height:56px',
      'border-radius:50%',
      'border:none',
      'cursor:pointer',
      'font-size:24px',
      'box-shadow:0 4px 16px rgba(0,0,0,.18)',
      'background:' + primaryColor,
      'color:#fff',
      'opacity:0.92',
      'transition:opacity .2s',
      positionStyles(position, 'launcher'),
    ].join(';');

    var panel = document.createElement('div');
    panel.style.cssText = [
      'position:fixed',
      'z-index:2147482999',
      'width:' + panelWidth + 'px',
      'max-width:calc(100vw - 32px)',
      'height:' + panelHeight + 'px',
      'max-height:calc(100vh - 100px)',
      'border-radius:12px',
      'overflow:hidden',
      'box-shadow:0 8px 32px rgba(0,0,0,.2)',
      'display:none',
      'background:#fff',
      positionStyles(position, 'panel'),
    ].join(';');

    var iframe = document.createElement('iframe');
    iframe.src = embedUrl;
    iframe.style.cssText = 'width:100%;height:100%;border:none';
    iframe.setAttribute('allow', 'clipboard-write');
    var hostOrigin = '';
    try {
      hostOrigin = global.location ? global.location.origin : '';
    } catch (e) { hostOrigin = ''; }
    var crossOriginEmbed = hostOrigin && embedOrigin && hostOrigin !== embedOrigin;
    var sandboxAttr = String(opts.sandbox || opts.sandboxMode || '').trim();
    if (!sandboxAttr && opts.scriptEl) {
      sandboxAttr = String(opts.scriptEl.getAttribute('data-sandbox') || '').trim();
    }
    if (sandboxAttr === 'true' || sandboxAttr === '1') {
      sandboxAttr = 'allow-scripts allow-forms allow-popups allow-modals allow-same-origin';
    }
    if (!sandboxAttr && crossOriginEmbed) {
      sandboxAttr = 'allow-scripts allow-forms allow-popups allow-modals allow-same-origin';
    }
    if (sandboxAttr && sandboxAttr !== 'false' && sandboxAttr !== '0') {
      iframe.setAttribute('sandbox', sandboxAttr);
    }
    iframe.setAttribute('title', title);
    panel.appendChild(iframe);

    function isTrustedOrigin(origin) {
      if (!origin || origin === 'null') return false;
      return origin === embedOrigin;
    }

    function postToIframe(message) {
      if (!iframe.contentWindow) return;
      iframe.contentWindow.postMessage(message, embedOrigin || '/');
    }

    function provideToken() {
      loadToken(false).then(function (tok) {
        if (!tok) return;
        postToIframe({
          source: HOST_SOURCE,
          type: 'provide_token',
          token: tok,
          channel_id: channelId,
        });
      }).catch(function () {});
    }

    function postHostPayload(type, payload) {
      if (!iframe.contentWindow) {
        console.warn('[' + BRAND_NAME + '] iframe not ready');
        return false;
      }
      postToIframe({ source: HOST_SOURCE, type: type, payload: payload || {} });
      return true;
    }

    function whenIframeReady(fn, attempt) {
      var tries = attempt || 0;
      if (iframeReady && iframe.contentWindow) {
        fn();
        return;
      }
      if (tries >= 20) {
        console.warn('[' + BRAND_NAME + '] iframe not ready');
        return;
      }
      setTimeout(function () { whenIframeReady(fn, tries + 1); }, 100);
    }

    function setContext(ctx) {
      if (!ctx || typeof ctx !== 'object') {
        console.warn('[' + BRAND_NAME + '] setContext expects an object');
        return;
      }
      postHostPayload('set_context', ctx);
    }

    function openWithQuery(query) {
      var text = String(query || '').trim();
      if (!text) {
        console.warn('[' + BRAND_NAME + '] openWithQuery requires a non-empty query');
        return;
      }
      setOpen(true);
      whenIframeReady(function () {
        postHostPayload('open_with_query', { query: text });
      });
    }

    function setLocale(locale) {
      var loc = String(locale || '').trim();
      if (!loc) {
        console.warn('[' + BRAND_NAME + '] setLocale requires a locale string');
        return;
      }
      postHostPayload('set_locale', { locale: loc });
    }

    function onMessage(e) {
      if (e.source !== iframe.contentWindow) return;
      if (!isTrustedOrigin(e.origin)) return;
      if (!e.data || e.data.source !== EMBED_SOURCE) return;
      if (e.data.channel_id && e.data.channel_id !== channelId) return;

      if (!iframeOrigin) {
        iframeOrigin = e.origin;
      }

      switch (e.data.type) {
        case 'bootstrap_request':
          provideToken();
          break;
        case 'ready':
          iframeReady = true;
          launcher.style.opacity = '1';
          emit('ready', { channelId: channelId });
          break;
        case 'message_sent':
          emit('message_sent', {
            channelId: channelId,
            sessionId: e.data.session_id,
            query: e.data.query,
          });
          break;
        case 'message_received':
          emit('message_received', {
            channelId: channelId,
            sessionId: e.data.session_id,
            content: e.data.content,
          });
          break;
        default:
          break;
      }
    }

    function setOpen(next) {
      panelOpen = !!next;
      panel.style.display = panelOpen ? 'block' : 'none';
      launcher.textContent = panelOpen ? '✕' : '💬';
      if (panelOpen) {
        emit('open', { channelId: channelId });
      } else {
        emit('close', { channelId: channelId });
      }
    }

    function open() { setOpen(true); }
    function close() { setOpen(false); }
    function toggle() { setOpen(!panelOpen); }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (refreshTimer) clearTimeout(refreshTimer);
      global.removeEventListener('message', onMessage);
      if (launcher.parentNode) launcher.parentNode.removeChild(launcher);
      if (panel.parentNode) panel.parentNode.removeChild(panel);
      listeners = {};
      if (instance === api) instance = null;
    }

    launcher.addEventListener('click', toggle);
    iframe.addEventListener('load', function () {
      iframeOrigin = embedOrigin;
      provideToken();
    });

    document.body.appendChild(launcher);
    document.body.appendChild(panel);
    global.addEventListener('message', onMessage);

    return {
      open: open,
      close: close,
      toggle: toggle,
      destroy: destroy,
      isOpen: function () { return panelOpen; },
      isReady: function () { return iframeReady; },
      setContext: setContext,
      openWithQuery: openWithQuery,
      setLocale: setLocale,
    };
  }

  var api = {
    init: function (opts) {
      if (instance) instance.destroy();
      instance = createWidget(opts || {});
      return instance;
    },
    open: function () { if (instance) instance.open(); },
    close: function () { if (instance) instance.close(); },
    toggle: function () { if (instance) instance.toggle(); },
    destroy: function () { if (instance) instance.destroy(); },
    on: function (event, handler) {
      if (!handler || typeof handler !== 'function') return;
      if (!listeners[event]) listeners[event] = [];
      listeners[event].push(handler);
    },
    off: function (event, handler) {
      if (!listeners[event]) return;
      if (!handler) {
        delete listeners[event];
        return;
      }
      listeners[event] = listeners[event].filter(function (fn) { return fn !== handler; });
    },
    setContext: function (ctx) { if (instance) instance.setContext(ctx); },
    openWithQuery: function (query) { if (instance) instance.openWithQuery(query); },
    setLocale: function (locale) { if (instance) instance.setLocale(locale); },
  };

  global[BRAND_NAME] = api;
  global.WeKnora = api;

  var legacyScript = loaderScript;
  if (legacyScript) {
    var legacyChannel = legacyScript.getAttribute('data-channel');
    var legacyToken = legacyScript.getAttribute('data-token');
    var legacyTokenEndpoint = legacyScript.getAttribute('data-token-endpoint');
    if (legacyChannel && (legacyToken || legacyTokenEndpoint)) {
      api.init({
        channel: legacyChannel,
        token: legacyToken,
        tokenEndpoint: legacyTokenEndpoint,
        scriptEl: legacyScript,
        position: legacyScript.getAttribute('data-position'),
        primaryColor: legacyScript.getAttribute('data-primary-color'),
        title: legacyScript.getAttribute('data-title'),
        baseUrl: legacyScript.getAttribute('data-base-url'),
        width: legacyScript.getAttribute('data-width'),
        height: legacyScript.getAttribute('data-height'),
      });
    }
  }
})(typeof window !== 'undefined' ? window : this);
