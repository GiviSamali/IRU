# IRU Combat UI Smoke Checklist

Use this checklist after each meaningful UI refactor step.

## Core Access

- [ ] Login with token
- [ ] Auto-login after refresh token reuse
- [ ] Logout returns to auth screen cleanly

## Chats

- [ ] Chat list loads
- [ ] Create new chat
- [ ] Open existing chat
- [ ] Delete chat
- [ ] Rename chat

## Devices And Send Flow

- [ ] Device list updates
- [ ] Select single device
- [ ] Switch to broadcast target
- [ ] Toggle `pipeline`
- [ ] Toggle `autonomous`
- [ ] Send a simple request

## Tasks / Pipeline

- [ ] Live task status updates
- [ ] Confirm-required flow renders buttons
- [ ] Confirm task works
- [ ] Deny task works
- [ ] Plan suggestion renders
- [ ] Run plan from suggestion works
- [ ] Decline plan suggestion continues same chat

## Panels

- [ ] Explorer opens
- [ ] Explorer navigates folders
- [ ] Explorer download link works
- [ ] Dev mode opens
- [ ] Dev mode runs raw command
- [ ] Admin users tab opens
- [ ] Admin devices tab opens
- [ ] Admin audit tab opens

## Auxiliary UX

- [ ] Voice button availability is correct
- [ ] Text attachments can be added and removed
- [ ] Memory badge appears when data exists
- [ ] Memory popover opens and closes
- [ ] Consent modal still works
- [ ] Terms modal still works

## Mobile / Responsive

- [ ] Sidebar opens on narrow viewport
- [ ] Mobile plus popover works
- [ ] Input row does not overlap controls
- [ ] Explorer/admin/dev panels open correctly on narrow viewport

## Static Pages

- [ ] `/about` loads
- [ ] `/instruction` loads
- [ ] `/terms` loads

## Technical Checks

- [ ] No JS syntax errors
- [ ] No duplicate device polling after repeated login/logout
- [ ] No duplicate static event bindings after repeated app activation
