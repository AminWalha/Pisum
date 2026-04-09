; ═══════════════════════════════════════════════════════════════════════
;  PISUM Radiology — Script Inno Setup
;  Génère : PISUM_Radiology_Setup_x.x.x.exe
;
;  PRÉREQUIS : Inno Setup 6.x  →  https://jrsoftware.org/isinfo.php
; ═══════════════════════════════════════════════════════════════════════

#define AppName       "PISUM Radiology"
#define AppShortName  "PISUM"
#define AppVersion    "1.0.2"
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

; {autopf} → Program Files (x64/x86 selon l'OS) — standard pour les apps admin
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}

PrivilegesRequired=admin

Uninstallable=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
CreateUninstallRegKey=yes

OutputDir=installer
OutputBaseFilename=PISUM_Radiology_Setup_{#AppVersion}

; CORRIGÉ : chemin relatif (pisum.ico doit être au même niveau que ce .iss)
; build.ps1 copie pisum.ico à la racine du projet — ce chemin est donc correct.
SetupIconFile=pisum.ico

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
; ── EXE principal ────────────────────────────────────────────────────
Source: "{#DistRoot}\PISUM\PISUM.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── Dépendances PyInstaller 6+ (tout est dans _internal\) ────────────
Source: "{#DistRoot}\PISUM\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; ── Ressources à la racine de l'exe ──────────────────────────────────
Source: "pisum.ico";   DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "rad.ico";     DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

; ── Dossier flags\ ───────────────────────────────────────────────────
Source: "..\flags\*"; DestDir: "{app}\flags"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; ── Config réseau PACS : préservée lors des mises à jour ─────────────
; Ne jamais écraser si déjà présente (config utilisateur)
Source: "pisum_network.cfg"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist

; ── Données externes ─────────────────────────────────────────────────
Source: "CRs by languages\*"; DestDir: "{app}\CRs by languages"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist


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

  // CORRIGÉ : chercher HKLM en premier — l'installation requiert admin,
  // donc l'entrée de désinstallation est toujours dans HKLM, pas HKCU.
  if not RegQueryStringValue(HKLM, RegKey, 'UninstallString', Uninstaller) then
    RegQueryStringValue(HKCU, RegKey, 'UninstallString', Uninstaller);

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
