/**
 * FanDuel Token Extractor - Background Service Worker
 *
 * Intercepts requests to FanDuel API and extracts authentication tokens.
 * Tokens are saved to Chrome storage and can be exported to a JSON file.
 */

// Token storage
let tokens = {
  auth_token: null,
  session_token: null,
  extracted_at: null,
  expires_at: null
};

// Listen for requests to FanDuel API
chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    const headers = details.requestHeaders || [];
    let foundAuth = false;
    let foundSession = false;

    for (const header of headers) {
      const name = header.name.toLowerCase();

      // X-Auth-Token is what we need for API calls
      if (name === 'x-auth-token' && header.value) {
        tokens.auth_token = header.value;
        foundAuth = true;
        console.log('Found X-Auth-Token:', header.value.substring(0, 30) + '...');
      }

      // Also capture session token if present
      if (name === 'x-session-token' && header.value) {
        tokens.session_token = header.value;
        foundSession = true;
        console.log('Found X-Session-Token:', header.value.substring(0, 30) + '...');
      }
    }

    // If we found tokens, save them
    if (foundAuth || foundSession) {
      tokens.extracted_at = new Date().toISOString();
      tokens.expires_at = new Date(Date.now() + 60 * 60 * 1000).toISOString();

      // Save to Chrome storage
      chrome.storage.local.set({ fanduel_tokens: tokens }, () => {
        console.log('FanDuel tokens saved:', {
          auth: tokens.auth_token ? tokens.auth_token.substring(0, 30) + '...' : null,
          session: tokens.session_token ? tokens.session_token.substring(0, 30) + '...' : null,
        });
        updateBadge(true);
      });

      // Auto-export to file (using data URL instead of blob)
      autoExportTokens();
    }
  },
  {
    urls: [
      'https://*.fanduel.com/*',
      'https://api.fanduel.com/*'
    ]
  },
  ['requestHeaders', 'extraHeaders']
);

// Update extension badge
function updateBadge(hasFreshTokens) {
  if (hasFreshTokens) {
    chrome.action.setBadgeText({ text: '✓' });
    chrome.action.setBadgeBackgroundColor({ color: '#4CAF50' });
  } else {
    chrome.action.setBadgeText({ text: '!' });
    chrome.action.setBadgeBackgroundColor({ color: '#FF9800' });
  }
}

// Auto-export tokens to downloads folder
async function autoExportTokens() {
  if (!tokens.auth_token && !tokens.session_token) {
    return;
  }

  const exportData = {
    auth_token: tokens.auth_token,
    session_token: tokens.session_token,
    extracted_at: tokens.extracted_at,
    expires_at: tokens.expires_at
  };

  // Use data URL instead of blob (blob URLs don't work in service workers)
  const jsonString = JSON.stringify(exportData, null, 2);
  const dataUrl = 'data:application/json;base64,' + btoa(jsonString);

  try {
    await chrome.downloads.download({
      url: dataUrl,
      filename: 'fanduel_tokens.json',
      conflictAction: 'overwrite',
      saveAs: false
    });
    console.log('Tokens exported to ~/Downloads/fanduel_tokens.json');
  } catch (error) {
    console.error('Failed to export tokens:', error);
  }
}

// Handle messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'getTokens') {
    chrome.storage.local.get(['fanduel_tokens'], (result) => {
      sendResponse(result.fanduel_tokens || null);
    });
    return true;
  }

  if (request.action === 'exportTokens') {
    autoExportTokens().then(() => {
      sendResponse({ success: true });
    }).catch((error) => {
      sendResponse({ success: false, error: error.message });
    });
    return true;
  }

  if (request.action === 'clearTokens') {
    tokens = {
      auth_token: null,
      session_token: null,
      extracted_at: null,
      expires_at: null
    };
    chrome.storage.local.remove(['fanduel_tokens'], () => {
      updateBadge(false);
      sendResponse({ success: true });
    });
    return true;
  }
});

// Load existing tokens on startup
chrome.storage.local.get(['fanduel_tokens'], (result) => {
  if (result.fanduel_tokens) {
    tokens = result.fanduel_tokens;
    const extractedAt = new Date(tokens.extracted_at);
    const age = Date.now() - extractedAt.getTime();
    const isFresh = age < 60 * 60 * 1000;
    updateBadge(isFresh);
    console.log('Loaded existing tokens, fresh:', isFresh);
  } else {
    updateBadge(false);
  }
});

console.log('FanDuel Token Extractor loaded');
