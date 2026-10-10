@echo off
title Companion (codice nuovo, prova)
echo Companion dal codice nuovo (quello che diventera' la 1.3.5).
echo Il Companion installato deve essere chiuso (icona vicino all'orologio: Esci).
echo Lascia aperta questa finestra: chiuderla chiude il Companion.
cd /d C:\NWN\projects\nwn-voce
python -m nwn_voce
echo Companion chiuso (codice %errorlevel%).
pause
