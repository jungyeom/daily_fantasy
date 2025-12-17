/**
 * FanDuel Token Extractor - Popup Script
 */

// Elements
const statusEl = document.getElementById('status');
const tokenInfoEl = document.getElementById('token-info');
const authTokenEl = document.getElementById('auth-token');
const sessionTokenEl = document.getElementById('session-token');
const timeInfoEl = document.getElementById('time-info');
const extractedTimeEl = document.getElementById('extracted-time');
const exportBtn = document.getElementById('export-btn');
const copyBtn = document.getElementById('copy-btn');
const copyFeedback = document.getElementById('copy-feedback');

// Current tokens
let currentTokens = null;

// Load tokens on popup open
function loadTokens() {
  chrome.runtime.sendMessage({ action: 'getTokens' }, (tokens) => {
    currentTokens = tokens;
    updateUI(tokens);
  });
}

// Update UI based on token state
function updateUI(tokens) {
  if (!tokens || !tokens.auth_token || !tokens.session_token) {
    // No tokens
    statusEl.className = 'status none';
    statusEl.innerHTML = '<span class="status-icon">⚠️</span><span class="status-text">No tokens captured</span>';
    tokenInfoEl.style.display = 'none';
    timeInfoEl.style.display = 'none';
    exportBtn.disabled = true;
    copyBtn.disabled = true;
    return;
  }

  // Check freshness
  const extractedAt = new Date(tokens.extracted_at);
  const age = Date.now() - extractedAt.getTime();
  const isFresh = age < 60 * 60 * 1000; // 1 hour

  if (isFresh) {
    statusEl.className = 'status fresh';
    statusEl.innerHTML = '<span class="status-icon">✅</span><span class="status-text">Tokens captured (fresh)</span>';
  } else {
    statusEl.className = 'status stale';
    statusEl.innerHTML = '<span class="status-icon">⏰</span><span class="status-text">Tokens may be expired</span>';
  }

  // Show token preview (truncated for security)
  authTokenEl.textContent = truncateToken(tokens.auth_token);
  sessionTokenEl.textContent = truncateToken(tokens.session_token);
  tokenInfoEl.style.display = 'block';

  // Show time
  extractedTimeEl.textContent = formatTime(extractedAt);
  timeInfoEl.style.display = 'block';

  // Enable buttons
  exportBtn.disabled = false;
  copyBtn.disabled = false;
}

// Truncate token for display
function truncateToken(token) {
  if (!token) return '-';
  if (token.length <= 40) return token;
  return token.substring(0, 20) + '...' + token.substring(token.length - 10);
}

// Format time for display
function formatTime(date) {
  const now = new Date();
  const diff = now - date;

  if (diff < 60000) {
    return 'Just now';
  } else if (diff < 3600000) {
    const mins = Math.floor(diff / 60000);
    return `${mins} minute${mins > 1 ? 's' : ''} ago`;
  } else {
    return date.toLocaleTimeString();
  }
}

// Show copy feedback
function showCopyFeedback(message) {
  copyFeedback.textContent = message;
  copyFeedback.classList.add('show');
  setTimeout(() => {
    copyFeedback.classList.remove('show');
  }, 2000);
}

// Export tokens to file
exportBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'exportTokens' }, (response) => {
    if (response.success) {
      showCopyFeedback('Exported to Downloads/fanduel_tokens.json');
    } else {
      showCopyFeedback('Export failed: ' + response.error);
    }
  });
});

// Copy tokens to clipboard
copyBtn.addEventListener('click', async () => {
  if (!currentTokens) return;

  const text = `FANDUEL_AUTH_TOKEN=${currentTokens.auth_token}\nFANDUEL_SESSION_TOKEN=${currentTokens.session_token}`;

  try {
    await navigator.clipboard.writeText(text);
    showCopyFeedback('Copied to clipboard!');
  } catch (error) {
    showCopyFeedback('Failed to copy: ' + error.message);
  }
});

// Load tokens when popup opens
loadTokens();

// Refresh every 5 seconds while popup is open
setInterval(loadTokens, 5000);
