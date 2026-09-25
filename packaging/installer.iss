; Installer di Vox Fabula Voice (Inno Setup 6). Lanciato da packaging\build.ps1,
; che passa la versione:  ISCC /DMyAppVersion=1.1.0 installer.iss
;
; - installazione PER UTENTE in %LOCALAPPDATA%\Programs\Vox Fabula Voice: niente
;   richiesta di amministratore, niente modifiche al sistema;
; - menu Start (+ icona sul desktop facoltativa) e disinstallazione da
;   Impostazioni > App, come un programma qualsiasi;
; - le impostazioni dell'utente (%APPDATA%\Vox Fabula Voice) restano dopo la
;   disinstallazione, cosi' un aggiornamento non le perde.
; - AppId invariato da quando si chiamava "NWN Voce": per Windows e' lo stesso
;   programma, quindi un'installazione di prova col vecchio nome viene sostituita.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Vox Fabula Voice"
#define MyAppPublisher "VoxFabula"
#define MyAppExeName "Vox Fabula Voice.exe"
#define OldAppName "NWN Voce"

[Setup]
AppId={{8F3A1C42-9B7E-4D6A-A1F2-3C5E9D8B7A60}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} - installazione
DefaultDirName={localappdata}\Programs\{#MyAppName}
; sempre la cartella col nome nuovo (l'aggiornamento automatico la riconosce da li')
UsePreviousAppDir=no
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=VoxFabula-Voice-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
; immagini dell'installer: cielo astrale col logo, cristallo in alto a destra
WizardImageFile=brand\wizard-side.bmp
WizardSmallImageFile=brand\wizard-small.bmp
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; da una versione all'altra i file interni cambiano: niente avanzi
Type: filesandordirs; Name: "{app}\_internal"
; eventuale installazione di prova col vecchio nome: cartella e collegamenti
Type: filesandordirs; Name: "{localappdata}\Programs\{#OldAppName}"
Type: files; Name: "{autoprograms}\{#OldAppName}.lnk"
Type: files; Name: "{autodesktop}\{#OldAppName}.lnk"

[Files]
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; installazione normale: casella "Avvia Vox Fabula Voice" alla fine
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
; aggiornamento dall'app (installer lanciato con /SILENT): riapre l'app da solo
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent
