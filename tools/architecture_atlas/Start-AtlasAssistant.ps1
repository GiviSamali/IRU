param([int]$Port=8774)
$ErrorActionPreference='Stop'
$AtlasRoot=$PSScriptRoot
$PythonCommand=Get-Command python -ErrorAction SilentlyContinue
$PythonExecutable=if($PythonCommand){$PythonCommand.Source}else{Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'}
if(-not(Test-Path -LiteralPath $PythonExecutable)){throw 'Python не найден. Активируйте .venv с Python и повторите запуск.'}
Write-Host "Атлас с помощником: http://127.0.0.1:$Port/"
Write-Host 'Настройки DeepSeek находятся в кнопке Помощник. Ключ в чат Codex не вставляйте.'
& $PythonExecutable (Join-Path $AtlasRoot 'assistant\server.py') --port $Port
