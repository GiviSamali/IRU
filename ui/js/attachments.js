// ── Прикрепление текстовых файлов ───────────────────────────
const ALLOWED_EXT = new Set([
  'txt','md','csv','json','xml','yml','yaml','py','js','ts','html','css',
  'sql','log','ini','conf','sh','ps1','bat','go','rs','java','cpp','c','h'
]);
const MAX_FILE_SIZE = 500 * 1024;
const MAX_TOTAL_SIZE = 2 * 1024 * 1024;
const MAX_FILES = 5;

let attachedFiles = [];

const attachBtn = document.getElementById('attachBtn');
const fileInput = document.getElementById('fileInput');
const attachmentsBar = document.getElementById('attachmentsBar');

if (attachBtn && fileInput) {
  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    handleFiles(Array.from(e.target.files));
    fileInput.value = '';
  });
}

// Drag & drop
const _dragTarget = document.body;
['dragenter', 'dragover'].forEach(evt => {
  _dragTarget.addEventListener(evt, (e) => {
    if (e.dataTransfer && e.dataTransfer.types.includes('Files')) {
      e.preventDefault();
      _dragTarget.classList.add('drag-over');
    }
  });
});
['dragleave', 'drop'].forEach(evt => {
  _dragTarget.addEventListener(evt, (e) => {
    if (evt === 'drop') {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files) {
        handleFiles(Array.from(e.dataTransfer.files));
      }
    }
    _dragTarget.classList.remove('drag-over');
  });
});

// Paste файлов в поле ввода
(function() {
  const ci = document.getElementById('chatInput');
  if (ci) {
    ci.addEventListener('paste', (e) => {
      if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length) {
        e.preventDefault();
        handleFiles(Array.from(e.clipboardData.files));
      }
    });
  }
})();

function handleFiles(files) {
  for (const file of files) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!ALLOWED_EXT.has(ext)) {
      alert(`Файл "${file.name}" не поддерживается. Принимаются только текстовые файлы (.txt, .md, .csv, .json, .py и т.п.)`);
      continue;
    }
    if (file.size > MAX_FILE_SIZE) {
      alert(`Файл "${file.name}" слишком большой (${Math.round(file.size/1024)} КБ). Максимум 500 КБ.`);
      continue;
    }
    if (attachedFiles.length >= MAX_FILES) {
      alert(`Можно прикрепить максимум ${MAX_FILES} файлов.`);
      return;
    }
    const totalSize = attachedFiles.reduce((s, f) => s + f.size, 0);
    if (totalSize + file.size > MAX_TOTAL_SIZE) {
      alert('Суммарный размер файлов превысит 2 МБ. Удалите ненужные.');
      return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
      attachedFiles.push({ name: file.name, size: file.size, content: ev.target.result });
      renderAttachments();
    };
    reader.onerror = () => { alert(`Не удалось прочитать файл "${file.name}"`); };
    reader.readAsText(file, 'UTF-8');
  }
}

function renderAttachments() {
  if (!attachmentsBar) return;
  if (attachedFiles.length === 0) {
    attachmentsBar.hidden = true;
    attachmentsBar.innerHTML = '';
    return;
  }
  attachmentsBar.hidden = false;
  attachmentsBar.innerHTML = attachedFiles.map((f, idx) => `
    <div class="attachment-chip">
      <span class="chip-name" title="${escapeHTML(f.name)}">${escapeHTML(f.name)}</span>
      <span class="chip-size">${formatAttachSize(f.size)}</span>
      <button class="chip-remove" data-idx="${idx}" aria-label="Удалить">\u00d7</button>
    </div>
  `).join('');
  attachmentsBar.querySelectorAll('.chip-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      attachedFiles.splice(parseInt(e.target.dataset.idx, 10), 1);
      renderAttachments();
    });
  });
}

function formatAttachSize(bytes) {
  if (bytes < 1024) return bytes + ' Б';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' КБ';
  return (bytes / 1024 / 1024).toFixed(2) + ' МБ';
}

function buildMessageWithAttachments(userText) {
  if (attachedFiles.length === 0) return userText;
  const parts = ['=== Прикреплённые файлы ==='];
  for (const f of attachedFiles) {
    parts.push(`\n[${f.name}, ${f.size} байт]`);
    parts.push(f.content);
  }
  parts.push('\n=== Сообщение ===');
  parts.push(userText);
  return parts.join('\n');
}

function clearAttachments() {
  attachedFiles = [];
  renderAttachments();
}

// ── MOBILE PLUS POPOVER (Point 6) ─────────────────────────────
