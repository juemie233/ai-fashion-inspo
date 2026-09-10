# AI 穿搭灵感库 — 代码行数统计（PowerShell 版，口径与 count_lines.sh 保持一致）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\count_lines.ps1
#
# 注意：本文件必须保存为「UTF-8 带 BOM」。Windows PowerShell 5.1 对无 BOM 文件
# 按 ANSI(cp936) 解析源码，中文注释会被读成乱码并破坏语法，各分节统计会静默变成 0。

$projectRoot = "C:\Users\Administrator\Desktop\Claude Code\MMK\fashion-inspo"

function Count-FileLines {
    param([string[]]$files, [string]$commentPattern, [string]$label)
    if ($files.Count -eq 0) {
        Write-Host $label
        return
    }

    $code = 0
    $blank = 0
    $comment = 0

    foreach ($f in $files) {
        if (!(Test-Path -LiteralPath $f)) { continue }
        # 必须显式 -Encoding UTF8：Windows PowerShell 5.1 默认按 ANSI(cp936) 解码，
        # 会把 UTF-8 中文读成乱码并吞掉行，导致行数偏低
        $fileLines = Get-Content -LiteralPath $f -Encoding UTF8 -ErrorAction SilentlyContinue
        foreach ($line in $fileLines) {
            $trimmed = $line.Trim()
            if ($trimmed -eq "") {
                $blank++
            } elseif ($trimmed -match $commentPattern) {
                $comment++
            } else {
                $code++
            }
        }
    }

    $total = $code + $blank + $comment
    Write-Host $label
    Write-Host ("  Total: {0}  |  Code: {1}  |  Blank: {2}  |  Comment: {3}  |  Files: {4}" -f $total, $code, $blank, $comment, $files.Count)
}

$RE_PY = '^#'
$RE_TS = '^(//|/\*|\*|<!--)'
$RE_BAT = '^(rem|REM|::|#)'
$RE_HTML = '^<!--'

Write-Host ""
Write-Host "=== Code Line Count ===" -ForegroundColor Cyan
Write-Host ""

$pyFiles = Get-ChildItem -Path (Join-Path $projectRoot "backend") -Recurse -File -Include "*.py" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -NotMatch "(\\|/)(node_modules|__pycache__|\.egg-info|\.pytest_cache|\.venv)($|\\)" }
if ($pyFiles.Count -gt 0) {
    $paths = $pyFiles | ForEach-Object { $_.FullName }
    Count-FileLines -files $paths -commentPattern $RE_PY -label "[1] Backend Python"
}

$webFiles = Get-ChildItem -Path (Join-Path $projectRoot "web/src") -Recurse -File -Include "*.ts", "*.vue", "*.css" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -NotMatch "(\\|/)(node_modules|\.venv)($|\\)" }
if ($webFiles.Count -gt 0) {
    $paths = $webFiles | ForEach-Object { $_.FullName }
    Count-FileLines -files $paths -commentPattern $RE_TS -label "[2] Web Frontend (Vue/TS/CSS)"
}

$mobileFiles = Get-ChildItem -Path (Join-Path $projectRoot "mobile") -Recurse -File -Include "*.tsx", "*.ts", "*.json" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -NotMatch "(\\|/)(node_modules|\.venv)($|\\)" -and $_.Name -ne "package-lock.json" }
if ($mobileFiles.Count -gt 0) {
    $paths = $mobileFiles | ForEach-Object { $_.FullName }
    Count-FileLines -files $paths -commentPattern $RE_TS -label "[3] Mobile (React Native)"
}

$beFiles = Get-ChildItem -Path (Join-Path $projectRoot "browser-extension") -Recurse -File -Include "*.js", "*.html", "*.css", "*.json" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -NotMatch "(\\|/)(node_modules|\.venv)($|\\)" -and $_.Name -ne "package-lock.json" }
if ($beFiles.Count -gt 0) {
    $paths = $beFiles | ForEach-Object { $_.FullName }
    Count-FileLines -files $paths -commentPattern $RE_TS -label "[4] Browser Extension"
}

$scriptFiles = Get-ChildItem -Path (Join-Path $projectRoot "scripts\*") -File -Include "*.py", "*.sh", "*.bat" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne "count_lines.sh" -and $_.Name -ne "count_lines.ps1" -and $_.FullName -NotMatch "(\\|/)(node_modules|\.venv)($|\\)" }
if ($scriptFiles.Count -gt 0) {
    $paths = $scriptFiles | ForEach-Object { $_.FullName }
    Count-FileLines -files $paths -commentPattern $RE_BAT -label "[5] Scripts"
}

$sharedFiles = Get-ChildItem -Path (Join-Path $projectRoot "shared") -Recurse -File -Include "*.ts" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -NotMatch "(\\|/)(node_modules|\.venv)($|\\)" }
if ($sharedFiles.Count -gt 0) {
    $paths = $sharedFiles | ForEach-Object { $_.FullName }
    Count-FileLines -files $paths -commentPattern $RE_TS -label "[6] Shared Types"
}

$docFiles = @()
$mdFiles = Get-ChildItem -Path (Join-Path $projectRoot "*") -File -Include "*.md", "*.mdx" -ErrorAction SilentlyContinue
$mdFiles += Get-ChildItem -Path (Join-Path $projectRoot "backend\*") -File -Include "*.txt", ".env", ".gitignore" -ErrorAction SilentlyContinue
$mdFiles += Get-ChildItem -Path (Join-Path $projectRoot "*") -File -Include ".gitignore", ".editorconfig" -ErrorAction SilentlyContinue
foreach ($f in $mdFiles) {
    $docFiles += $f.FullName
}
if ($docFiles.Count -gt 0) {
    Count-FileLines -files $docFiles -commentPattern $RE_HTML -label "[7] Docs/Config"
}

Write-Host ""
Write-Host "---" -ForegroundColor Yellow

$totalCode = 0
$totalBlank = 0
$totalComment = 0

$allFiles = Get-ChildItem -Path $projectRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".py", ".ts", ".tsx", ".vue", ".css", ".js", ".html", ".json", ".md", ".sh", ".bat") } |
    Where-Object { $_.FullName -NotMatch "(\\|/)(node_modules|__pycache__|dist|build|\.git|\.claude|storage|\.pytest_cache|\.egg-info|\.venv|site-packages|backups)($|\\)" } |
    Where-Object { $_.Name -notin @("package-lock.json", "pnpm-lock.yaml", "yarn.lock") }

Write-Host ("File count: {0}" -f $allFiles.Count)

foreach ($f in $allFiles) {
    $line = Get-Content -LiteralPath $f.FullName -Encoding UTF8 -ErrorAction SilentlyContinue
    foreach ($l in $line) {
        $trimmed = $l.Trim()
        if ($trimmed -eq "") { $totalBlank++ }
        elseif ($trimmed -match $RE_PY -or $trimmed -match $RE_TS -or $trimmed -match $RE_BAT) { $totalComment++ }
        else { $totalCode++ }
    }
}

$totalAll = $totalCode + $totalBlank + $totalComment
Write-Host ""
Write-Host "=== Grand Total: $totalAll | Code: $totalCode | Blank: $totalBlank | Comment: $totalComment" -ForegroundColor Cyan
Write-Host ""
