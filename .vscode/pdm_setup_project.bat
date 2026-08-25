@echo off
setlocal EnableDelayedExpansion

set REPO_FOLDER=%~dp0..
echo Repository directory: %REPO_FOLDER%
pushd "%REPO_FOLDER%"

where pdm >nul 2>nul
if errorlevel 1 (
    echo "'pdm' not found. Installing via official installer..."
    powershell.exe -ExecutionPolicy ByPass -c "irm https://pdm-project.org/install-pdm.py | python -"
    set "PDM_SCRIPTS=%APPDATA%\Python\Scripts"
    if exist "!PDM_SCRIPTS!\pdm.exe" set Path=!PDM_SCRIPTS!;!Path!
)

where pdm >nul 2>nul
if errorlevel 1 (
    echo "'pdm' still not on PATH. Please install it manually: https://pdm-project.org/"
    popd
    exit /b 1
)

echo Installing dev dependencies...
pdm install --group dev
if errorlevel 1 (
    echo "pdm install failed."
    popd
    exit /b 1
)

echo Vendoring Qt into the plugin...
pdm run python dev.py vendor

echo Installing pre-commit hooks...
pdm run pre-commit install

echo.
echo Setup complete. Next:
echo   1. Select the .venv interpreter in VS Code (Python: Select Interpreter).
echo   2. Use "Register Plugin (junction)" or "Register Python Path" to point a UE project at this repo.
popd
