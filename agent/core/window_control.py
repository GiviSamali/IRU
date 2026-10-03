"""Bounded window capability. Only this adapter decides which native APIs to call."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import os
import threading
import time
import uuid
from contextlib import contextmanager

ACTIONS = frozenset({'active', 'list', 'find', 'monitors', 'activate', 'minimize', 'maximize', 'restore', 'close', 'move', 'resize', 'left', 'right', 'move_monitor'})
SILENT = ACTIONS - {'active', 'list', 'find', 'monitors', 'close'}
ALIASES = {
    'word': {'winword.exe'}, 'ворд': {'winword.exe'}, 'excel': {'excel.exe'}, 'эксель': {'excel.exe'}, 'блокнот': {'notepad.exe'},
    'gpt': {'chatgpt.exe'}, 'chatgpt': {'chatgpt.exe'}, 'чатгпт': {'chatgpt.exe'},
    'проводник': {'explorer.exe'}, 'explorer': {'explorer.exe'},
    'notepad': {'notepad.exe'}, 'vs code': {'code.exe'}, 'vscode': {'code.exe'},
    'браузер': {'chrome.exe', 'msedge.exe', 'firefox.exe', 'comet.exe', 'browser.exe', 'opera.exe', 'brave.exe'},
    'browser': {'chrome.exe', 'msedge.exe', 'firefox.exe', 'comet.exe', 'browser.exe', 'opera.exe', 'brave.exe'},
}

class WindowsAdapter:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('unsupported_platform')
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.d = ctypes.WinDLL('dwmapi', use_last_error=True)
        self.enum_window = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.enum_monitor = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)
        signatures = {
            'GetForegroundWindow': ([], W.HWND), 'IsWindow': ([W.HWND], W.BOOL),
            'IsWindowVisible': ([W.HWND], W.BOOL), 'IsIconic': ([W.HWND], W.BOOL),
            'IsZoomed': ([W.HWND], W.BOOL), 'GetWindowTextW': ([W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClassNameW': ([W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetWindowThreadProcessId': ([W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            'GetWindowRect': ([W.HWND, ctypes.POINTER(W.RECT)], W.BOOL),
            'GetWindowLongW': ([W.HWND, ctypes.c_int], W.LONG),
            'EnumWindows': ([self.enum_window, W.LPARAM], W.BOOL),
            'EnumDisplayMonitors': ([W.HDC, ctypes.POINTER(W.RECT), self.enum_monitor, W.LPARAM], W.BOOL),
            'GetMonitorInfoW': ([W.HMONITOR, ctypes.c_void_p], W.BOOL),
            'MonitorFromWindow': ([W.HWND, W.DWORD], W.HMONITOR),
            'ShowWindowAsync': ([W.HWND, ctypes.c_int], W.BOOL),
            'SetThreadDpiAwarenessContext': ([W.HANDLE], W.HANDLE),
            'SetForegroundWindow': ([W.HWND], W.BOOL),
            'SetWindowPos': ([W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT], W.BOOL),
            'PostMessageW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.u, name); fn.argtypes = args; fn.restype = result
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]; self.k.CloseHandle.restype = W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.d.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
        self.d.DwmGetWindowAttribute.restype = ctypes.c_long

    @contextmanager
    def coordinates(self):
        previous = self.u.SetThreadDpiAwarenessContext(W.HANDLE(-4))  # Per-monitor aware V2
        try:
            yield
        finally:
            if previous: self.u.SetThreadDpiAwarenessContext(previous)

    def foreground(self):
        return int(self.u.GetForegroundWindow() or 0)

    def pid(self, handle):
        pid = W.DWORD(); self.u.GetWindowThreadProcessId(handle, ctypes.byref(pid)); return pid.value

    def record(self, handle):
        if not self.u.IsWindow(handle): return None
        title = ctypes.create_unicode_buffer(1024); cls = ctypes.create_unicode_buffer(256)
        self.u.GetWindowTextW(handle, title, len(title)); self.u.GetClassNameW(handle, cls, len(cls))
        rect = W.RECT()
        if not self.u.GetWindowRect(handle, ctypes.byref(rect)): return None
        pid = self.pid(handle); process = ''
        ph = self.k.OpenProcess(0x1000, False, pid)
        if ph:
            try:
                buf = ctypes.create_unicode_buffer(32768); length = W.DWORD(len(buf))
                if self.k.QueryFullProcessImageNameW(ph, 0, buf, ctypes.byref(length)): process = os.path.basename(buf.value)
            finally: self.k.CloseHandle(ph)
        return {'handle': int(handle), 'pid': pid, 'title': title.value, 'class_name': cls.value,
                'process_name': process, 'minimized': bool(self.u.IsIconic(handle)),
                'maximized': bool(self.u.IsZoomed(handle)), 'foreground': handle == self.foreground(),
                'bounds': {'left': rect.left, 'top': rect.top, 'right': rect.right, 'bottom': rect.bottom}}

    def windows(self):
        result = []
        @self.enum_window
        def visit(handle, _):
            if len(result) >= 200: return False
            if not self.u.IsWindowVisible(handle) or self.u.GetWindowLongW(handle, -20) & 0x80: return True
            cloaked = W.DWORD()
            if self.d.DwmGetWindowAttribute(handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value: return True
            row = self.record(handle)
            if row and row['title'] and row['class_name'] not in {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'}: result.append(row)
            return True
        self.u.EnumWindows(visit, 0)
        return result

    def monitors(self):
        class Info(ctypes.Structure):
            _fields_ = [('cbSize', W.DWORD), ('rcMonitor', W.RECT), ('rcWork', W.RECT), ('dwFlags', W.DWORD)]
        result = []
        @self.enum_monitor
        def visit(handle, _dc, _rect, _data):
            info = Info(); info.cbSize = ctypes.sizeof(info)
            if self.u.GetMonitorInfoW(handle, ctypes.byref(info)):
                r = info.rcWork
                result.append({'handle': int(handle), 'primary': bool(info.dwFlags & 1),
                    'work_area': {'left': r.left, 'top': r.top, 'right': r.right, 'bottom': r.bottom}})
            return True
        self.u.EnumDisplayMonitors(None, None, visit, 0)
        result.sort(key=lambda m: (not m['primary'], m['work_area']['left'], m['work_area']['top']))
        for index, monitor in enumerate(result, 1): monitor['monitor'] = index
        return result

    def monitor_for(self, handle): return int(self.u.MonitorFromWindow(handle, 2) or 0)

    def perform(self, handle, action, rect=None):
        if action in {'minimize', 'maximize', 'restore'}:
            self.u.ShowWindowAsync(handle, {'minimize': 6, 'maximize': 3, 'restore': 9}[action]); return True
        if action == 'activate':
            if self.u.IsIconic(handle): self.u.ShowWindowAsync(handle, 9)
            return bool(self.u.SetForegroundWindow(handle))
        if action == 'close': return bool(self.u.PostMessageW(handle, 0x0010, 0, 0))  # WM_CLOSE, never terminate
        self.u.ShowWindowAsync(handle, 9)
        return bool(self.u.SetWindowPos(handle, None, rect['left'], rect['top'], rect['right']-rect['left'], rect['bottom']-rect['top'], 0x4014))

class WindowControl:
    def __init__(self, adapter):
        self.adapter = adapter
        self.ids = {}
        self.last = None
        self.lock = threading.RLock()

    def public(self, row):
        key = (row['handle'], row['pid'])
        if key not in self.ids: self.ids[key] = uuid.uuid4().hex
        return {**{k: v for k, v in row.items() if k != 'handle'}, 'window_id': self.ids[key]}

    def run(self, action, target='current', title=None, pid=None, window_id=None, x=None, y=None, width=None, height=None, monitor=None):
        with self.lock:
            if action not in ACTIONS: return self.failure('invalid_action')
            if not isinstance(target, str) or not target.strip() or len(target) > 256 or (title is not None and (not isinstance(title, str) or not title or len(title) > 512)):
                return self.failure('invalid_target')
            if window_id is not None and (not isinstance(window_id, str) or len(window_id) != 32): return self.failure('invalid_window_id')
            for value in (pid, x, y, width, height, monitor):
                if value is not None and (type(value) is not int or abs(value) > 2**31-1): return self.failure('invalid_parameters')
            if (pid is not None and pid <= 0) or (monitor is not None and monitor <= 0): return self.failure('invalid_parameters')
            rows = self.adapter.windows()
            keys = {(r['handle'], r['pid']) for r in rows}
            self.ids = {key: value for key, value in self.ids.items() if key in keys}
            monitors = self.adapter.monitors()
            if action == 'monitors': return {'status': 'success', 'monitors': [{k:v for k,v in m.items() if k != 'handle'} for m in monitors]}
            if action == 'list': return {'status': 'success', 'windows': [self.public(r) for r in rows]}
            if action == 'active':
                rows = [r for r in rows if r['handle'] == self.adapter.foreground()]
            elif window_id:
                rows = [r for r in rows if self.ids.get((r['handle'], r['pid'])) == window_id]
            elif target.lower() in {'current', 'active'} and not title and not pid:
                rows = [r for r in rows if r['handle'] == self.adapter.foreground()]
            elif target.lower() == 'last' and not title and not pid:
                rows = [r for r in rows if (r['handle'], r['pid']) == self.last]
            else:
                name = target.lower().strip()
                if name not in {'current', 'active'}:
                    processes = ALIASES.get(name, {name if name.endswith('.exe') else name + '.exe'})
                    by_process = [r for r in rows if r['process_name'].lower() in processes]
                    rows = by_process if by_process or name in ALIASES else [r for r in rows if name in r['title'].lower()]
                if title: rows = [r for r in rows if title.lower() in r['title'].lower()]
                if pid: rows = [r for r in rows if r['pid'] == pid]
            if not rows: return self.failure('window_not_found')
            if len(rows) != 1:
                return {'status': 'ambiguous', 'error': 'ambiguous_window', 'candidates': [self.public(r) for r in rows[:20]], 'summary': 'Найдено несколько окон. Уточните заголовок нужного окна.'}
            row = rows[0]; handle = row['handle']
            if action in {'active', 'find'}: return {'status': 'success', 'window': self.public(row)}
            fresh = self.adapter.record(handle)
            if not fresh or fresh['pid'] != row['pid']: return self.failure('window_changed')
            bounds = dict(fresh['bounds']); rect = None
            if action in {'left', 'right', 'move_monitor'}:
                selected = next((m for m in monitors if m['monitor'] == monitor), None) if monitor else next((m for m in monitors if m['handle'] == self.adapter.monitor_for(handle)), None)
                if not selected or (action == 'move_monitor' and monitor is None): return self.failure('monitor_not_found')
                area = selected['work_area']; aw = area['right']-area['left']; ah = area['bottom']-area['top']
                if action in {'left', 'right'}:
                    mid = area['left'] + aw//2
                    rect = {**area, 'left': area['left'] if action == 'left' else mid, 'right': mid if action == 'left' else area['right']}
                else:
                    w = min(bounds['right']-bounds['left'], aw); h = min(bounds['bottom']-bounds['top'], ah)
                    rect = {'left': area['left'], 'top': area['top'], 'right': area['left']+w, 'bottom': area['top']+h}
            elif action == 'move':
                if x is None or y is None: return self.failure('coordinates_required')
                rect = {'left': x, 'top': y, 'right': x+bounds['right']-bounds['left'], 'bottom': y+bounds['bottom']-bounds['top']}
            elif action == 'resize':
                if width is None or height is None or not 1 <= width <= 32768 or not 1 <= height <= 32768: return self.failure('invalid_size')
                rect = {**bounds, 'right': bounds['left']+width, 'bottom': bounds['top']+height}
            if not self.adapter.perform(handle, action, rect): return self.failure('native_action_failed')
            deadline = time.monotonic() + 0.75
            while True:
                updated = self.adapter.record(handle)
                verified = (not updated) if action == 'close' else bool(updated and updated['pid'] == row['pid'] and (
                    updated['minimized'] if action == 'minimize' else updated['maximized'] and not updated['minimized'] if action == 'maximize' else
                    not updated['minimized'] and (fresh['minimized'] or not updated['maximized']) if action == 'restore' else
                    self.adapter.foreground() == handle if action == 'activate' else updated['bounds'] == rect))
                if verified or time.monotonic() >= deadline: break
                time.sleep(0.03)
            if not verified:
                if action == 'close': return {'status': 'pending', 'completion_state': 'pending', 'summary': 'Запрос закрытия отправлен. Окно остаётся открытым: возможно, требуется сохранить документ.', 'window': self.public(row)}
                return self.failure('action_not_verified')
            self.last = (handle, row['pid'])
            labels = {'minimize':'свёрнуто', 'maximize':'развёрнуто', 'restore':'восстановлено', 'activate':'активировано', 'left':'слева', 'right':'справа', 'move':'перемещено', 'resize':'размер изменён', 'move_monitor':'на другом мониторе', 'close':'закрыто'}
            return {'status': 'success', 'action': action, 'window': self.public(updated or row), 'completion_state': 'success',
                    'summary': f"{row['title']} → {labels[action]}", 'response_policy': 'silent_on_success' if action in SILENT else 'normal'}

    @staticmethod
    def failure(reason): return {'status': 'failed', 'error': reason, 'summary': 'Операция с окном не выполнена: ' + reason}

_control = None
_init_lock = threading.Lock()
def window_control(**params):
    global _control
    try:
        with _init_lock:
            if _control is None: _control = WindowControl(WindowsAdapter())
        with _control.adapter.coordinates():
            return _control.run(**params)
    except TypeError:
        return WindowControl.failure('invalid_parameters')
    except Exception:
        return WindowControl.failure('unsupported_platform' if os.name != 'nt' else 'native_api_error')
