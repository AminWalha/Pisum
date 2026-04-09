; ═══════════════════════════════════════════════════════════════════════
;  PISUM Radiology — Script Inno Setup
;  Génère : PISUM_Radiology_Setup_x.x.x.exe
;
;  PRÉREQUIS : Inno Setup 6.x  →  https://jrsoftware.org/isinfo.php
;
;  STRUCTURE RÉELLE DU PROJET :
;
;  Bureau\PISUM\LOGICIEL\PISUM\
;  ├── pisum_setup.iss            ← CE fichier
;  └── dist\
;      ├── CRs by languages\      ← données radio
;      ├── flags\                 ← drapeaux langues
;      ├── PISUM\
;      │   ├── PISUM.exe          ← exécutable principal
;      │   └── _internal\         ← DLL PyInstaller
;      ├── cloud_license_client.py
;      ├── Comptes_Rendus.py
;      ├── config_manager.py
;      ├── encrypted_excel_loader.py
;      ├── pacs_ris_db.py
;      ├── pisum.ico
;      ├── pisum_network.cfg
;      ├── shared_config.py
;      └── whisper_dictation.py
;
;  UTILISATION :
;    1. Adapte AppVersion avant chaque release
;    2. Lance build_v6_8.ps1 → génère dist\
;    3. Ouvre ce .iss dans Inno Setup Compiler
;    4. Build → Compile  (Ctrl+F9)
;
;  CHANGEMENTS v1.0.4 :
;    - Suppression de vc_redist.x64.exe (non nécessaire)
;    - MinVersion=6.1 (compatible Windows 7/8/10/11 toutes versions)
; ═══════════════════════════════════════════════════════════════════════

#define AppName       "PISUM Radiology"
#define AppShortName  "PISUM"
#define AppVersion    "1.0.3"
#define AppPublisher  "Radiologie"
#define AppExeName    "PISUM.exe"

; Racine du dossier dist\ (relatif à l'emplacement de ce .iss)
#define DistRoot      "dist"

; ── [Setup] ─────────────────────────────────────────────────────────────
[Setup]
; !! NE PAS MODIFIER AppId après la première release !!
AppId={{F4A7B2E9-3C8D-4E1F-A5B6-7D9E2F1C4A8B}

AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=
AppSupportURL=
AppUpdatesURL=

DefaultDirName={sd}\{#AppName}
DefaultGroupName={#AppName}

PrivilegesRequired=admin

Uninstallable=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
CreateUninstallRegKey=yes

OutputDir={#DistRoot}\..\installer
OutputBaseFilename=PISUM_Radiology_Setup_{#AppVersion}

SetupIconFile={#DistRoot}\pisum.ico

Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

WizardStyle=modern
WizardSizePercent=120
ShowLanguageDialog=auto
DisableWelcomePage=no
AllowNoIcons=yes

CloseApplications=yes
CloseApplicationsFilter=*{#AppExeName}
RestartApplications=no

MinVersion=6.1

; ── [Languages] ─────────────────────────────────────────────────────────
[Languages]
Name: "french";  MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

; ── [Tasks] ─────────────────────────────────────────────────────────────
[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le &Bureau"; GroupDescription: "Raccourcis :"; Flags: checkedonce

; ── [Files] ─────────────────────────────────────────────────────────────
[Files]

; ── Exécutable principal ─────────────────────────────────────────────
Source: "{#DistRoot}\PISUM\{#AppExeName}";        DestDir: "{app}";                  Flags: ignoreversion

; ── Dépendances PyInstaller ───────────────────────────────────────────
Source: "{#DistRoot}\PISUM\_internal\*";           DestDir: "{app}\_internal";        Flags: ignoreversion recursesubdirs createallsubdirs

; ── Icône ─────────────────────────────────────────────────────────────
Source: "{#DistRoot}\pisum.ico";                   DestDir: "{app}";                  Flags: ignoreversion

; ── Données — drapeaux des langues ───────────────────────────────────
Source: "{#DistRoot}\flags\*";                     DestDir: "{app}\flags";            Flags: ignoreversion recursesubdirs createallsubdirs

; ── Données — formules radiologiques ─────────────────────────────────
Source: "{#DistRoot}\CRs by languages\*";          DestDir: "{app}\CRs by languages"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Modules Python (en clair) ─────────────────────────────────────────
Source: "{#DistRoot}\Comptes_Rendus.py";           DestDir: "{app}";                  Flags: ignoreversion
Source: "{#DistRoot}\whisper_dictation.py";        DestDir: "{app}";                  Flags: ignoreversion
Source: "{#DistRoot}\cloud_license_client.py";     DestDir: "{app}";                  Flags: ignoreversion
Source: "{#DistRoot}\config_manager.py";           DestDir: "{app}";                  Flags: ignoreversion
Source: "{#DistRoot}\encrypted_excel_loader.py";   DestDir: "{app}";                  Flags: ignoreversion
Source: "{#DistRoot}\pacs_ris_db.py";              DestDir: "{app}";                  Flags: ignoreversion
Source: "{#DistRoot}\shared_config.py";            DestDir: "{app}";                  Flags: ignoreversion

; ── Config réseau PACS : préservée lors des mises à jour ─────────────
Source: "{#DistRoot}\pisum_network.cfg";           DestDir: "{app}";                  Flags: ignoreversion onlyifdoesntexist

; ── Runtime PyArmor ──────────────────────────────────────────────────
Source: "{#DistRoot}\pyarmor_runtime_011252\*";    DestDir: "{app}\pyarmor_runtime_011252"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── [Icons] ─────────────────────────────────────────────────────────────
[Icons]
; Menu Démarrer
Name: "{group}\{#AppName}";               Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\pisum.ico"; Comment: "Logiciel de compte-rendu radiologique"
Name: "{group}\Désinstaller {#AppName}";  Filename: "{uninstallexe}";      IconFilename: "{app}\pisum.ico"

; Bureau
Name: "{autodesktop}\{#AppName}";         Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\pisum.ico"; Tasks: desktopicon

; ── [Registry] ──────────────────────────────────────────────────────────
[Registry]
; Taille estimée dans Ajout/Suppression de programmes (~700 Mo)
Root: HKLM;  Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{{F4A7B2E9-3C8D-4E1F-A5B6-7D9E2F1C4A8B}_is1"; ValueType: dword; ValueName: "EstimatedSize"; ValueData: "716800"; Flags: uninsdeletekey

; ── [Run] ────────────────────────────────────────────────────────────────
[Run]
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName} maintenant"; Flags: nowait postinstall skipifsilent

; ── [UninstallDelete] ────────────────────────────────────────────────────
[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\_MEI*"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\build_logs"
Type: files;          Name: "{app}\*.log"

; ── [Code] ──────────────────────────────────────────────────────────────
[Code]

// Supprimer l'ancienne version avant installation
function InitializeSetup(): Boolean;
var
  Uninstaller: String;
  ResultCode:  Integer;
  RegKey:      String;
begin
  Result := True;
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\'
            + '{F4A7B2E9-3C8D-4E1F-A5B6-7D9E2F1C4A8B}_is1';

  if not RegQueryStringValue(HKCU, RegKey, 'UninstallString', Uninstaller) then
    RegQueryStringValue(HKLM, RegKey, 'UninstallString', Uninstaller);

  if Uninstaller <> '' then
  begin
    if MsgBox(
      'Une version précédente de {#AppName} est installée.' + #13#10 +
      'Elle sera supprimée avant de continuer.' + #13#10#13#10 +
      'Continuer ?',
      mbConfirmation, MB_YESNO
    ) = IDNO then
    begin
      Result := False;
      Exit;
    end;
    Exec(RemoveQuotes(Uninstaller), '/SILENT /NORESTART', '',
         SW_SHOW, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;

// Confirmation avant désinstallation
function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    'Désinstaller {#AppName} ?' + #13#10#13#10 +
    'Le dossier d''installation et tous ses fichiers seront supprimés.' + #13#10 +
    'Vos données radiologiques externes ne seront pas affectées.',
    mbConfirmation, MB_YESNO
  ) = IDYES;
end;

// Message après désinstallation
procedure DeinitializeUninstall();
begin
  MsgBox('{#AppName} a été désinstallé avec succès.', mbInformation, MB_OK);
end;
