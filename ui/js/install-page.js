fetch('/api/agent/version')
  .then((response) => response.json())
  .then((data) => {
    const versionLabel = data.version ? `v${data.version}` : '';
    const versionNode = document.getElementById('agent-version');
    const footerVersionNode = document.getElementById('footer-version');

    if (versionNode) versionNode.textContent = versionLabel;
    if (footerVersionNode) footerVersionNode.textContent = versionLabel;

    const kind = data.kind || 'zip';
    const installBlock = document.getElementById(`install-steps-${kind}`);
    const needItem = document.getElementById(`need-item-${kind}`);

    if (installBlock) installBlock.hidden = false;
    if (needItem) needItem.hidden = false;
  })
  .catch(() => {
    const installBlock = document.getElementById('install-steps-zip');
    const needItem = document.getElementById('need-item-zip');
    if (installBlock) installBlock.hidden = false;
    if (needItem) needItem.hidden = false;
  });
