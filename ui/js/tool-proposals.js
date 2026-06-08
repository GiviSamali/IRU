function formatToolProposalDate(value) {
  if (!value) return '—';
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function parseCsvList(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function safeJsonBlock(value) {
  const text = JSON.stringify(value ?? {}, null, 2);
  return `<pre class="tool-proposal-json">${escapeHTML(text)}</pre>`;
}

function selectedToolProposal() {
  return (state.toolProposals || []).find((item) => String(item.id) === String(state.selectedToolProposalId)) || null;
}

function renderToolProposalBadge(value) {
  const text = value || 'unknown';
  return `<span class="tool-proposal-badge status-${escapeAttr(text)}">${escapeHTML(text)}</span>`;
}

function renderToolProposalsPanel() {
  const panel = document.getElementById('toolProposalsPanel');
  const button = document.getElementById('toolProposalsToggle');
  const list = document.getElementById('toolProposalsList');
  const detail = document.getElementById('toolProposalDetail');
  const error = document.getElementById('toolProposalsError');
  if (panel) panel.classList.toggle('open', !!state.toolProposalsOpen);
  if (button) {
    button.classList.toggle('active', !!state.toolProposalsOpen);
    button.setAttribute('aria-expanded', state.toolProposalsOpen ? 'true' : 'false');
  }
  if (error) {
    error.hidden = !state.toolProposalsError;
    error.textContent = state.toolProposalsError || '';
  }
  if (!list || !detail) return;

  if (state.toolProposalsLoading) {
    list.innerHTML = '<div class="tool-proposals-state">Кандидаты загружаются...</div>';
  } else {
    const proposals = state.toolProposals || [];
    if (!proposals.length) {
      list.innerHTML = '<div class="tool-proposals-state">Кандидатов пока нет. Создайте их через чат или форму ниже.</div>';
    } else {
      list.innerHTML = proposals.map((proposal) => {
        const active = String(proposal.id) === String(state.selectedToolProposalId) ? ' active' : '';
        return `<button type="button" class="tool-proposal-list-item${active}" data-action="select-tool-proposal" data-id="${escapeAttr(proposal.id)}">
          <div class="tool-proposal-list-main">
            <strong>${escapeHTML(proposal.name || 'tool.proposal')}</strong>
            <span>${escapeHTML(proposal.title || proposal.purpose || '')}</span>
          </div>
          <div class="tool-proposal-list-meta">
            ${renderToolProposalBadge(proposal.status)}
            <span>${escapeHTML(proposal.priority || 'normal')}</span>
            <span>${escapeHTML(proposal.risk_level || 'safe')}</span>
            <span>${escapeHTML(formatToolProposalDate(proposal.created_at))}</span>
          </div>
        </button>`;
      }).join('');
    }
  }

  const proposal = selectedToolProposal();
  if (!proposal) {
    detail.innerHTML = '<div class="tool-proposals-state">Выберите кандидата из списка.</div>';
    return;
  }

  detail.innerHTML = `
    <div class="tool-proposal-detail-head">
      <div>
        <h4>${escapeHTML(proposal.name || '')}</h4>
        <p>${escapeHTML(proposal.title || '')}</p>
      </div>
      ${renderToolProposalBadge(proposal.status)}
    </div>
    <div class="tool-proposal-detail-grid">
      <div><span>Статус</span>${escapeHTML(proposal.status || '—')}</div>
      <div><span>Приоритет</span>${escapeHTML(proposal.priority || 'normal')}</div>
      <div><span>Риск</span>${escapeHTML(proposal.risk_level || 'safe')}</div>
      <div><span>Категория</span>${escapeHTML(proposal.category || 'tooling')}</div>
    </div>
    <section><h5>Проблема</h5><p>${escapeHTML(proposal.problem || '—')}</p></section>
    <section><h5>Назначение</h5><p>${escapeHTML(proposal.purpose || '—')}</p></section>
    <section><h5>Права</h5><p>${escapeHTML((proposal.permissions || []).join(', ') || '—')}</p></section>
    <section><h5>Input schema</h5>${safeJsonBlock(proposal.input_schema || {})}</section>
    <section><h5>Output schema</h5>${safeJsonBlock(proposal.output_schema || {})}</section>
    <section><h5>Evidence contract</h5>${safeJsonBlock(proposal.evidence_contract || {})}</section>
    <section><h5>Side effects</h5><p>${escapeHTML((proposal.side_effects || []).join(', ') || '—')}</p></section>
    <section><h5>Idempotency / cleanup / rollback</h5><p>${escapeHTML([proposal.idempotency, proposal.cleanup, proposal.rollback].filter(Boolean).join(' · ') || '—')}</p></section>
    <section><h5>Примеры</h5>${safeJsonBlock(proposal.examples || [])}</section>
    <section><h5>Тест-план</h5><p>${escapeHTML((proposal.test_plan || []).join(' · ') || '—')}</p></section>
    <section>
      <h5>Заметки</h5>
      <textarea id="toolProposalNotesInput" class="tool-proposal-notes-input" rows="3">${escapeHTML(proposal.notes || '')}</textarea>
      <div class="tool-proposal-actions">
        <button type="button" class="tool-proposals-small-btn" data-action="save-tool-proposal-notes">Обновить заметки</button>
        <button type="button" class="tool-proposals-small-btn" data-action="set-tool-proposal-status" data-status="proposed">Вернуть в proposed</button>
        <button type="button" class="tool-proposals-small-btn danger" data-action="set-tool-proposal-status" data-status="rejected">Отклонить</button>
      </div>
    </section>
  `;
}

async function loadToolProposals() {
  state.toolProposalsLoading = true;
  state.toolProposalsError = '';
  renderToolProposalsPanel();
  try {
    const resp = await apiFetch(`${API}/api/tool-proposals`, { headers: authHeaders() });
    const data = await resp.json();
    if (!resp.ok || data.status !== 'ok') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    state.toolProposals = data.proposals || [];
    if (!state.selectedToolProposalId && state.toolProposals.length) {
      state.selectedToolProposalId = state.toolProposals[0].id;
    }
  } catch (e) {
    state.toolProposalsError = e.message || 'Ошибка загрузки кандидатов';
  } finally {
    state.toolProposalsLoading = false;
    renderToolProposalsPanel();
  }
}

function toggleToolProposalsPanel() {
  state.toolProposalsOpen = !state.toolProposalsOpen;
  renderToolProposalsPanel();
  if (state.toolProposalsOpen) loadToolProposals();
}

function closeToolProposalsPanel() {
  state.toolProposalsOpen = false;
  renderToolProposalsPanel();
}

async function createToolProposal(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = document.getElementById('toolProposalCreateBtn');
  const formData = new FormData(form);
  const payload = {
    name: formData.get('name'),
    title: formData.get('title'),
    problem: formData.get('problem'),
    purpose: formData.get('purpose'),
    category: formData.get('category') || 'tooling',
    risk_level: formData.get('risk_level') || 'write',
    priority: formData.get('priority') || 'normal',
    permissions: parseCsvList(formData.get('permissions')),
    input_schema: {},
    output_schema: {},
    evidence_contract: {},
    side_effects: [],
    examples: [],
    test_plan: [],
    notes: formData.get('notes') || '',
  };
  if (button) button.disabled = true;
  try {
    const resp = await apiFetch(`${API}/api/tool-proposals`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || data.status !== 'created') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    form.reset();
    state.selectedToolProposalId = data.proposal_id;
    showToast(`Кандидат ${data.name} создан`);
    await loadToolProposals();
  } catch (e) {
    showToast(e.message || 'Ошибка создания кандидата', true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function patchToolProposal(proposalId, payload) {
  const resp = await apiFetch(`${API}/api/tool-proposals/${encodeURIComponent(proposalId)}`, {
    method: 'PATCH',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok || data.status === 'error') {
    throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  }
  return data;
}

async function updateSelectedToolProposal(payload) {
  const proposal = selectedToolProposal();
  if (!proposal) return;
  try {
    await patchToolProposal(proposal.id, payload);
    await loadToolProposals();
    showToast('Кандидат обновлён');
  } catch (e) {
    showToast(e.message || 'Ошибка обновления кандидата', true);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('toolProposalsToggle')?.addEventListener('click', toggleToolProposalsPanel);
  document.getElementById('toolProposalsCloseBtn')?.addEventListener('click', closeToolProposalsPanel);
  document.getElementById('toolProposalsRefreshBtn')?.addEventListener('click', loadToolProposals);
  document.getElementById('toolProposalCreateForm')?.addEventListener('submit', createToolProposal);
});

document.addEventListener('click', (event) => {
  const select = event.target.closest('[data-action="select-tool-proposal"]');
  if (select) {
    state.selectedToolProposalId = select.dataset.id;
    renderToolProposalsPanel();
    return;
  }
  const status = event.target.closest('[data-action="set-tool-proposal-status"]');
  if (status) {
    updateSelectedToolProposal({
      status: status.dataset.status,
      notes: document.getElementById('toolProposalNotesInput')?.value || '',
    });
    return;
  }
  const notes = event.target.closest('[data-action="save-tool-proposal-notes"]');
  if (notes) {
    updateSelectedToolProposal({
      notes: document.getElementById('toolProposalNotesInput')?.value || '',
    });
  }
});
