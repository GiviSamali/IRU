const USAGE_FALLBACK_TEXT = 'Токены сегодня: —';

function formatUsageTokens(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

function formatUsageCost(value) {
  const n = Number(value || 0);
  if (n <= 0) return '~$0.00';
  if (n < 0.01) return `~$${n.toFixed(4)}`;
  return `~$${n.toFixed(2)}`;
}

function renderUsageSummaryLine(summary) {
  const total = formatUsageTokens(summary?.total_tokens || 0);
  const cost = formatUsageCost(summary?.estimated_cost_usd || 0);
  const calls = Number(summary?.llm_calls || 0);
  return `${total} токенов · ${cost} · ${calls} LLM calls`;
}

function renderUsageDetails(data) {
  const summary = data?.summary || {};
  const recent = Array.isArray(data?.recent_events) ? data.recent_events : [];
  const block = (label, item) => `
    <div class="usage-stat-row">
      <span>${escapeHTML(label)}</span>
      <strong>${escapeHTML(renderUsageSummaryLine(item || {}))}</strong>
    </div>`;
  const recentHtml = recent.length
    ? recent.slice(0, 8).map((event) => `
      <div class="usage-event-row">
        <span>${escapeHTML(event.phase || event.route || 'llm')}</span>
        <strong>${escapeHTML(formatUsageTokens(event.total_tokens || 0))}</strong>
        <em>${escapeHTML(formatUsageCost(event.estimated_cost_usd || 0))}</em>
      </div>`).join('')
    : '<div class="usage-empty">Пока нет LLM-вызовов.</div>';

  return `
    <div class="usage-note">Примерная стоимость, не счёт и не billing.</div>
    ${block('Сегодня', summary.today)}
    ${block('Месяц', summary.month)}
    ${block('Всё время', summary.all_time)}
    <details class="usage-details">
      <summary>Последние вызовы</summary>
      <div class="usage-events">${recentHtml}</div>
    </details>`;
}

async function refreshUsageSummary() {
  const badgeText = document.getElementById('usageBadgeText');
  const body = document.getElementById('usagePopoverBody');
  if (!badgeText || !body) return;

  try {
    const response = await apiFetch(`${API}/api/usage/summary`, { headers: authHeaders() });
    const data = await response.json();
    if (!response.ok || data.status !== 'ok') throw new Error(data.detail || data.error || `HTTP ${response.status}`);
    const today = data.summary?.today || {};
    badgeText.textContent = `Токены сегодня: ${renderUsageSummaryLine(today)}`;
    body.innerHTML = renderUsageDetails(data);
  } catch (err) {
    badgeText.textContent = USAGE_FALLBACK_TEXT;
    body.innerHTML = '<div class="usage-empty">Не удалось загрузить usage.</div>';
  }
}

async function refreshTaskUsage(taskId, msgIndex) {
  if (!taskId || Number.isNaN(Number(msgIndex))) return;
  try {
    const response = await apiFetch(`${API}/api/tasks/${encodeURIComponent(taskId)}/usage`, { headers: authHeaders() });
    const data = await response.json();
    if (!response.ok || data.status !== 'ok') return;
    const msg = state.messages[msgIndex];
    if (!msg) return;
    msg.usageSummary = data.summary || null;
    renderMessages();
    refreshUsageSummary();
  } catch {
    // Usage visibility must never break the task UI.
  }
}

function renderMessageUsage(message) {
  const summary = message?.usageSummary;
  if (!summary || !Number(summary.llm_calls || 0)) return '';
  return `<div class="message-usage">Использование: ${escapeHTML(renderUsageSummaryLine(summary))}</div>`;
}

function bindUsageBadge() {
  const badge = document.getElementById('usageBadge');
  if (!badge || badge.dataset.bound === '1') return;
  badge.dataset.bound = '1';
  badge.addEventListener('click', () => {
    const expanded = badge.getAttribute('aria-expanded') === 'true';
    badge.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    badge.classList.toggle('open', !expanded);
    if (!expanded) refreshUsageSummary();
  });
  document.addEventListener('click', (event) => {
    if (!badge.contains(event.target)) {
      badge.classList.remove('open');
      badge.setAttribute('aria-expanded', 'false');
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  bindUsageBadge();
});
