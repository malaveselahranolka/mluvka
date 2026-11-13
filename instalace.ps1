# Instalace Mluvky. Spusť v PowerShellu:
#   irm https://raw.githubusercontent.com/malaveselahranolka/mluvka/main/instalace.ps1 | iex
# Druhé spuštění aktualizuje kód a nestahuje znovu model ani whisper.
[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ZDROJ_KODU = "https://github.com/malaveselahranolka/mluvka/archive/refs/heads/main.zip"
$WHISPER_CUDA = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-win-cuda-12.4.0-x64.zip"
$WHISPER_CPU = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip"
# SHA256 z GitHub API (pole digest u assetu release b5454), overeno 13. 11. 2026
$WHISPER_CUDA_SHA = "afef0b881c500958921c3f5523b50e59ee2ec9b6f5cbd25b324c51ed308a957a"
$WHISPER_CPU_SHA  = "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"
$MODEL_URL = "https://huggingface.co/moravianai/CzechSpeech/resolve/main/ggml-czechspeech-q5_0.bin"
$MODEL_TREE = "https://huggingface.co/api/models/moravianai/CzechSpeech/tree/main"
$MODEL_SOUBOR = "ggml-czechspeech-q5_0.bin"
$MODEL_BAJTU = 574041195

function Krok([int]$n, [string]$text) { Write-Host "`n[$n/8] $text" -ForegroundColor Cyan }
function Info([string]$text) { Write-Host "      $text" -ForegroundColor Gray }
function Hotovo([string]$text) { Write-Host "      $text" -ForegroundColor Green }

function Stahni([string]$url, [string]$cil) {
    if ($DryRun) {
        $kod = try { (Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -MaximumRedirection 5).StatusCode } catch { "chyba: $($_.Exception.Message)" }
        Info "DryRun HEAD $url => $kod"
        return
    }
    $slozka = Split-Path -Parent $cil
    if (-not (Test-Path $slozka)) { New-Item -ItemType Directory -Force -Path $slozka | Out-Null }
    & curl.exe -L --fail --retry 5 --retry-delay 2 -C - --progress-bar -o $cil $url
    if ($LASTEXITCODE -ne 0) { throw "Stahování selhalo ($url)." }
}

function Rozbal([string]$zip, [string]$cil) {
    if (Test-Path $cil) { Remove-Item -Recurse -Force $cil }
    Expand-Archive -Path $zip -DestinationPath $cil -Force
}

# --- 0. Prostředí -------------------------------------------------------------
Write-Host "Mluvka: soukrome diktovani cestiny pro Windows" -ForegroundColor Magenta

if (-not [Environment]::Is64BitOperatingSystem) { throw "Mluvka potrebuje 64bitove Windows." }
if ($PSVersionTable.PSVersion.Major -lt 5) { throw "Mluvka potrebuje PowerShell 5.1 nebo novejsi." }

$DATA = Join-Path $env:LOCALAPPDATA "Mluvka"
$APP = Join-Path $DATA "app"
$VENV = Join-Path $DATA "venv"
$BIN = Join-Path $DATA "bin\whisper"
$MODELY = Join-Path $DATA "models"
$TMP = Join-Path $env:TEMP ("mluvka-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

Krok 1 "Kontrola Windows a PowerShellu"
Hotovo "Windows 64bit, PowerShell $($PSVersionTable.PSVersion)"
Info "Data: $DATA"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $DATA, $TMP | Out-Null }

# --- 2. Python ----------------------------------------------------------------
Krok 2 "Python 3.11 nebo novejsi"
function Najdi-Python {
    foreach ($kandidat in @(@("py", @("-3", "-c", "import sys;print(sys.executable)")), @("python", @("-c", "import sys;print(sys.executable)")))) {
        $exe = $kandidat[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $cesta = & $exe @($kandidat[1]) 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $cesta) { continue }
            $verze = & $cesta -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            $d = $verze -split '\.'
            if ([int]$d[0] -ge 3 -and [int]$d[1] -ge 11) { return @($cesta.Trim(), $verze.Trim()) }
        } catch { continue }
    }
    return $null
}

$py = Najdi-Python
if (-not $py) {
    if ($DryRun) {
        Info "DryRun: Python by se instaloval pres winget"
    } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
        Info "Python nenalezen, instaluji pres winget..."
        & winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $py = Najdi-Python
    }
    if (-not $py -and -not $DryRun) {
        throw "Nepodarilo se najit Python 3.11+. Nainstaluj ho z https://www.python.org/downloads/ a spust instalaci znovu."
    }
}
if ($py) { Hotovo "Python $($py[1]): $($py[0])" }

# --- 3. Kód -------------------------------------------------------------------
Krok 3 "Kod Mluvky"
$zipKod = Join-Path $TMP "mluvka.zip"
Stahni $ZDROJ_KODU $zipKod
if (-not $DryRun) {
    $rozbaleno = Join-Path $TMP "kod"
    Rozbal $zipKod $rozbaleno
    $koren = Get-ChildItem -Path $rozbaleno -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $APP | Out-Null
    # Prepisujeme jen kod. config.json, historie a modely lezi v DATA, ne v APP.
    foreach ($polozka in Get-ChildItem -Path $koren.FullName) {
        $cil = Join-Path $APP $polozka.Name
        if (Test-Path $cil) { Remove-Item -Recurse -Force $cil }
        Move-Item -Path $polozka.FullName -Destination $cil
    }
    Hotovo "Kod je v $APP"
}

# --- 4. Virtuální prostředí ---------------------------------------------------
Krok 4 "Python balicky"
if ($DryRun) {
    Info "DryRun: venv v $VENV + pip install -r requirements.txt"
} else {
    if (-not (Test-Path (Join-Path $VENV "Scripts\python.exe"))) {
        & $py[0] -m venv $VENV
        if ($LASTEXITCODE -ne 0) { throw "Vytvoreni venv selhalo." }
    }
    $venvPy = Join-Path $VENV "Scripts\python.exe"
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -r (Join-Path $APP "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Instalace balicku selhala." }
    Hotovo "Balicky nainstalovany"
}

# --- 5. whisper.cpp -----------------------------------------------------------
Krok 5 "Prepisovy engine whisper.cpp"
$maNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
$whisperUrl = if ($maNvidia) { $WHISPER_CUDA } else { $WHISPER_CPU }
Info $(if ($maNvidia) { "Nalezena grafika NVIDIA, stahuji CUDA verzi (685 MB, chvili to potrva)." } else { "Grafika NVIDIA nenalezena, stahuji verzi pro procesor (9 MB)." })

if ((Test-Path (Join-Path $BIN "whisper-server.exe")) -and -not $DryRun) {
    Hotovo "whisper-server uz je nainstalovany, preskakuji"
} else {
    $zipW = Join-Path $TMP "whisper.zip"
    Stahni $whisperUrl $zipW
    if (-not $DryRun) {
        $ocekW = if ($maNvidia) { $WHISPER_CUDA_SHA } else { $WHISPER_CPU_SHA }
        if ((Get-FileHash $zipW -Algorithm SHA256).Hash.ToLower() -ne $ocekW) {
            throw "Archiv whisper.cpp neodpovida ocekavanemu SHA256, instalace konci."
        }
        $rozW = Join-Path $TMP "whisper"
        Rozbal $zipW $rozW
        $exe = Get-ChildItem -Path $rozW -Filter "whisper-server.exe" -Recurse | Select-Object -First 1
        if (-not $exe) { throw "V archivu whisper.cpp chybi whisper-server.exe." }
        New-Item -ItemType Directory -Force -Path $BIN | Out-Null
        Get-ChildItem -Path $exe.Directory.FullName -File | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination (Join-Path $BIN $_.Name) -Force
        }
        Hotovo "whisper-server je v $BIN"
    }
}

# --- 6. Model -----------------------------------------------------------------
Krok 6 "Prepisovy model CzechSpeech"
Info "Model CzechSpeech (Moravian AI) je pod licenci CC BY-NC 4.0: zdarma pro nekomercni pouziti."
$modelCil = Join-Path $MODELY $MODEL_SOUBOR

function Ocekavany-Hash {
    try {
        $strom = Invoke-RestMethod -Uri $MODEL_TREE -UseBasicParsing
        foreach ($f in $strom) { if ($f.path -eq $MODEL_SOUBOR) { return $f.lfs.oid } }
    } catch { return $null }
    return $null
}

$ocekavany = Ocekavany-Hash
if ($ocekavany) { Info "Ocekavany SHA256: $($ocekavany.Substring(0,16))..." } else { Info "SHA256 se nepodarilo zjistit, kontrola se preskoci." }

if ($DryRun) {
    Stahni $MODEL_URL $modelCil
} else {
    $pokus = 0
    while ($true) {
        $pokus++
        $maSoubor = Test-Path $modelCil
        if ($maSoubor -and $ocekavany) {
            $skutecny = (Get-FileHash -Path $modelCil -Algorithm SHA256).Hash.ToLower()
            if ($skutecny -eq $ocekavany.ToLower()) { Hotovo "Model je stazeny a overeny"; break }
            Info "Soubor modelu neodpovida, stahuji znovu."
            Remove-Item -Force $modelCil
        } elseif ($maSoubor) {
            if ((Get-Item $modelCil).Length -eq $MODEL_BAJTU) { Hotovo "Model uz existuje, preskakuji"; break }
            Info "Soubor modelu je neuplny, stahuji znovu."
            Remove-Item -Force $modelCil
        }
        if ($pokus -gt 2) { throw "Model se nepodarilo stahnout spravne." }
        Info "Stahuji 574 MB..."
        Stahni $MODEL_URL $modelCil
        if (-not $ocekavany) {
            if ((Get-Item $modelCil).Length -ne $MODEL_BAJTU) { throw "Stazeny model ma spatnou velikost." }
            Hotovo "Model stazeny"; break
        }
    }
}

# --- 7. Zástupci --------------------------------------------------------------
Krok 7 "Zastupci na Plose a v nabidce Start"
if ($DryRun) {
    Info "DryRun: python -m mluvka.zastupci v $APP"
} else {
    Push-Location $APP
    try {
        & (Join-Path $VENV "Scripts\python.exe") -m mluvka.zastupci
        if ($LASTEXITCODE -ne 0) { throw "Vytvoreni zastupcu selhalo." }
    } finally { Pop-Location }
    Hotovo "Zastupci vytvoreni"
}

# --- 8. Hotovo ----------------------------------------------------------------
Krok 8 "Hotovo"
if (-not $DryRun -and (Test-Path $TMP)) { Remove-Item -Recurse -Force $TMP -ErrorAction SilentlyContinue }

if ($DryRun) {
    Write-Host "`nDryRun dokoncen, nic se neinstalovalo." -ForegroundColor Yellow
    return
}

Write-Host "`nHotovo. Mluvku spustis zastupcem na Plose. Diktujes drzenim praveho Ctrl." -ForegroundColor Green
$odpoved = Read-Host "Spustit Mluvku hned? [a/N]"
if ($odpoved -match '^[aAyY]') {
    Start-Process -FilePath (Join-Path $VENV "Scripts\pythonw.exe") -ArgumentList "-m", "mluvka", "--okno" -WorkingDirectory $APP
}
