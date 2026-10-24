# 麦门顶饿算账 · 交互式启动
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -Path $PSScriptRoot

$py = $null
foreach ($cand in @('python', 'py', 'C:\Python312\python.exe')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) {
    Write-Host ''
    Write-Host '  没找到 Python。请先装 Python 3，安装时勾选 Add Python to PATH。' -ForegroundColor Yellow
    Write-Host '  下载地址： https://www.python.org/downloads/'
    Write-Host ''
    Read-Host '  按回车关闭' | Out-Null
    exit 1
}

Write-Host ''
Write-Host '  麦门顶饿算账 · 麦当劳官方数据实时算账' -ForegroundColor Cyan
Write-Host '  ----------------------------------------'
Write-Host ''

$city = Read-Host '  城市（直接回车 = 周口市）'
if ([string]::IsNullOrWhiteSpace($city)) { $city = '周口市' }

$kw = Read-Host '  门店关键词（直接回车 = 自动找最近的门店）'

$bud = Read-Host '  预算几块钱（直接回车 = 35）'
if ([string]::IsNullOrWhiteSpace($bud)) { $bud = '35' }

$kc = Read-Host '  热量上限 kcal（直接回车 = 800，填 0 不限）'
if ([string]::IsNullOrWhiteSpace($kc)) { $kc = '800' }

$one = Read-Host '  只看一句话结论，方便发微信？（y / 直接回车 = 不要）'

Write-Host ''
Write-Host '  正在查麦当劳官方接口...' -ForegroundColor DarkGray
Write-Host ''

$argList = @('mcd_solver.py', '--city', $city, '--budget', $bud, '--kcal', $kc)
if (-not [string]::IsNullOrWhiteSpace($kw)) { $argList += @('--keyword', $kw) }
if ($one -match '^[Yy]') { $argList += '--oneline' }

& $py @argList

Write-Host ''
Write-Host '  ----------------------------------------'
Read-Host '  按回车关闭' | Out-Null
