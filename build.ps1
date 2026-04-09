# ═══════════════════════════════════════════════════════════════════
#  BUILD PISUM — PyArmor + PyInstaller
#  Usage : .\build.ps1 [-SkipTests] [-SkipPyArmor]
# ═══════════════════════════════════════════════════════════════════

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipPyArmor   # build sans obfuscation (dev / debug)
)

$ErrorActionPreference = 'Stop'
$BuildVersion = Get-Date -Format "yyyyMMdd_HHmmss"
if ($PSScriptRoot) { $BuildRoot = $PSScriptRoot } else { $BuildRoot = (Get-Location).Path }

$Config = @{
    DistDir    = "dist_pyarmor"   # sortie PyArmor (séparé du dist PyInstaller)
    BuildDir   = "build"
    LogDir     = "build_logs"
    FinalDir   = "dist"           # sortie finale PyInstaller

    # ── Modules obfusqués (sensibles) ────────────────────────────────
    CoreModules = @(
        "cloud_license_client.py"
    )
    SupabaseLoaderModules = @(
        "supabase_templates_loader.py"
    )

    # ── Modules copiés tels quels (non obfusqués) ────────────────────
    UnprotectedModules = @(
        "main.py"
        "Comptes_Rendus.py"
        "whisper_dictation.py"
        "correction.py"
        "config_manager.py"
        "shared_config.py"
        "encrypted_excel_loader.py"
        "pacs_ris_db.py"
        "pisum_license_manager.py"
        "pisum_upgrade_ui.py"
        "pacs_network_sync.py"
        "report_editor_controller.py"
        "report_template_manager.py"
        "custom_formulas_db.py"
        "icon_manager.py"
    )

    # ── Ressources à copier ──────────────────────────────────────────
    Resources = @{
        "../flags"          = "flags"
        "pisum.ico"         = "pisum.ico"
        "pisum_network.cfg" = "pisum_network.cfg"
        "rad.ico"           = "rad.ico"
    }

    # ── Options PyArmor ─────────────────────────────────────────────
    PyArmorOptions = @(
        "--enable-bcc"        # compilation native (nécessite clang)
        "--enable-rft"        # renommage de fonctions/classes
        "--mix-str"           # obfuscation des chaînes
        "--obf-code", "2"
        "--platform", "windows.x86_64"
    )
    SupabaseLoaderPyArmorOptions = @(
        "--enable-rft"
        "--mix-str"
        "--obf-code", "2"
        "--platform", "windows.x86_64"
    )

    ClangPath = "$env:USERPROFILE\.pyarmor\clang.exe"
    VcRedistPath = "vc_redist.x64.exe"

    # ── PyInstaller : hidden imports ─────────────────────────────────
    HiddenImports = @(
        # UI
        "customtkinter"
        "tkinter"
        "tkinter.ttk"
        "tkinter.messagebox"
        "tkinter.filedialog"
        "PIL"
        "PIL.Image"
        "PIL.ImageTk"
        # PDF
        "reportlab"
        "reportlab.lib"
        "reportlab.lib.pagesizes"
        "reportlab.lib.styles"
        "reportlab.lib.units"
        "reportlab.lib.colors"
        "reportlab.lib.enums"
        "reportlab.platypus"
        "reportlab.pdfbase"
        "reportlab.pdfbase.pdfmetrics"
        "reportlab.pdfbase.ttfonts"
        "fitz"                # PyMuPDF (preview PDF)
        # Data
        "sqlite3"
        "pandas"
        "openpyxl"
        # Crypto
        "cryptography"
        "cryptography.fernet"
        "cryptography.hazmat"
        "cryptography.hazmat.primitives"
        "cryptography.hazmat.primitives.ciphers"
        "cryptography.hazmat.primitives.ciphers.aead"
        "cryptography.hazmat.primitives.kdf"
        "cryptography.hazmat.primitives.kdf.pbkdf2"
        "cryptography.hazmat.primitives.hashes"
        "cryptography.hazmat.backends"
        # Network / Supabase
        "requests"
        "supabase"
        "gotrue"
        "postgrest"
        "storage3"
        "realtime"
        "httpx"
        # Word
        "docx"
        # Windows printing (print_dialog.py)
        "win32print"
        "win32api"
        "win32con"
        "pywintypes"
        # Whisper / AI
        "whisper"
        "sounddevice"
        "numpy"
        "numpy.core"
        "numpy.lib"
        "imageio_ffmpeg"
        "torch"
        "torch.nn"
        "torch.nn.functional"
        "tqdm"
        "tiktoken"
        "more_itertools"
        # Gemini AI (bouton AI enhancer + correction dictée)
        "google"
        "google.generativeai"
        "google.ai"
        "google.ai.generativelanguage_v1beta"
        "google.api_core"
        "google.auth"
        "google.protobuf"
        "grpc"
        # App modules
        "Comptes_Rendus"
        "correction"
        "shared_config"
        "config_manager"
        "encrypted_excel_loader"
        "pacs_ris_db"
        "pisum_license_manager"
        "pisum_upgrade_ui"
        "pacs_network_sync"
        "report_editor_controller"
        "report_template_manager"
        "custom_formulas_db"
        "icon_manager"
        "whisper_dictation"
        "supabase_templates_loader"
        "cloud_license_client"
        # UI subpackages
        "ui"
        "ui.app"
        "ui.sidebar"
        "ui.theme"
        "ui.components"
        "ui.components.widgets"
        "ui.views"
        "ui.views.dashboard_view"
        "ui.views.worklist_view"
        "ui.views.report_view"
        "ui.views.report_editor_view"
        "ui.views.templates_view"
        "ui.views.license_view"
        "ui.views.settings_view"
        "ui.views.patients_view"
        "ui.dialogs"
        "ui.dialogs.print_dialog"
        "ui.dialogs.formula_search"
        "ui.dialogs.formula_editor"
        "ui.dialogs.patient_dialog"
        "ui.dialogs.exam_dialog"
        "ui.dialogs.confirm_dialog"
        "ui.dialogs.activation_dialog"
        # ReportLab font support
        "reportlab.pdfbase.ttfonts"
        "reportlab.pdfbase.pdfmetrics"
        "reportlab.pdfbase.cidfonts"
        "reportlab.platypus.flowables"
        "reportlab.platypus.tables"
    )

    CollectSubmodules = @("customtkinter", "PIL", "cryptography", "whisper", "numpy", "torch", "reportlab", "fitz", "win32", "google", "grpc")
    CollectAll        = @("customtkinter", "whisper", "sounddevice", "numpy", "torch", "tiktoken", "tqdm", "more_itertools", "reportlab", "google.generativeai")

    WhisperModelSize = "medium"
    WhisperCacheDir  = "$env:USERPROFILE\.cache\whisper"

    PatchTargets = @{
        DebugFile = "cloud_license_client.py"
        HashFile  = "encrypted_excel_loader.py"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
function Write-Log {
    param([Parameter(Mandatory)][string]$Message, [string]$Level = 'Info')
    $timestamp = Get-Date -Format "HH:mm:ss"
    $colors = @{ Info='Cyan'; Success='Green'; Warning='Yellow'; Error='Red'; Debug='Gray' }
    $icons  = @{ Info="i"; Success="OK"; Warning="!!"; Error="XX"; Debug=">>" }
    $msg = "[$timestamp] [$($icons[$Level])] $Message"
    Write-Host $msg -ForegroundColor $colors[$Level]
    if (-not (Test-Path $Config.LogDir)) { New-Item -Path $Config.LogDir -ItemType Directory -Force | Out-Null }
    Add-Content -Path "$($Config.LogDir)\build_$BuildVersion.log" -Value $msg -Encoding UTF8 -ErrorAction SilentlyContinue
}

# ─────────────────────────────────────────────────────────────────────────────
function Test-Prerequisites {
    Write-Log "Vérification des prérequis..."
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'

    foreach ($cmd in @("python", "pyinstaller")) {
        $v = $null
        try { $v = & $cmd --version 2>&1 | Where-Object { $_ -notmatch '^\s*$' } | Select-Object -First 1 } catch { }
        if ($null -eq $v) { throw "Prérequis manquant : $cmd" }
        Write-Log "  $cmd : $v" -Level Success
    }

    if (-not $SkipPyArmor) {
        $v = $null
        try { $v = & pyarmor --version 2>&1 | Select-Object -First 1 } catch { }
        if ($null -eq $v) { throw "Prérequis manquant : pyarmor" }
        Write-Log "  pyarmor : $v" -Level Success

        if (Test-Path $Config.ClangPath) {
            Write-Log "  clang.exe : OK (BCC activé)" -Level Success
        } else {
            Write-Log "  clang.exe introuvable — --enable-bcc sera ignoré pour éviter l'échec." -Level Warning
            $Config.PyArmorOptions = $Config.PyArmorOptions | Where-Object { $_ -ne "--enable-bcc" }
        }
    }

    if (-not (Test-Path "main.py")) { throw "main.py introuvable — le point d'entrée est requis." }
    Write-Log "  main.py : OK" -Level Success

    $modelFile = Join-Path $Config.WhisperCacheDir "$($Config.WhisperModelSize).pt"
    if (Test-Path $modelFile) {
        $sizeMb = [math]::Round((Get-Item $modelFile).Length / 1MB, 0)
        Write-Log "  Modèle Whisper '$($Config.WhisperModelSize).pt' : $sizeMb Mo [OK]" -Level Success
    } else {
        Write-Log "  Modèle Whisper '$($Config.WhisperModelSize).pt' introuvable — il ne sera pas bundlé." -Level Warning
    }

    if (Test-Path $Config.VcRedistPath) {
        Write-Log "  vc_redist.x64.exe : OK" -Level Success
    } else {
        Write-Log "  vc_redist.x64.exe manquant — installeur sans runtime VC++." -Level Warning
    }

    $ErrorActionPreference = $prevEAP
}

# ─────────────────────────────────────────────────────────────────────────────
$script:OriginalContents = @{}

function Patch-Sources {
    # Patch 1 : désactiver le mode debug
    $debugFile = $Config.PatchTargets.DebugFile
    if (Test-Path $debugFile) {
        $content = Get-Content $debugFile -Raw -Encoding UTF8
        if ($content -match '_DEBUG_MODE\s*=\s*True') {
            $script:OriginalContents[$debugFile] = $content
            Set-Content $debugFile -Value ($content -replace '_DEBUG_MODE\s*=\s*True', '_DEBUG_MODE = False') -Encoding UTF8 -NoNewline
            Write-Log "_DEBUG_MODE mis à False dans $debugFile" -Level Warning
        }
    }

    # Patch 2 : vider le hash
    $hashFile = $Config.PatchTargets.HashFile
    if (Test-Path $hashFile) {
        $content = Get-Content $hashFile -Raw -Encoding UTF8
        $patched = $content -replace '(_EXPECTED_HASH\s*:\s*str\s*=\s*")[^"]*(")', '${1}${2}'
        if ($patched -ne $content) {
            if (-not $script:OriginalContents.ContainsKey($hashFile)) { $script:OriginalContents[$hashFile] = $content }
            Set-Content $hashFile -Value $patched -Encoding UTF8 -NoNewline
            Write-Log "_EXPECTED_HASH vidé dans $hashFile" -Level Warning
        }
    }

    # Patch 3 : supprimer les BOM UTF-8
    $allModules = $Config.CoreModules + $Config.SupabaseLoaderModules + $Config.UnprotectedModules
    foreach ($m in $allModules) {
        if (-not (Test-Path $m)) { continue }
        $bytes = [System.IO.File]::ReadAllBytes($m)
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            if (-not $script:OriginalContents.ContainsKey($m)) {
                $script:OriginalContents[$m] = [System.IO.File]::ReadAllText($m, [System.Text.Encoding]::UTF8)
            }
            [System.IO.File]::WriteAllBytes($m, $bytes[3..($bytes.Length - 1)])
            Write-Log "BOM UTF-8 retiré de $m" -Level Debug
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
function Restore-Sources {
    foreach ($file in $script:OriginalContents.Keys) {
        try {
            Set-Content $file -Value $script:OriginalContents[$file] -Encoding UTF8 -NoNewline
            Write-Log "Source restaurée : $file" -Level Debug
        } catch {
            Write-Log "Impossible de restaurer $file : $_" -Level Warning
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
function Invoke-PyArmor {
    Write-Log "── ÉTAPE 1/3 : Obfuscation PyArmor ──────────────────────────────" -Level Info

    if (-not (Test-Path $Config.DistDir)) { New-Item -Path $Config.DistDir -ItemType Directory -Force | Out-Null }

    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & pyarmor cfg bcc_excludes=""     2>&1 | Out-Null
    & pyarmor cfg rft_excludes=""     2>&1 | Out-Null
    & pyarmor cfg mix_str_excludes="" 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP

    # cloud_license_client : protection maximale
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & pyarmor gen @($Config.PyArmorOptions + @("-O", $Config.DistDir) + $Config.CoreModules)
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prevEAP
    if ($ec -ne 0) { throw "PyArmor erreur CoreModules (code: $ec)" }
    Write-Log "cloud_license_client obfusqué." -Level Success

    # supabase_templates_loader : protection légère (sans BCC)
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & pyarmor gen @($Config.SupabaseLoaderPyArmorOptions + @("-O", $Config.DistDir) + $Config.SupabaseLoaderModules)
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prevEAP
    if ($ec -ne 0) { throw "PyArmor erreur SupabaseLoaderModules (code: $ec)" }
    Write-Log "supabase_templates_loader obfusqué." -Level Success

    # Test imports depuis le dossier obfusqué
    if (-not $SkipTests) {
        Push-Location $Config.DistDir
        try {
            $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
            Set-Content $tmpScript -Value @"
import sys, os
sys.path.insert(0, os.getcwd())
ok = True
for m in ['cloud_license_client', 'supabase_templates_loader']:
    try:
        __import__(m)
        print(f'  OK {m}')
    except Exception as e:
        print(f'  FAIL {m}: {e}')
        ok = False
sys.exit(0 if ok else 1)
"@ -Encoding UTF8
            & python $tmpScript
            if ($LASTEXITCODE -ne 0) { throw "Tests d'import obfusqués échoués" }
            Write-Log "Imports obfusqués OK." -Level Success
        } finally {
            Pop-Location
            if (Test-Path $tmpScript) { Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue }
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
function Invoke-PyInstaller {
    Write-Log "── ÉTAPE 2/3 : Build PyInstaller ────────────────────────────────" -Level Info

    $finalAbs = Join-Path $BuildRoot $Config.FinalDir
    $buildAbs = Join-Path $BuildRoot $Config.BuildDir
    $distAbs  = Join-Path $BuildRoot $Config.DistDir

    $piArgs = @(
        "main.py"                          # ← point d'entrée correct
        "--onedir"
        "--noconsole"
        "--name", "PISUM"
        "--distpath", $finalAbs
        "--workpath", $buildAbs
        "--specpath", $BuildRoot
        "--clean"
    )

    # Icône
    $icoPath = Join-Path $BuildRoot "pisum.ico"
    if (Test-Path $icoPath) { $piArgs += "--icon=$icoPath" }

    # Hidden imports
    foreach ($i in $Config.HiddenImports)    { $piArgs += "--hidden-import=$i" }
    foreach ($m in $Config.CollectSubmodules) { $piArgs += "--collect-submodules=$m" }
    foreach ($m in $Config.CollectAll)        { $piArgs += "--collect-all=$m" }

    # ── Dossier ui/ (package complet) ────────────────────────────────
    $uiDir = Join-Path $BuildRoot "ui"
    if (Test-Path $uiDir) {
        $piArgs += "--add-data=${uiDir};ui"
        Write-Log "Package ui/ inclus." -Level Debug
    }

    # ── Modules obfusqués ────────────────────────────────────────────
    if (-not $SkipPyArmor) {
        foreach ($m in $Config.CoreModules + $Config.SupabaseLoaderModules) {
            $src = Join-Path $distAbs (Split-Path $m -Leaf)
            if (Test-Path $src) {
                $piArgs += "--add-data=${src};."
                Write-Log "Module obfusqué inclus : $(Split-Path $m -Leaf)" -Level Debug
            } else {
                Write-Log "Module obfusqué introuvable (ignoré) : $m" -Level Warning
            }
        }
        # Runtime PyArmor
        $runtime = Get-ChildItem $distAbs -Directory -Filter "pyarmor_runtime_*" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($runtime) {
            $piArgs += "--add-data=$($runtime.FullName);$($runtime.Name)"
            Write-Log "Runtime PyArmor inclus : $($runtime.Name)" -Level Debug
        } else {
            Write-Log "Runtime PyArmor introuvable dans $distAbs — vérifiez l'étape PyArmor." -Level Warning
        }
    } else {
        # Sans PyArmor — ajouter les sources originaux
        foreach ($m in $Config.CoreModules + $Config.SupabaseLoaderModules) {
            $src = Join-Path $BuildRoot $m
            if (Test-Path $src) { $piArgs += "--add-data=${src};." }
        }
    }

    # ── Modules non protégés ─────────────────────────────────────────
    foreach ($m in $Config.UnprotectedModules) {
        $src = Join-Path $BuildRoot $m
        if (Test-Path $src) { $piArgs += "--add-data=${src};." }
        else { Write-Log "Module non protégé introuvable (ignoré) : $m" -Level Warning }
    }

    # ── Ressources ───────────────────────────────────────────────────
    foreach ($r in $Config.Resources.GetEnumerator()) {
        $rPath = Join-Path $BuildRoot $r.Key
        if (Test-Path $rPath) {
            if (Test-Path $rPath -PathType Container) { $piArgs += "--add-data=${rPath};$($r.Value)" }
            else { $piArgs += "--add-data=${rPath};." }
            Write-Log "Ressource incluse : $($r.Key)" -Level Debug
        } else {
            Write-Log "Ressource introuvable (ignorée) : $($r.Key)" -Level Warning
        }
    }

    # ── Modèle Whisper ───────────────────────────────────────────────
    $modelFile = Join-Path $Config.WhisperCacheDir "$($Config.WhisperModelSize).pt"
    if (Test-Path $modelFile) {
        $piArgs += "--add-data=${modelFile};whisper_models"
        Write-Log "Modèle Whisper inclus : $($Config.WhisperModelSize).pt" -Level Success
    } else {
        Write-Log "Modèle Whisper introuvable — dictation non disponible dans le build." -Level Warning
    }

    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & pyinstaller @piArgs
    $ec = $LASTEXITCODE; $ErrorActionPreference = $prevEAP
    if ($ec -ne 0) { throw "PyInstaller erreur (code: $ec)" }
    Write-Log "Build PyInstaller terminé." -Level Success
}

# ─────────────────────────────────────────────────────────────────────────────
function Inject-ExeHash {
    Write-Log "── ÉTAPE 3/3 : Injection du hash EXE ───────────────────────────" -Level Info
    $exePath = Join-Path $BuildRoot "$($Config.FinalDir)\PISUM\PISUM.exe"
    if (-not (Test-Path $exePath)) {
        Write-Log "EXE introuvable, hash non injecté." -Level Warning
        return $null
    }

    $tmpScript = $null
    try {
        $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
        Set-Content $tmpScript -Value @"
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    h.update(f.read(524288))
print(h.hexdigest())
"@ -Encoding UTF8
        $exeHash = (& python $tmpScript $exePath).Trim()
    } finally {
        if ($tmpScript -and (Test-Path $tmpScript)) { Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue }
    }

    if ($exeHash -match '^[a-f0-9]{64}$') {
        $hashFile = Join-Path $BuildRoot $Config.PatchTargets.HashFile
        if (Test-Path $hashFile) {
            $content = Get-Content $hashFile -Raw -Encoding UTF8
            Set-Content $hashFile -Value ($content -replace '(_EXPECTED_HASH\s*:\s*str\s*=\s*")[^"]*(")', "`${1}$exeHash`${2}") -Encoding UTF8 -NoNewline
            $script:OriginalContents[$hashFile] = Get-Content $hashFile -Raw -Encoding UTF8
        }
        Write-Log "Hash EXE injecté : $($exeHash.Substring(0,16))...$($exeHash.Substring(48))" -Level Success
        return $exeHash
    } else {
        Write-Log "Hash EXE invalide : '$exeHash'" -Level Error
        return $null
    }
}

# ═══════════════════════════════════════════════════════════════════
#  PIPELINE
# ═══════════════════════════════════════════════════════════════════
try {
    Set-Location $BuildRoot
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  BUILD PISUM — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
    if ($SkipPyArmor) { Write-Host "  MODE : PyInstaller seul (sans obfuscation)" -ForegroundColor Yellow }
    else              { Write-Host "  MODE : PyArmor + PyInstaller" -ForegroundColor Cyan }
    Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host ""

    Test-Prerequisites
    Patch-Sources

    if (-not $SkipPyArmor) {
        Invoke-PyArmor
    } else {
        Write-Log "PyArmor ignoré (-SkipPyArmor)." -Level Warning
    }

    Invoke-PyInstaller
    $exeHash = Inject-ExeHash

    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  BUILD TERMINÉ AVEC SUCCÈS" -ForegroundColor Green
    Write-Host "  EXE  : $($Config.FinalDir)\PISUM\PISUM.exe" -ForegroundColor Green
    Write-Host "  Logs : $($Config.LogDir)\build_$BuildVersion.log" -ForegroundColor Green
    if ($exeHash) { Write-Host "  SHA256 (512KB) : $($exeHash.Substring(0,16))..." -ForegroundColor Green }
    Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
    exit 0

} catch {
    Write-Log "ERREUR FATALE : $_" -Level Error
    Write-Host ""
    Write-Host "  BUILD ÉCHOUÉ : $_" -ForegroundColor Red
    Write-Host ""
    exit 1

} finally {
    Restore-Sources
}
