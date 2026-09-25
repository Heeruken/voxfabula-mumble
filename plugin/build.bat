@echo off
rem Build del plugin Mumble vc_range (PoC) con il compilatore di Visual Studio 2022.
rem Non serve installare nulla: usa l'MSVC gia' presente con VS Community 2022.
rem L'header MumblePlugin.h va compilato come C++ (/TP): alcune costanti sono
rem 'constexpr' e in C non sarebbero costanti.
setlocal
set "HERE=%~dp0"

rem Trova vcvars64.bat tra le edizioni standard di VS 2022.
set "VCVARS="
for %%E in (Community Professional Enterprise BuildTools) do (
    if not defined VCVARS if exist "C:\Program Files\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat"
)
if not defined VCVARS (
    echo ERRORE: vcvars64.bat non trovato sotto "C:\Program Files\Microsoft Visual Studio\2022\".
    exit /b 1
)

rem Carica l'ambiente x64 (mette cl.exe nel PATH).
call "%VCVARS%" >nul
if errorlevel 1 (
    echo ERRORE: vcvars64.bat fallito.
    exit /b 1
)

rem Compila il DLL del plugin (come C++).
cl /nologo /LD /O2 /W3 /TP ^
   /I "%HERE%mumble_api" ^
   "%HERE%vc_range.c" ^
   /Fe:"%HERE%vc_range.dll" ^
   /Fo:"%HERE%vc_range.obj"
if errorlevel 1 (
    echo.
    echo ERRORE: compilazione fallita.
    exit /b 1
)

echo.
echo OK: creato "%HERE%vc_range.dll"
exit /b 0
