# 麦门顶饿算账 · 交互式启动
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -Path $PSScriptRoot

$py = 'C:\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

Write-Host ''
Write-Host '  麦门顶饿算账 · 麦当劳官方数据实时算账' -ForegroundColor Cyan
Write-Host '  ----------------------------------------'
Write-Host ''

$city = Read-Host '  城市（直接回车 = 周口市）'
if ([string]::IsNullOrWhiteSpace($city)) { $city = '周口市' }

$kw = Read-Host '  门店关键词（直接回车 = 万达广场）'
if ([string]::IsNullOrWhiteSpace($kw)) { $kw = '万达广场' }

$bud = Read-Host '  预算几块钱（直接回车 = 35）'
if ([string]::IsNullOrWhiteSpace($bud)) { $bud = '35' }

$kc = Read-Host '  热量上限 kcal（直接回车 = 800，填 0 不限）'
if ([string]::IsNullOrWhiteSpace($kc)) { $kc = '800' }

Write-Host ''
Write-Host '  正在查麦当劳官方接口...' -ForegroundColor DarkGray
Write-Host ''

& $py mcd_solver.py --city "$city" --keyword "$kw" --budget $bud --kcal $kc

Write-Host ''
Write-Host '  ----------------------------------------'
Read-Host '  按回车关闭' | Out-Null
