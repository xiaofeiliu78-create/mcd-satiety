@echo off
chcp 65001 >nul
title 麦门顶饿算账
cd /d "%~dp0"

set PY=C:\Python312\python.exe
if not exist "%PY%" set PY=python

echo.
echo  麦门顶饿算账 - 麦当劳官方数据实时算账
echo  ----------------------------------------
echo.

set /p CITY=城市（回车 = 周口市）: 
if "%CITY%"=="" set CITY=周口市

set /p KW=门店关键词（回车 = 万达广场）: 
if "%KW%"=="" set KW=万达广场

set /p BUD=预算几块钱（回车 = 35）: 
if "%BUD%"=="" set BUD=35

set /p KC=热量上限 kcal（回车 = 800，填 0 = 不限）: 
if "%KC%"=="" set KC=800

echo.
echo  正在查麦当劳官方接口...
echo.

"%PY%" mcd_solver.py --city "%CITY%" --keyword "%KW%" --budget %BUD% --kcal %KC%

echo.
echo  ----------------------------------------
pause
