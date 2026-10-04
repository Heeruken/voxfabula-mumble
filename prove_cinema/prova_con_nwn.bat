@echo off
echo Hai 10 secondi: clicca sulla finestra di NWN e gioca normalmente.
timeout /t 10 /nobreak >nul
cd /d C:\NWN\projects\nwn-voce
python -m nwn_voce --video "%~dp0prova.webm"
echo Esito: %errorlevel%  (0=visto, 3=saltato, 1=errore)
pause

