/**
 * ============================================================================
 * NexusDL API — Swagger UI Custom Extensions
 * ============================================================================
 *
 * Ce script ajoute des extensions personnalisées à l'interface Swagger UI
 * pour améliorer l'expérience utilisateur et ajouter des fonctionnalités
 * spécifiques à NexusDL.
 *
 * Style: Cyberpunk Neon / Hacker Futurist
 * ============================================================================
 */

(function() {
  'use strict';

  // ========================================================================
  // Configuration
  // ========================================================================

  const CONFIG = {
    appName: 'NexusDL',
    version: '0.1.0',
    theme: {
      primary: '#00ff41',
      secondary: '#00ffff',
      accent: '#ff00ff',
      background: '#000000',
      surface: '#0d1117',
    },
    autoRefreshInterval: 30000, // 30 secondes
    enableConsoleBranding: true,
    enableKeyboardShortcuts: true,
  };

  // ========================================================================
  // Console Branding
  // ========================================================================

  if (CONFIG.enableConsoleBranding) {
    const styles = [
      'color: #00ff41',
      'font-family: monospace',
      'font-size: 14px',
      'font-weight: bold',
      'text-shadow: 0 0 10px #00ff41',
    ].join(';');

    const taglineStyles = [
      'color: #00aaaa',
      'font-family: monospace',
      'font-size: 11px',
      'font-style: italic',
    ].join(';');

    console.log('%c╔═══════════════════════════════════════╗', styles);
    console.log('%c║        NexusDL API Documentation      ║', styles);
    console.log('%c║   Your manga, your library, your way  ║', taglineStyles);
    console.log('%c╚═══════════════════════════════════════╝', styles);
    console.log('%cVersion: ' + CONFIG.version, taglineStyles);
    console.log('%cDocs: https://nexusdl.dev/docs/api', taglineStyles);
    console.log('%c⚠️  Attention: This is a developer console.', 'color: #ffff00; font-family: monospace;');
    console.log('%c   Do not paste code here if you don\'t understand it.', 'color: #ffff00; font-family: monospace;');
  }

  // ========================================================================
  // Keyboard Shortcuts
  // ========================================================================

  if (CONFIG.enableKeyboardShortcuts) {
    document.addEventListener('keydown', function(event) {
      // Ctrl+K ou Cmd+K : Focus sur la barre de recherche
      if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
        event.preventDefault();
        const searchInput = document.querySelector('.operation-filter input');
        if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
      }

      // Ctrl+/ ou Cmd+/ : Afficher/masquer l'aide
      if ((event.ctrlKey || event.metaKey) && event.key === '/') {
        event.preventDefault();
        showShortcutsHelp();
      }

      // Escape : Fermer les modals
      if (event.key === 'Escape') {
        const openModals = document.querySelectorAll('.dialog-ux');
        openModals.forEach(modal => {
          modal.style.display = 'none';
        });
      }

      // F5 : Rafraîchir la documentation
      if (event.key === 'F5') {
        event.preventDefault();
        location.reload();
      }
    });
  }

  function showShortcutsHelp() {
    const shortcuts = [
      { keys: 'Ctrl+K', description: 'Search operations' },
      { keys: 'Ctrl+/', description: 'Show this help' },
      { keys: 'Escape', description: 'Close modals' },
      { keys: 'F5', description: 'Refresh documentation' },
      { keys: '↑/↓', description: 'Navigate operations' },
      { keys: 'Enter', description: 'Expand/collapse operation' },
    ];

    let message = '╔══════════════════════════════════╗\n';
    message += '║     NexusDL Keyboard Shortcuts     ║\n';
    message += '╠══════════════════════════════════╣\n';
    shortcuts.forEach(s => {
      message += `║ ${s.keys.padEnd(12)} │ ${s.description.padEnd(22)} ║\n`;
    });
    message += '╚══════════════════════════════════╝';

    console.log(message);
  }

  // ========================================================================
  // Enhancements to Swagger UI
  // ========================================================================

  /**
   * Ajoute des badges aux opérations selon leur tag
   */
  function addOperationBadges() {
    const tagBadges = {
      'auth': { color: '#ff00ff', label: '🔐 AUTH' },
      'health': { color: '#00ff41', label: '💚 HEALTH' },
      'sites': { color: '#00ffff', label: '🌐 SITES' },
      'search': { color: '#ffff00', label: '🔍 SEARCH' },
      'manga': { color: '#ff0040', label: '📚 MANGA' },
      'chapters': { color: '#00ffff', label: '📖 CHAPTERS' },
      'library': { color: '#00ff41', label: '📚 LIBRARY' },
      'download': { color: '#ffff00', label: '⬇️ DOWNLOAD' },
      'settings': { color: '#ff00ff', label: '⚙️ SETTINGS' },
      'websocket': { color: '#00ffff', label: '🔌 WS' },
    };

    // Attendre que Swagger UI soit chargé
    const observer = new MutationObserver(function(mutations) {
      document.querySelectorAll('.opblock-tag').forEach(tag => {
        const tagName = tag.getAttribute('data-tag') || tag.textContent.trim().toLowerCase();
        if (tagBadges[tagName] && !tag.querySelector('.nx-badge')) {
          const badge = document.createElement('span');
          badge.className = 'nx-badge';
          badge.textContent = tagBadges[tagName].label;
          badge.style.cssText = `
            display: inline-block;
            background-color: ${tagBadges[tagName].color}20;
            color: ${tagBadges[tagName].color};
            border: 1px solid ${tagBadges[tagName].color};
            border-radius: 3px;
            padding: 2px 8px;
            font-size: 10px;
            font-weight: bold;
            margin-left: 12px;
            font-family: 'JetBrains Mono', monospace;
          `;
          tag.appendChild(badge);
        }
      });
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  /**
   * Ajoute un compteur d'opérations par méthode HTTP
   */
  function addMethodCounter() {
    const methods = {
      GET: { color: '#00ffff', count: 0 },
      POST: { color: '#00ff41', count: 0 },
      PUT: { color: '#ffff00', count: 0 },
      DELETE: { color: '#ff0040', count: 0 },
      PATCH: { color: '#ff00ff', count: 0 },
    };

    document.querySelectorAll('.opblock-summary-method').forEach(el => {
      const method = el.textContent.trim().toUpperCase();
      if (methods[method]) {
        methods[method].count++;
      }
    });

    const counter = document.createElement('div');
    counter.style.cssText = `
      position: fixed;
      bottom: 20px;
      right: 20px;
      background-color: ${CONFIG.theme.surface};
      border: 1px solid ${CONFIG.theme.primary};
      border-radius: 6px;
      padding: 12px 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      box-shadow: 0 0 20px rgba(0, 255, 65, 0.3);
      z-index: 9999;
    `;

    let html = '<div style="color: #00ff41; font-weight: bold; margin-bottom: 8px;">API Stats</div>';
    Object.entries(methods).forEach(([method, data]) => {
      if (data.count > 0) {
        html += `<div style="display: flex; justify-content: space-between; gap: 16px;">
          <span style="color: ${data.color}; font-weight: bold;">${method}</span>
          <span style="color: #00aaaa;">${data.count}</span>
        </div>`;
      }
    });

    counter.innerHTML = html;
    document.body.appendChild(counter);
  }

  /**
   * Ajoute un bouton "Try all" pour tester rapidement les endpoints
   */
  function addQuickActions() {
    const actions = document.createElement('div');
    actions.style.cssText = `
      position: fixed;
      top: 80px;
      right: 20px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      z-index: 9999;
    `;

    const buttons = [
      { label: '🏥 Health', action: () => testEndpoint('/health/detailed') },
      { label: '📚 Sites', action: () => testEndpoint('/api/v1/sites') },
      { label: '🔍 Search', action: () => testEndpoint('/api/v1/search?q=test') },
    ];

    buttons.forEach(btn => {
      const button = document.createElement('button');
      button.textContent = btn.label;
      button.style.cssText = `
        background-color: ${CONFIG.theme.surface};
        color: ${CONFIG.theme.primary};
        border: 1px solid ${CONFIG.theme.primary};
        border-radius: 4px;
        padding: 8px 12px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        cursor: pointer;
        transition: all 0.2s ease;
      `;
      button.addEventListener('mouseenter', () => {
        button.style.backgroundColor = CONFIG.theme.primary;
        button.style.color = CONFIG.theme.background;
      });
      button.addEventListener('mouseleave', () => {
        button.style.backgroundColor = CONFIG.theme.surface;
        button.style.color = CONFIG.theme.primary;
      });
      button.addEventListener('click', btn.action);
      actions.appendChild(button);
    });

    document.body.appendChild(actions);
  }

  async function testEndpoint(path) {
    try {
      console.log(`%c[Testing] ${path}`, 'color: #00ffff; font-weight: bold;');
      const response = await fetch(path);
      const data = await response.json();
      console.log(`%c[Response] ${response.status}`, 'color: #00ff41;', data);
      alert(`Endpoint: ${path}\nStatus: ${response.status}\n\nCheck console for details.`);
    } catch (error) {
      console.error(`%c[Error] ${path}`, 'color: #ff0040;', error);
      alert(`Error testing ${path}: ${error.message}`);
    }
  }

  // ========================================================================
  // Initialization
  // ========================================================================

  function init() {
    // Attendre que Swagger UI soit complètement chargé
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', onReady);
    } else {
      onReady();
    }
  }

  function onReady() {
    // Attendre un peu pour que Swagger UI finisse de se charger
    setTimeout(() => {
      try {
        addOperationBadges();
        addMethodCounter();
        addQuickActions();
        console.log('%c[NexusDL] Documentation enhancements loaded', 'color: #00ff41; font-weight: bold;');
      } catch (error) {
        console.warn('[NexusDL] Failed to load enhancements:', error);
      }
    }, 1000);
  }

  // ========================================================================
  // Auto-refresh for health endpoint
  // ========================================================================

  let healthRefreshInterval = null;

  function startHealthRefresh() {
    if (healthRefreshInterval) return;

    healthRefreshInterval = setInterval(async () => {
      try {
        const response = await fetch('/health');
        const data = await response.json();
        // Mettre à jour un indicateur visuel si présent
        const indicator = document.querySelector('.nx-health-indicator');
        if (indicator) {
          indicator.textContent = data.status === 'healthy' ? '●' : '○';
          indicator.style.color = data.status === 'healthy' ? '#00ff41' : '#ff0040';
        }
      } catch (error) {
        console.debug('[NexusDL] Health check failed:', error);
      }
    }, CONFIG.autoRefreshInterval);
  }

  function stopHealthRefresh() {
    if (healthRefreshInterval) {
      clearInterval(healthRefreshInterval);
      healthRefreshInterval = null;
    }
  }

  // ========================================================================
  // Public API
  // ========================================================================

  window.NexusDL = {
    config: CONFIG,
    startHealthRefresh,
    stopHealthRefresh,
    testEndpoint,
    showShortcutsHelp,
  };

  // ========================================================================
  // Start
  // ========================================================================

  init();

})();
