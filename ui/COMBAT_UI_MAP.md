# IRU Combat UI Map

This document fixes the current combat UI map before refactor work.

## Runtime Context

- Public app root: `/`
- Static support pages: `/about`, `/instruction`, `/terms`
- Production assumption: the combat UI is already served behind the live domain and should behave like a public product surface, not a local-only panel.

## Main Files

- `ui/index.html`
  Primary shell markup for auth, chat, explorer, admin, dev mode, consent and terms modals.
- `ui/app.js`
  Thin bootstrap entrypoint after the refactor.
- `ui/js/core.js`
  Shared state, auth, token refresh, app session bootstrap.
- `ui/js/utils.js`
  Shared helpers: escaping, formatting, toast, link rendering.
- `ui/js/devices.js`
  Device polling, selectors, input mode state.
- `ui/js/chat.js`
  Chat list, messages, send flow, task polling, plan suggestion rendering.
- `ui/js/explorer.js`
  File explorer and download/open helpers.
- `ui/js/shell.js`
  Shell helpers: mobile sidebar, consent, terms, user capability refresh.
- `ui/js/admin.js`
  Admin users/devices/audit panels.
- `ui/js/devmode.js`
  Raw command panel.
- `ui/js/voice.js`
  Speech recognition integration.
- `ui/js/attachments.js`
  File attachment intake and rendering.
- `ui/js/extras.js`
  Memory badge, rename flow, plan actions, viewport/mobile helpers.
- `ui/style.css`
  Import hub for combat UI CSS sections.
- `ui/css/base.css`
  Tokens, auth, layout, header, chat base, shared controls.
- `ui/css/workspace.css`
  Input controls, explorer, device dropdowns, admin panel core.
- `ui/css/surfaces.css`
  Audit, device cards, modals, dev mode, live task UI, voice, attachments, memory, mobile rules.

## Responsibility Map

### Auth / Session

- Login via token
- JWT refresh
- Auto-login
- Logout
- Initial app activation

### Chat Workspace

- Chat list load/create/open/delete/rename
- Message render pipeline
- Live task polling
- Confirm / deny flow
- Plan suggestion flow

### Device Control

- Device polling
- Header device selector
- Input target selector
- Input execution modes: `pipeline`, `autonomous`

### Secondary Panels

- Explorer
- Dev mode
- Admin users / devices / audit

### Auxiliary UX

- Voice input
- File attachments
- Memory badge / popover
- Mobile sidebar / popovers
- Consent modal
- Terms modal

## DOM Contracts To Preserve

These IDs/classes are used across JS and should remain stable during the first iteration:

- `authScreen`, `appRoot`, `authInput`, `authBtn`, `authError`
- `chatList`, `chatMessages`, `headerTitle`, `userName`
- `deviceBtn`, `deviceDropdown`, `deviceList`, `deviceLabel`, `deviceDot`
- `inputDeviceBtn`, `inputDeviceDropdown`, `inputDeviceLabel`, `inputDeviceDot`
- `inputModeBtn`, `inputModeDropdown`, `inputModeBadges`, `modePipeline`, `modeAutonomous`
- `chatInput`, `charCount`, `btnSend`, `voiceBtn`, `attachBtn`, `fileInput`, `attachmentsBar`
- `explorerPanel`, `explorerPath`, `explorerList`
- `devModePanel`, `devModeInput`, `devModeOutput`, `devModeDeviceSelect`, `devModeBroadcast`
- `adminPanel`, `adminList`, `adminSearch`, `adminNewName`, `adminStats`, `adminDevicesList`, `auditLogList`
- `memoryBadge`, `memoryBadgeText`, `memoryPopover`, `memoryPopoverList`
- `termsModal`, `consentModal`, `toast`

## Known Legacy Risks

- Dynamic message HTML still uses inline `onclick` handlers for some message/explorer/admin subcontrols.
- Some privilege visibility still relies on fallback checks when backend role fields are missing.
- The combat UI remains intentionally framework-free in this iteration.
