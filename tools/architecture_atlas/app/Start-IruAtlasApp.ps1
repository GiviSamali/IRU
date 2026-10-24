param(
    [switch]$NoWatch
)

$ErrorActionPreference = 'Stop'

$Url        = 'http://127.0.0.1:8766'
$Atlas      = 'C:\Users\russa\OneDrive\Desktop\IRU\tools\architecture_atlas'
$ProfileDir = Join-Path $env:LOCALAPPDATA 'IRU\atlas-app-profile'
$Edge       = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$LogDir     = Join-Path $env:LOCALAPPDATA 'IRU\logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$Log = Join-Path $LogDir 'atlas-app.log'

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Test-Server {
    try { return ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) } catch { return $false }
}

function Start-Server {
    $node = $null
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) { $node = $cmd.Source }
    if (-not $node) {
        foreach ($c in @(
            "$env:ProgramFiles\nodejs\node.exe",
            "$env:LOCALAPPDATA\Programs\nodejs\node.exe",
            "C:\Program Files\nodejs\node.exe"
        )) { if (Test-Path $c) { $node = $c; break } }
    }
    if (-not $node) { Write-Log 'server start failed: node.exe not found'; return $false }
    Start-Process -FilePath $node -ArgumentList 'scripts/serve.mjs' -WorkingDirectory $Atlas -WindowStyle Hidden
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Server) { Write-Log 'server started'; return $true }
    }
    Write-Log 'server did not answer in time'
    return $false
}

if (-not ('TopWin' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class TopWin {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")]
    public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
    public const int GWL_EXSTYLE = -20;
    public const int WS_EX_TOPMOST = 0x00000008;
    public const uint SWP_NOSIZE = 0x0001;
    public const uint SWP_NOMOVE = 0x0002;
    public const uint SWP_NOACTIVATE = 0x0010;
    public const uint SWP_SHOWWINDOW = 0x0040;
    public static bool IsTopmost(IntPtr h) {
        if (h == IntPtr.Zero) return false;
        return (GetWindowLong(h, GWL_EXSTYLE) & WS_EX_TOPMOST) != 0;
    }
    public static bool Pin(IntPtr h) {
        if (h == IntPtr.Zero) return false;
        return SetWindowPos(h, new IntPtr(-1), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
    }
}
"@
}

function Get-AtlasWindows {
    $result = @()
    $procs = Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -like '*atlas-app-profile*') {
            $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
            if ($gp -and $gp.MainWindowHandle -ne 0) { $result += $gp }
        }
    }
    if ($result.Count -eq 0) {
        foreach ($gp in (Get-Process -Name msedge -ErrorAction SilentlyContinue)) {
            if ($gp.MainWindowHandle -ne 0 -and $gp.MainWindowTitle -like '*IRU*') { $result += $gp }
        }
    }
    return $result
}

if (-not (Test-Server)) {
    Write-Log 'atlas server not reachable, trying to start'
    if (-not (Start-Server)) { Write-Log 'giving up: server unavailable' }
}

$existing = Get-AtlasWindows
if ($existing.Count -eq 0) {
    $argLine = '--app={0} --user-data-dir="{1}" --no-first-run --no-default-browser-check --window-size=1500,950' -f $Url, $ProfileDir
    Start-Process -FilePath $Edge -ArgumentList $argLine
    Write-Log 'edge app window requested'
} else {
    Write-Log 'atlas app window already open'
}

# wait for the window to appear
$win = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    $w = Get-AtlasWindows
    if ($w.Count -gt 0) { $win = $w[0]; break }
}
if (-not $win) { Write-Log 'window not found'; exit 1 }

[TopWin]::Pin($win.MainWindowHandle) | Out-Null
Write-Log ("pinned topmost pid={0} title='{1}'" -f $win.Id, $win.MainWindowTitle)

if ($NoWatch) { exit 0 }

$misses = 0
while ($true) {
    Start-Sleep -Milliseconds 800
    $w = Get-AtlasWindows
    if ($w.Count -eq 0) {
        $misses++
        if ($misses -ge 6) { Write-Log 'window closed, watcher exit'; break }
        continue
    }
    $misses = 0
    $h = $w[0].MainWindowHandle
    if (-not [TopWin]::IsTopmost($h)) {
        [TopWin]::Pin($h) | Out-Null
        Write-Log 'topmost re-applied'
    }
}
