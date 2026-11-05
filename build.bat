@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   Building ShinraTypewriter.exe
echo ============================================
echo.

rem ---- find Python ----
rem Look Python up by full path (works even if PATH is damaged), and check it really runs
set "PY="
for %%n in (py python python3) do (
    if not defined PY (
        for /f "delims=" %%i in ('where %%n 2^>nul') do (
            if not defined PY (
                "%%i" --version >nul 2>&1 && set "PY="%%i""
            )
        )
    )
)
if not defined PY (
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PY if exist "%%d\python.exe" set "PY="%%d\python.exe""
    )
)
if not defined PY (
    echo Python wasn't found. Install it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" during setup^), then run this again.
    pause & exit /b 1
)

rem ---- find Tesseract (gets bundled inside the exe) ----
set "TESS=C:\Program Files\Tesseract-OCR"
if not exist "%TESS%\tesseract.exe" set "TESS=C:\Program Files (x86)\Tesseract-OCR"
if not exist "%TESS%\tesseract.exe" (
    echo Tesseract OCR wasn't found. Install it from
    echo   https://github.com/UB-Mannheim/tesseract/wiki
    echo using the default location, then run this again.
    pause & exit /b 1
)

echo Using Python: %PY%

rem ---- build environment ----
if exist ".venv" if not exist ".venv\Scripts\python.exe" rmdir /s /q .venv
if not exist ".venv\Scripts\python.exe" (
    echo Creating build environment...
    %PY% -m venv .venv || (echo Couldn't create the build environment. & pause & exit /b 1)
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip >nul
echo Installing packages...
"%VPY%" -m pip install -r requirements.txt pyinstaller || (echo Package install failed. & pause & exit /b 1)

rem ---- copy only the parts of Tesseract the bot needs ----
echo Copying Tesseract...
if exist build_tess rmdir /s /q build_tess
mkdir build_tess\tessdata
copy /y "%TESS%\tesseract.exe" build_tess\ >nul
copy /y "%TESS%\*.dll" build_tess\ >nul
copy /y "%TESS%\tessdata\eng.traineddata" build_tess\tessdata\ >nul
if not exist build_tess\tessdata\eng.traineddata (
    echo English language data missing from your Tesseract install ^(tessdata\eng.traineddata^).
    pause & exit /b 1
)

rem ---- build ----
echo Building the app ^(takes a minute or two^)...
"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name ShinraTypewriter ^
    --add-data "build_tess;tesseract" app.py || (echo Build failed. & pause & exit /b 1)

copy /y dist\ShinraTypewriter.exe ShinraTypewriter.exe >nul
echo.
echo ============================================
echo   Done!  ShinraTypewriter.exe is in this folder.
echo   Share ShinraTypewriter.exe and README.txt.
echo ============================================
pause
