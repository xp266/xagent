@echo off
setlocal
cd /d "%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is required but not found. Install it: https://docs.astral.sh/uv/
    exit /b 1
)

if not "%XAGENT_PYPI_INDEX%"=="" (
    echo ==^> using custom index: %XAGENT_PYPI_INDEX%
    uv lock --index-url %XAGENT_PYPI_INDEX%
    if errorlevel 1 exit /b 1
)

echo ==^> syncing dependencies
if exist uv.lock (
    uv sync --frozen
) else (
    uv sync
)
if errorlevel 1 exit /b 1

echo ==^> prefetching model catalog
uv run --frozen python scripts\prefetch_catalog.py
if errorlevel 1 exit /b 1

echo ==^> done
endlocal
