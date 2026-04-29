function stripUtfPrefix(cmd) {
  return (cmd || '').replace(/^\s*\[Console\]::OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8;\s*\$OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8;\s*/i, '');
}
function escapeHTML(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }
function escapeAttr(s) { if (s == null) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function formatSize(b) {
  if (b == null) return '';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  if (b < 1073741824) return (b/1048576).toFixed(1) + ' MB';
  return (b/1073741824).toFixed(1) + ' GB';
}
function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => { t.className = 'toast'; }, 3000);
}
function linkify(text) {
  return text.replace(/(\/api\/download\/[a-f0-9-]+)/g, '<a href="$1" target="_blank">\ud83d\udce5 РЎРєР°С‡Р°С‚СЊ С„Р°Р№Р»</a>');
}

