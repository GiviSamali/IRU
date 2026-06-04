function bindOnce(id, eventName, handler) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener(eventName, handler);
}

function bindStaticEvents() {
  if (window.__iruStaticEventsBound) return;
  window.__iruStaticEventsBound = true;

  bindOnce('authBtn', 'click', doAuth);
  bindOnce('authInput', 'keydown', (event) => {
    if (event.key === 'Enter') doAuth();
  });

  bindOnce('btnNewChat', 'click', createNewChat);
  bindOnce('btnLogout', 'click', doLogout);
  bindOnce('sidebarOverlay', 'click', closeMobileSidebar);
  bindOnce('btnMenuMobile', 'click', toggleMobileSidebar);
  bindOnce('btnAdmin', 'click', toggleAdmin);
  bindOnce('memoryBadge', 'click', toggleMemoryPanel);
  bindOnce('memoryPanelCloseBtn', 'click', closeMemoryPanel);
  bindOnce('memoryFactAddBtn', 'click', addMemoryFactFromPanel);
  bindOnce('memoryFactInput', 'keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') addMemoryFactFromPanel();
  });
  bindOnce('deviceBtn', 'click', toggleDeviceDropdown);
  bindOnce('devModeToggle', 'click', toggleDevMode);
  bindOnce('explorerToggle', 'click', toggleExplorer);
  bindOnce('mobilePlusBtn', 'click', toggleMobilePlusPopover);
  bindOnce('mobilePlusDeviceAction', 'click', () => {
    toggleInputDeviceDropdown();
    closeMobilePlusPopover();
  });
  bindOnce('mobilePlusModeAction', 'click', () => {
    toggleInputModeDropdown();
    closeMobilePlusPopover();
  });
  bindOnce('mobilePlusAttachAction', 'click', () => {
    document.getElementById('fileInput').click();
    closeMobilePlusPopover();
  });
  bindOnce('inputDeviceBtn', 'click', toggleInputDeviceDropdown);
  bindOnce('inputModeBtn', 'click', toggleInputModeDropdown);
  bindOnce('modePipeline', 'change', (event) => setMode('pipeline', event.target.checked));
  bindOnce('modeAutonomous', 'change', (event) => setMode('autonomous', event.target.checked));
  bindOnce('btnSend', 'click', sendMessage);

  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.addEventListener('keydown', handleInputKey);
    chatInput.addEventListener('input', function handleChatInput() {
      autoGrow(this);
      updateCharCount();
    });
  }

  bindOnce('explorerCloseBtn', 'click', toggleExplorer);
  bindOnce('explorerBackBtn', 'click', explorerBack);
  bindOnce('explorerUpBtn', 'click', explorerUp);
  bindOnce('explorerRefreshBtn', 'click', explorerRefresh);

  bindOnce('devModeBroadcast', 'change', toggleDevBroadcast);
  bindOnce('devModeCloseBtn', 'click', toggleDevMode);
  bindOnce('devModeSendBtn', 'click', sendDevCommand);
  const devModeInput = document.getElementById('devModeInput');
  if (devModeInput) {
    devModeInput.addEventListener('keydown', handleDevModeKey);
    devModeInput.addEventListener('input', function handleDevModeInput() {
      autoGrow(this);
    });
  }

  bindOnce('tabBtnUsers', 'click', () => switchAdminTab('users'));
  bindOnce('tabBtnDevices', 'click', () => switchAdminTab('devices'));
  bindOnce('tabBtnAudit', 'click', () => switchAdminTab('audit'));
  bindOnce('adminCloseBtn', 'click', toggleAdmin);
  bindOnce('adminSearch', 'input', filterAdminUsers);
  bindOnce('adminCreateBtn', 'click', adminCreateUser);
  bindOnce('adminNewName', 'keydown', (event) => {
    if (event.key === 'Enter') adminCreateUser();
  });

  bindOnce('termsAcceptBtn', 'click', acceptTerms);
  bindOnce('consentAcceptBtn', 'click', () => setConsent(true));
  bindOnce('consentDeclineBtn', 'click', () => setConsent(false));

  document.querySelectorAll('[data-download-agent]').forEach((el) => {
    el.addEventListener('click', (event) => {
      event.preventDefault();
      downloadAgent();
    });
  });
}

function bootstrapCombatUI() {
  bindStaticEvents();
  renderInputModeBtn();
  updateCharCount();
  tryAutoLogin();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrapCombatUI, { once: true });
} else {
  bootstrapCombatUI();
}
