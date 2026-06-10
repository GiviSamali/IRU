from __future__ import annotations

import base64
import hashlib
import json
import ntpath
import posixpath
import re
from typing import Any, Awaitable, Callable


PC_CORE_TOOL_NAMES = {
    "fs_resolve_path",
    "fs_open_folder",
    "fs_list_dir",
    "fs_stat",
    "fs_read_file",
    "fs_write_file",
    "fs_patch_file",
    "fs_rename",
    "fs_copy",
    "fs_move",
    "fs_delete",
    "app_open_file",
}

PC_CORE_NAME_ALIASES = {
    "fs.resolve_path": "fs_resolve_path",
    "fs.open_folder": "fs_open_folder",
    "fs.list_dir": "fs_list_dir",
    "fs.stat": "fs_stat",
    "fs.read_file": "fs_read_file",
    "fs.write_file": "fs_write_file",
    "fs.patch_file": "fs_patch_file",
    "fs.rename": "fs_rename",
    "fs.copy": "fs_copy",
    "fs.move": "fs_move",
    "fs.delete": "fs_delete",
    "app.open_file": "app_open_file",
}
PC_CORE_TOOL_NAMES = PC_CORE_TOOL_NAMES | set(PC_CORE_NAME_ALIASES)

PC_CORE_CANONICAL_NAMES = {
    "fs.resolve_path",
    "fs.open_folder",
    "fs.list_dir",
    "fs.stat",
    "fs.read_file",
    "fs.write_file",
    "fs.patch_file",
    "fs.rename",
    "fs.copy",
    "fs.move",
    "fs.delete",
    "app.open_file",
}

ALIASES = {
    "downloads": "downloads",
    "загрузки": "downloads",
    "скачанные": "downloads",
    "папка загрузок": "downloads",
    "documents": "documents",
    "документы": "documents",
    "desktop": "desktop",
    "рабочий стол": "desktop",
    "home": "home",
    "профиль": "home",
    "пользователь": "home",
    "pictures": "pictures",
    "изображения": "pictures",
    "картинки": "pictures",
    "videos": "videos",
    "видео": "videos",
    "music": "music",
    "музыка": "music",
    "temp": "temp",
    "временная": "temp",
}

RISKY_WINDOWS_PREFIXES = (
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
)


SendCommandFn = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


def alias_key(path_or_alias: str) -> str | None:
    return ALIASES.get(str(path_or_alias or "").strip().lower())


def is_invalid_new_name(new_name: str) -> bool:
    value = str(new_name or "").strip()
    return not value or any(sep in value for sep in ("/", "\\")) or value in {".", ".."}


def is_risky_path(path: str) -> bool:
    value = str(path or "").strip().replace("/", "\\").lower()
    if re.fullmatch(r"[a-z]:\\?", value):
        return True
    return any(value == prefix or value.startswith(prefix + "\\") for prefix in RISKY_WINDOWS_PREFIXES)


def _b64_text(value: str) -> str:
    return base64.b64encode(str(value or "").encode("utf-8")).decode("ascii")


def _b64_json(value: Any) -> str:
    return _b64_text(json.dumps(value, ensure_ascii=False))


def _ps_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _ps_bool(value: Any) -> str:
    return "$true" if bool(value) else "$false"


async def _send(send_command_fn: SendCommandFn, device_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    result = send_command_fn(device_id, action, params)
    if hasattr(result, "__await__"):
        result = await result  # type: ignore[assignment]
    return result if isinstance(result, dict) else {"status": "error", "error": str(result)}


def _parse_stdout_json(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") in {"success", "ok"} and isinstance(result.get("resolved_path"), str):
        return result
    stdout = str(result.get("stdout") or "").strip()
    if not stdout:
        if result.get("error"):
            return {"status": "error", "error": result.get("error")}
        if result.get("stderr"):
            return {"status": "error", "error": result.get("stderr")}
        return {"status": "error", "error": "empty_tool_output"}
    try:
        return json.loads(stdout)
    except Exception:
        return {
            "status": "error",
            "error": "invalid_tool_output",
            "stdout_preview": stdout[:500],
            "returncode": result.get("returncode"),
            "stderr_preview": str(result.get("stderr") or "")[:500],
        }


def _resolve_script(path_or_alias: str, base_path: str | None, must_exist: bool, expected_type: str) -> str:
    aliases = {
        "downloads": "Downloads", "загрузки": "Downloads", "скачанные": "Downloads", "папка загрузок": "Downloads",
        "documents": "Documents", "документы": "Documents",
        "desktop": "Desktop", "рабочий стол": "Desktop",
        "pictures": "Pictures", "изображения": "Pictures", "картинки": "Pictures",
        "videos": "Videos", "видео": "Videos",
        "music": "Music", "музыка": "Music",
    }
    alias_json = json.dumps(aliases, ensure_ascii=False)
    base = base_path or ""
    return f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$inputValue = {_ps_str(path_or_alias)}
$basePath = {_ps_str(base)}
$mustExist = {_ps_bool(must_exist)}
$expectedType = {_ps_str(expected_type or 'any')}
$aliasesRaw = ConvertFrom-Json @'
{alias_json}
'@
$aliases = @{{}}
$aliasesRaw.PSObject.Properties | ForEach-Object {{ $aliases[$_.Name] = $_.Value }}
$trimmed = ($inputValue -as [string]).Trim()
$lower = $trimmed.ToLowerInvariant()
$source = 'relative'
if ($lower -eq 'home' -or $lower -eq 'профиль' -or $lower -eq 'пользователь') {{
  $resolved = [Environment]::GetFolderPath('UserProfile'); $source = 'alias'
}} elseif ($lower -eq 'temp' -or $lower -eq 'временная') {{
  $resolved = [IO.Path]::GetTempPath().TrimEnd('\\'); $source = 'alias'
}} elseif ($aliases.ContainsKey($lower)) {{
  $resolved = Join-Path ([Environment]::GetFolderPath('UserProfile')) $aliases[$lower]; $source = 'alias'
}} elseif ([IO.Path]::IsPathRooted($trimmed)) {{
  $resolved = [IO.Path]::GetFullPath($trimmed); $source = 'absolute'
}} else {{
  $root = if ($basePath) {{ $basePath }} else {{ (Get-Location).Path }}
  $resolved = [IO.Path]::GetFullPath((Join-Path $root $trimmed)); $source = 'relative'
}}
$exists = Test-Path -LiteralPath $resolved
$type = if ($exists) {{ if ((Get-Item -LiteralPath $resolved).PSIsContainer) {{ 'dir' }} else {{ 'file' }} }} else {{ 'missing' }}
$ok = (-not $mustExist -or $exists) -and ($expectedType -eq 'any' -or $type -eq $expectedType -or ($expectedType -eq 'dir' -and $type -eq 'dir') -or ($expectedType -eq 'file' -and $type -eq 'file'))
$status = if ($ok) {{ 'success' }} else {{ 'error' }}
[pscustomobject]@{{
  status=$status; input=$inputValue; resolved_path=$resolved; exists=$exists; type=$type; source=$source;
  expected_type=$expectedType; evidence="resolved_path=$resolved; exists=$exists; type=$type"
}} | ConvertTo-Json -Compress
"""


def _execute_ps(script: str, timeout: int = 20) -> dict[str, Any]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return {"command": f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}", "timeout": timeout}


async def resolve_path(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    script = _resolve_script(
        str(args.get("path_or_alias") or args.get("path") or ""),
        args.get("base_path"),
        bool(args.get("must_exist", False)),
        str(args.get("expected_type") or "any"),
    )
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 10)))


async def _stat_resolved(send_command_fn: SendCommandFn, device_id: str, resolved_path: str, expected_type: str = "any") -> dict[str, Any]:
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$path = {_ps_str(resolved_path)}
$exists = Test-Path -LiteralPath $path
$type = 'missing'; $size = $null; $created = $null; $modified = $null; $sha = $null
if ($exists) {{
  $item = Get-Item -LiteralPath $path
  $type = if ($item.PSIsContainer) {{ 'dir' }} else {{ 'file' }}
  $created = $item.CreationTimeUtc.ToString('o')
  $modified = $item.LastWriteTimeUtc.ToString('o')
  if (-not $item.PSIsContainer) {{
    $size = $item.Length
    if ($item.Length -le 5242880) {{ $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }}
  }}
}}
[pscustomobject]@{{status='success'; resolved_path=$path; exists=$exists; type=$type; size_bytes=$size; created_at=$created; modified_at=$modified; sha256=$sha; expected_type={_ps_str(expected_type)}}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 10)))


async def fs_stat(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    resolved = await resolve_path(send_command_fn, device_id, {**args, "must_exist": False})
    if resolved.get("status") == "error" and not resolved.get("resolved_path"):
        return resolved
    return await _stat_resolved(send_command_fn, device_id, str(resolved.get("resolved_path") or ""), str(args.get("expected_type") or "any"))


async def fs_list_dir(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 100), 500))
    offset = max(0, int(args.get("offset") or 0))
    resolved = await resolve_path(send_command_fn, device_id, {**args, "must_exist": True, "expected_type": "dir"})
    if resolved.get("status") == "error":
        return resolved
    path = str(resolved.get("resolved_path") or "")
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$path = {_ps_str(path)}
$filter = {_ps_str(str(args.get("filter") or "*"))}
$items = Get-ChildItem -LiteralPath $path -Force:{_ps_bool(args.get("include_hidden", False))} -Filter $filter
$total = @($items).Count
$selected = @($items | Select-Object -Skip {offset} -First {limit})
$rows = @($selected | ForEach-Object {{ [pscustomobject]@{{ name=$_.Name; path=$_.FullName; type=$(if ($_.PSIsContainer) {{'dir'}} else {{'file'}}); size_bytes=$(if ($_.PSIsContainer) {{$null}} else {{$_.Length}}); modified_at=$_.LastWriteTimeUtc.ToString('o') }} }})
[pscustomobject]@{{status='success'; resolved_path=$path; total_count=$total; returned_count=@($rows).Count; items=$rows; truncated=($total -gt ({offset}+{limit})); evidence="listed @($rows).Count of $total items from $path"}} | ConvertTo-Json -Compress -Depth 5
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 20)))


async def fs_read_file(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    max_chars = max(1, min(int(args.get("max_chars") or 20000), 100000))
    offset = max(0, int(args.get("offset") or 0))
    resolved = await resolve_path(send_command_fn, device_id, {**args, "path_or_alias": args.get("path") or args.get("path_or_alias"), "must_exist": True, "expected_type": "file"})
    if resolved.get("status") == "error":
        return resolved
    path = str(resolved.get("resolved_path") or "")
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$path = {_ps_str(path)}
$bytes = [IO.File]::ReadAllBytes($path)
$nul = $bytes | Where-Object {{ $_ -eq 0 }} | Select-Object -First 1
if ($nul -ne $null) {{
  [pscustomobject]@{{status='error'; error='binary_file_preview_rejected'; path=$path; resolved_path=$path; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()}} | ConvertTo-Json -Compress
  exit 0
}}
$encoding = {_ps_str(str(args.get("encoding") or "utf-8"))}
$text = [Text.Encoding]::UTF8.GetString($bytes)
$charsTotal = $text.Length
$slice = if ({offset} -lt $charsTotal) {{ $text.Substring({offset}, [Math]::Min({max_chars}, $charsTotal - {offset})) }} else {{ '' }}
[pscustomobject]@{{status='success'; path=$path; resolved_path=$path; content=$slice; chars=$slice.Length; truncated=(({offset} + {max_chars}) -lt $charsTotal); sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant(); encoding=$encoding; evidence="read $($slice.Length) chars from $path"}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 20)))


async def fs_write_file(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    path = str(args.get("path") or "")
    mode = str(args.get("mode") or "create_or_replace")
    backup = bool(args.get("backup", True))
    if mode not in {"create_only", "create_or_replace", "append"}:
        return {"status": "error", "error": "invalid_mode"}
    resolved = await resolve_path(send_command_fn, device_id, {"path_or_alias": path, "must_exist": False})
    target = str(resolved.get("resolved_path") or path)
    if is_risky_path(target):
        return {"status": "needs_confirmation", "reason": "risky_system_path", "resolved_path": target}
    content_b64 = _b64_text(str(args.get("content") or ""))
    append_mode = "$true" if mode == "append" else "$false"
    create_only = "$true" if mode == "create_only" else "$false"
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$path = {_ps_str(target)}
$exists = Test-Path -LiteralPath $path
if ({create_only} -and $exists) {{ [pscustomobject]@{{status='error'; error='file_exists'; path=$path}} | ConvertTo-Json -Compress; exit 0 }}
$parent = Split-Path -Parent $path
if ($parent -and -not (Test-Path -LiteralPath $parent)) {{ New-Item -ItemType Directory -Force -Path $parent | Out-Null }}
$backupPath = $null
if ($exists -and -not {append_mode} -and {_ps_bool(backup)}) {{ $backupPath = "$path.iru.bak.$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"; Copy-Item -LiteralPath $path -Destination $backupPath -Force }}
$content = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({_ps_str(content_b64)}))
if ({append_mode}) {{ [IO.File]::AppendAllText($path, $content, [Text.Encoding]::UTF8); $status='appended' }} else {{ [IO.File]::WriteAllText($path, $content, [Text.Encoding]::UTF8); $status='written' }}
$bytes = (Get-Item -LiteralPath $path).Length
[pscustomobject]@{{status=$status; path=$path; resolved_path=$path; bytes_written=$bytes; backup_path=$backupPath; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant(); terminal_sufficient=$true; completion_state='success'; evidence="$status $bytes bytes to $path"}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 30)))


async def fs_patch_file(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    resolved = await resolve_path(send_command_fn, device_id, {"path_or_alias": args.get("path"), "must_exist": True, "expected_type": "file"})
    if resolved.get("status") == "error":
        return resolved
    path = str(resolved.get("resolved_path") or "")
    if is_risky_path(path):
        return {"status": "needs_confirmation", "reason": "risky_system_path", "resolved_path": path}
    operations_b64 = _b64_json(args.get("operations") or [])
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$path = {_ps_str(path)}
$ops = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({_ps_str(operations_b64)})))
$text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
$beforeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
$new = $text
$applied = 0
foreach ($op in @($ops)) {{
  $kind = [string]$op.op
  if (-not $kind) {{ $kind = [string]$op.type }}
  if ($kind -eq 'replace') {{
    $find = [string]$op.find; $replace = [string]$op.replace
    if (-not $new.Contains($find)) {{ [pscustomobject]@{{status='error'; error='marker_not_found'; marker=$find; path=$path}} | ConvertTo-Json -Compress; exit 0 }}
    if ([string]$op.occurrence -eq 'all') {{ $count = ([regex]::Matches($new, [regex]::Escape($find))).Count; $new = $new.Replace($find,$replace); $applied += $count }} else {{ $idx=$new.IndexOf($find); $new=$new.Remove($idx,$find.Length).Insert($idx,$replace); $applied++ }}
  }} elseif ($kind -eq 'insert_before' -or $kind -eq 'insert_after') {{
    $find = [string]$op.find; $content = [string]$op.content
    $idx = $new.IndexOf($find)
    if ($idx -lt 0) {{ [pscustomobject]@{{status='error'; error='marker_not_found'; marker=$find; path=$path}} | ConvertTo-Json -Compress; exit 0 }}
    if ($kind -eq 'insert_after') {{ $idx += $find.Length }}
    $new = $new.Insert($idx, $content); $applied++
  }} elseif ($kind -eq 'append') {{
    $new = $new + [string]$op.content; $applied++
  }} elseif ($kind -eq 'delete_block') {{
    $start=[string]$op.start_marker; $end=[string]$op.end_marker; $s=$new.IndexOf($start); $e=$new.IndexOf($end, [Math]::Max(0,$s))
    if ($s -lt 0 -or $e -lt 0) {{ [pscustomobject]@{{status='error'; error='marker_not_found'; path=$path}} | ConvertTo-Json -Compress; exit 0 }}
    $new = $new.Remove($s, ($e + $end.Length) - $s); $applied++
  }}
}}
if ($new -eq $text) {{ [pscustomobject]@{{status='no_change'; path=$path; operations_applied=0; before_sha256=$beforeHash; after_sha256=$beforeHash; evidence="no change to $path"}} | ConvertTo-Json -Compress; exit 0 }}
$backupPath = $null
if ({_ps_bool(args.get("backup", True))}) {{ $backupPath = "$path.iru.bak.$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"; Copy-Item -LiteralPath $path -Destination $backupPath -Force }}
[IO.File]::WriteAllText($path, $new, [Text.Encoding]::UTF8)
$afterHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
[pscustomobject]@{{status='patched'; path=$path; resolved_path=$path; operations_applied=$applied; backup_path=$backupPath; before_sha256=$beforeHash; after_sha256=$afterHash; terminal_sufficient=$true; completion_state='success'; evidence="patched $applied operations in $path"}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 30)))


async def fs_rename(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    new_name = str(args.get("new_name") or "")
    if is_invalid_new_name(new_name):
        return {"status": "error", "error": "invalid_new_name"}
    resolved = await resolve_path(send_command_fn, device_id, {"path_or_alias": args.get("path"), "must_exist": True})
    if resolved.get("status") == "error":
        return resolved
    old_path = str(resolved.get("resolved_path") or "")
    if is_risky_path(old_path):
        return {"status": "needs_confirmation", "reason": "risky_system_path", "resolved_path": old_path}
    parent = ntpath.dirname(old_path) if "\\" in old_path else posixpath.dirname(old_path)
    new_path = ntpath.join(parent, new_name) if "\\" in old_path else posixpath.join(parent, new_name)
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$old={_ps_str(old_path)}; $new={_ps_str(new_path)}
if ((Test-Path -LiteralPath $new) -and -not {_ps_bool(args.get("overwrite", False))}) {{ [pscustomobject]@{{status='error'; error='destination_exists'; old_path=$old; new_path=$new}} | ConvertTo-Json -Compress; exit 0 }}
Move-Item -LiteralPath $old -Destination $new -Force:{_ps_bool(args.get("overwrite", False))}
[pscustomobject]@{{status='renamed'; old_path=$old; new_path=$new; terminal_sufficient=$true; completion_state='success'; evidence="renamed $old to $new"}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 20)))


async def _copy_or_move(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any], *, move: bool) -> dict[str, Any]:
    source = await resolve_path(send_command_fn, device_id, {"path_or_alias": args.get("source"), "must_exist": True})
    if source.get("status") == "error":
        return source
    src = str(source.get("resolved_path") or "")
    dst = str(args.get("destination") or "")
    if move and (is_risky_path(src) or is_risky_path(dst)):
        return {"status": "needs_confirmation", "reason": "risky_system_path", "source": src, "destination": dst}
    cmd = "Move-Item" if move else "Copy-Item"
    status = "moved" if move else "copied"
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$src={_ps_str(src)}; $dst={_ps_str(dst)}
if ((Test-Path -LiteralPath $dst) -and -not {_ps_bool(args.get("overwrite", False))}) {{ [pscustomobject]@{{status='error'; error='destination_exists'; source=$src; destination=$dst}} | ConvertTo-Json -Compress; exit 0 }}
{cmd} -LiteralPath $src -Destination $dst -Recurse -Force:{_ps_bool(args.get("overwrite", False))}
[pscustomobject]@{{status={_ps_str(status)}; source=$src; destination=$dst; old_path=$src; new_path=$dst; terminal_sufficient=$true; completion_state='success'; evidence="{status} $src to $dst"}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 60)))


async def fs_delete(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    mode = str(args.get("mode") or "trash")
    resolved = await resolve_path(send_command_fn, device_id, {"path_or_alias": args.get("path"), "must_exist": True})
    if resolved.get("status") == "error":
        return resolved
    path = str(resolved.get("resolved_path") or "")
    if mode == "permanent" and not bool(args.get("confirmed", False)):
        return {"status": "needs_confirmation", "reason": "destructive_action", "resolved_path": path}
    if resolved.get("type") == "dir" and not bool(args.get("confirmed", False)):
        return {"status": "needs_confirmation", "reason": "folder_delete_requires_confirmation", "resolved_path": path}
    if is_risky_path(path):
        return {"status": "needs_confirmation", "reason": "risky_system_path", "resolved_path": path}
    is_dir = resolved.get("type") == "dir"
    if mode == "permanent":
        delete_command = f"Remove-Item -LiteralPath $path -Force -Recurse:{_ps_bool(is_dir)}"
    else:
        delete_command = """
$parent = Split-Path -Parent $path
$leaf = Split-Path -Leaf $path
$shell = New-Object -ComObject Shell.Application
$folder = $shell.Namespace($parent)
if ($null -eq $folder) { throw "recycle_folder_not_found" }
$item = $folder.ParseName($leaf)
if ($null -eq $item) { throw "recycle_item_not_found" }
$item.InvokeVerb('delete')
"""
    script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$path={_ps_str(path)}
{delete_command}
[pscustomobject]@{{status='deleted'; path=$path; mode={_ps_str(mode)}; terminal_sufficient=$true; completion_state='success'; evidence="deleted $path via {mode}"}} | ConvertTo-Json -Compress
"""
    return _parse_stdout_json(await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(script, 30)))


async def fs_open_folder(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    resolved = await resolve_path(send_command_fn, device_id, {**args, "must_exist": True, "expected_type": "dir"})
    if resolved.get("status") == "error":
        return {"status": "failed", **resolved}
    path = str(resolved.get("resolved_path") or "")
    folder_name = ntpath.basename(path.rstrip("\\/")) or path
    launch_error = None
    launch_result: dict[str, Any] = {}
    try:
        launch_result = await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(f"Start-Process explorer.exe -ArgumentList @({_ps_str(path)})\n[pscustomobject]@{{status='launched'; path={_ps_str(path)}}} | ConvertTo-Json -Compress", 5))
    except Exception as exc:
        launch_error = str(exc)
    if not launch_error and (launch_result.get("error") or str(launch_result.get("stderr") or "").strip()):
        launch_error = str(launch_result.get("error") or launch_result.get("stderr"))
    window_result = await _send(send_command_fn, device_id, "window.find", {
        "title_contains": folder_name,
        "process_name": "explorer",
        "visible": True,
        "timeout_sec": 5,
    })
    window = window_result.get("window") or window_result.get("match") or {}
    window_found = str(window_result.get("status") or "").lower() in {"found", "success", "ok"} or bool(window_result.get("window_found") or window)
    status = "opened" if (window_found or not launch_error) else "failed"
    return {
        "status": status,
        "resolved_path": path,
        "exists": True,
        "type": "dir",
        "launch_error": launch_error,
        "launch_result": _parse_stdout_json(launch_result) if launch_result else {},
        "window_found": bool(window_found),
        "window_title": window.get("title") or window_result.get("window_title"),
        "process_name": window.get("process_name") or window_result.get("process_name"),
        "pid": window.get("pid") or window_result.get("pid"),
        "terminal_sufficient": bool(window_found or not launch_error),
        "completion_state": "success" if window_found else ("partial_success" if not launch_error else "failed"),
        "evidence": f"opened folder {path}; window_found={bool(window_found)}",
    }


async def app_open_file(send_command_fn: SendCommandFn, device_id: str, args: dict[str, Any]) -> dict[str, Any]:
    resolved = await resolve_path(send_command_fn, device_id, {"path_or_alias": args.get("path"), "must_exist": True, "expected_type": "file"})
    if resolved.get("status") == "error":
        return {"status": "failed", **resolved}
    path = str(resolved.get("resolved_path") or "")
    stem = ntpath.splitext(ntpath.basename(path))[0]
    launch_error = None
    try:
        launch_result = await _send(send_command_fn, device_id, "execute_cmd", _execute_ps(f"Start-Process -FilePath {_ps_str(path)}\n[pscustomobject]@{{status='launched'; path={_ps_str(path)}}} | ConvertTo-Json -Compress", 5))
    except Exception as exc:
        launch_result = {}
        launch_error = str(exc)
    if not launch_error and (launch_result.get("error") or str(launch_result.get("stderr") or "").strip()):
        launch_error = str(launch_result.get("error") or launch_result.get("stderr"))
    window_result = await _send(send_command_fn, device_id, "window.find", {"title_contains": stem, "visible": True, "timeout_sec": 5})
    window = window_result.get("window") or window_result.get("match") or {}
    window_found = str(window_result.get("status") or "").lower() in {"found", "success", "ok"} or bool(window_result.get("window_found") or window)
    launched = not launch_error
    status = "opened" if launched else "failed"
    return {
        "status": status,
        "path": path,
        "resolved_path": path,
        "process_started": launched,
        "launch_error": launch_error,
        "window_found": bool(window_found),
        "window_title": window.get("title") or window_result.get("window_title"),
        "process_name": window.get("process_name") or window_result.get("process_name"),
        "pid": window.get("pid") or window_result.get("pid"),
        "terminal_sufficient": bool(launched or window_found),
        "completion_state": "success" if window_found else ("partial_success" if launched else "failed"),
        "evidence": f"opened file {path}; window_found={bool(window_found)}",
    }


async def run_pc_core_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    send_command_fn: SendCommandFn,
    device_id: str,
) -> dict[str, Any]:
    tool_name = PC_CORE_NAME_ALIASES.get(tool_name, tool_name)
    try:
        if tool_name == "fs_resolve_path":
            return await resolve_path(send_command_fn, device_id, args)
        if tool_name == "fs_open_folder":
            return await fs_open_folder(send_command_fn, device_id, args)
        if tool_name == "fs_list_dir":
            return await fs_list_dir(send_command_fn, device_id, args)
        if tool_name == "fs_stat":
            return await fs_stat(send_command_fn, device_id, args)
        if tool_name == "fs_read_file":
            return await fs_read_file(send_command_fn, device_id, args)
        if tool_name == "fs_write_file":
            return await fs_write_file(send_command_fn, device_id, args)
        if tool_name == "fs_patch_file":
            return await fs_patch_file(send_command_fn, device_id, args)
        if tool_name == "fs_rename":
            return await fs_rename(send_command_fn, device_id, args)
        if tool_name == "fs_copy":
            return await _copy_or_move(send_command_fn, device_id, args, move=False)
        if tool_name == "fs_move":
            return await _copy_or_move(send_command_fn, device_id, args, move=True)
        if tool_name == "fs_delete":
            return await fs_delete(send_command_fn, device_id, args)
        if tool_name == "app_open_file":
            return await app_open_file(send_command_fn, device_id, args)
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "error", "error": f"unknown pc core tool: {tool_name}"}
