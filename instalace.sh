#!/usr/bin/env bash
# Instalace Mluvky pro macOS (Apple Silicon). Spusť v Terminálu:
#   curl -fsSL https://raw.githubusercontent.com/malaveselahranolka/mluvka/main/instalace.sh | bash
# Druhé spuštění aktualizuje kód a nestahuje znovu model.
set -euo pipefail

ZDROJ_KODU="https://github.com/malaveselahranolka/mluvka/archive/refs/heads/main.tar.gz"
MODEL_URL="https://huggingface.co/moravianai/CzechSpeech/resolve/main/ggml-czechspeech-q5_0.bin"
MODEL_TREE="https://huggingface.co/api/models/moravianai/CzechSpeech/tree/main"
MODEL_SOUBOR="ggml-czechspeech-q5_0.bin"
MODEL_BAJTU=574041195

DATA="$HOME/Library/Application Support/Mluvka"
APP="$DATA/app"
VENV="$DATA/venv"
MODELY="$DATA/models"
APP_BUNDLE="$HOME/Applications/Mluvka.app"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mluvka-install.XXXXXX")"

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

krok() { printf '\n\033[36m[%s/7] %s\033[0m\n' "$1" "$2"; }
info() { printf '      %s\n' "$1"; }
hotovo() { printf '\033[32m      %s\033[0m\n' "$1"; }
varovani() { printf '\033[33m      %s\033[0m\n' "$1"; }

file_size_bytes() {
    local target="$1"
    if stat -f %z "$target" >/dev/null 2>&1; then
        stat -f %z "$target"
    else
        wc -c < "$target" | tr -d '[:space:]'
    fi
}

sha256_lower() {
    local target="$1"
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$target" | awk '{print tolower($1)}'
    else
        sha256sum "$target" | awk '{print tolower($1)}'
    fi
}

stahni() {
    local url="$1"
    local cil="$2"
    mkdir -p "$(dirname "$cil")"
    curl -fL --retry 5 --retry-delay 2 -C - --progress-bar -o "$cil" "$url"
}

printf '\033[35mMluvka: soukromé diktování češtiny pro macOS\033[0m\n'

# --- 1. Prostředí a Homebrew ---------------------------------------------------
krok 1 "Kontrola macOS a Homebrew"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Chyba: Tento instalátor je určený pro macOS." >&2
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "arm64" ]]; then
    varovani "Varování: Detekován Mac s architekturou $ARCH (mimo Apple Silicon). Přepis bude pomalý, ale pokračuji."
fi

if ! command -v brew >/dev/null 2>&1; then
    if [[ -x "/opt/homebrew/bin/brew" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x "/usr/local/bin/brew" ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi

if ! command -v brew >/dev/null 2>&1; then
    echo "Chyba: Chybí správce balíčků Homebrew." >&2
    echo "Nainstaluj ho v Terminálu tímto příkazem a pak instalaci Mluvky spusť znovu:" >&2
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
    exit 1
fi

BREW_PREFIX="$(brew --prefix)"
hotovo "macOS ($ARCH), Homebrew: $BREW_PREFIX"
info "Data: $DATA"
mkdir -p "$DATA" "$MODELY"

# --- 2. Systémové balíčky -----------------------------------------------------
krok 2 "whisper-cpp a Python 3.12 (Homebrew)"
brew install whisper-cpp python@3.12 python-tk@3.12

PY_BIN=""
for cand in "$BREW_PREFIX/opt/python@3.12/bin/python3.12" "$BREW_PREFIX/bin/python3.12" "$(command -v python3.12 || true)"; do
    if [[ -n "$cand" && -x "$cand" ]]; then
        PY_BIN="$cand"
        break
    fi
done
if [[ -z "$PY_BIN" ]]; then
    echo "Chyba: Nepodařilo se najít Python 3.12 po instalaci přes Homebrew." >&2
    exit 1
fi
if ! command -v whisper-server >/dev/null 2>&1 && [[ ! -x "$BREW_PREFIX/bin/whisper-server" ]]; then
    echo "Chyba: Chybí binárka whisper-server z balíčku whisper-cpp." >&2
    exit 1
fi
hotovo "Python: $PY_BIN, whisper-server: ${BREW_PREFIX}/bin/whisper-server"

# --- 3. Kód Mluvky ------------------------------------------------------------
krok 3 "Kód Mluvky"
TAR_KOD="$TMP_DIR/mluvka.tar.gz"
stahni "$ZDROJ_KODU" "$TAR_KOD"
ROZBALENO="$TMP_DIR/kod"
mkdir -p "$ROZBALENO"
tar -xzf "$TAR_KOD" -C "$ROZBALENO"
KOREN="$(find "$ROZBALENO" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "$KOREN" ]]; then
    echo "Chyba: Rozbalený archiv kódu je prázdný." >&2
    exit 1
fi

mkdir -p "$APP"
# Přepisujeme jen kód. config.json, historie a modely leží v DATA, ne v APP.
while IFS= read -r -d '' item; do
    name="$(basename "$item")"
    rm -rf "${APP:?}/$name"
    mv "$item" "$APP/$name"
done < <(find "$KOREN" -mindepth 1 -maxdepth 1 -print0)
hotovo "Kód je v $APP"

# --- 4. Virtuální prostředí ---------------------------------------------------
krok 4 "Python balíčky"
if [[ ! -x "$VENV/bin/python" ]]; then
    "$PY_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$APP/requirements.txt"
hotovo "Balíčky nainstalovány"

# --- 5. Model CzechSpeech -----------------------------------------------------
krok 5 "Přepisový model CzechSpeech"
info "Model CzechSpeech (Moravian AI) je pod licencí CC BY-NC 4.0: zdarma pro nekomerční použití."
MODEL_CIL="$MODELY/$MODEL_SOUBOR"

OCEKAVANY="$("$VENV/bin/python" - "$MODEL_TREE" "$MODEL_SOUBOR" <<'PY'
import json, sys, urllib.request
tree_url, target = sys.argv[1], sys.argv[2]
try:
    with urllib.request.urlopen(tree_url, timeout=15) as r:
        data = json.load(r)
    for f in data:
        if isinstance(f, dict) and f.get("path") == target:
            oid = (f.get("lfs") or {}).get("oid")
            if oid:
                print(str(oid).strip().lower())
                break
except Exception:
    pass
PY
)" || OCEKAVANY=""

if [[ -n "$OCEKAVANY" ]]; then
    info "Očekávaný SHA256: ${OCEKAVANY:0:16}..."
else
    info "SHA256 se nepodařilo zjistit, kontrola se přeskočí."
fi

pokus=0
while true; do
    pokus=$((pokus + 1))
    if [[ -f "$MODEL_CIL" ]]; then
        if [[ -n "$OCEKAVANY" ]]; then
            SKUTECNY="$(sha256_lower "$MODEL_CIL")"
            if [[ "$SKUTECNY" == "$OCEKAVANY" ]]; then
                hotovo "Model je stažený a ověřený"
                break
            fi
            SIZE_NOW="$(file_size_bytes "$MODEL_CIL")"
            if [[ "$pokus" -eq 1 && "$SIZE_NOW" -gt 0 && "$SIZE_NOW" -lt "$MODEL_BAJTU" ]]; then
                info "Nalezen neúplný model ($SIZE_NOW B), zkouším navázat stahování."
            else
                info "Soubor modelu neodpovídá, stahuji znovu."
                rm -f "$MODEL_CIL"
            fi
        else
            SIZE_NOW="$(file_size_bytes "$MODEL_CIL")"
            if [[ "$SIZE_NOW" -eq "$MODEL_BAJTU" ]]; then
                hotovo "Model už existuje, přeskakuji"
                break
            elif [[ "$pokus" -eq 1 && "$SIZE_NOW" -gt 0 && "$SIZE_NOW" -lt "$MODEL_BAJTU" ]]; then
                info "Nalezen neúplný model ($SIZE_NOW B), zkouším navázat stahování."
            else
                info "Soubor modelu má chybnou velikost, stahuji znovu."
                rm -f "$MODEL_CIL"
            fi
        fi
    fi

    if [[ "$pokus" -gt 2 ]]; then
        echo "Chyba: Model se nepodařilo stáhnout správně." >&2
        exit 1
    fi

    info "Stahuji 574 MB..."
    stahni "$MODEL_URL" "$MODEL_CIL"
    if [[ -z "$OCEKAVANY" ]]; then
        SIZE_NOW="$(file_size_bytes "$MODEL_CIL")"
        if [[ "$SIZE_NOW" -ne "$MODEL_BAJTU" ]]; then
            echo "Chyba: Stažený model má špatnou velikost ($SIZE_NOW B místo $MODEL_BAJTU B)." >&2
            exit 1
        fi
        hotovo "Model stažený"
        break
    fi
done

# --- 6. Aplikační balík Mluvka.app --------------------------------------------
krok 6 "Aplikační balík ~/Applications/Mluvka.app"
mkdir -p "$APP_BUNDLE/Contents/MacOS"

cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Mluvka</string>
    <key>CFBundleDisplayName</key>
    <string>Mluvka</string>
    <key>CFBundleIdentifier</key>
    <string>cz.mluvka.app</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleExecutable</key>
    <string>Mluvka</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Mluvka potřebuje přístup k mikrofonu pro lokální přepis řeči na text.</string>
</dict>
</plist>
PLIST

cat > "$APP_BUNDLE/Contents/MacOS/Mluvka" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
DATA_DIR="${MLUVKA_DATA:-$HOME/Library/Application Support/Mluvka}"
cd "$DATA_DIR/app"
exec "$DATA_DIR/venv/bin/python" -m mluvka "$@"
LAUNCHER
chmod +x "$APP_BUNDLE/Contents/MacOS/Mluvka"
hotovo "Vytvořeno: $APP_BUNDLE"

# --- 7. Hotovo a oprávnění ----------------------------------------------------
krok 7 "Hotovo"
printf '\n\033[32mHotovo! Mluvku spustíš přes ~/Applications/Mluvka.app (nebo Spotlight: Mluvka).\033[0m\n'
printf 'Výchozí klávesa pro diktát je Fn / 🌐.\n\n'
cat <<'POKYNY'
Po prvním spuštění povol tato 3 oprávnění v Nastavení systému → Soukromí a zabezpečení:
  1. Mikrofon          — povol Mluvku / Python pro záznam hlasu
  2. Zpřístupnění      — povol pro vkládání přepisu (Cmd+V)
  3. Sledování vstupu  — povol pro zachycení globální klávesy diktátu

Klávesa 🌐:
  Nastavení systému → Klávesnice → Stiskem 🌐 → Nedělat nic
  (jinak klávesa Fn při stisku otevírá výběr emodži nebo přepíná vstup).
POKYNY

if [[ -t 0 ]]; then
    read -r -p $'\nSpustit Mluvku teď? [a/N] ' odpoved || odpoved=""
    if [[ "$odpoved" =~ ^[aAyY] ]]; then
        open "$APP_BUNDLE"
    fi
fi
