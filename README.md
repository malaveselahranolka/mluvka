# Mluvka

Soukromé diktování češtiny pro Windows. Držíš klávesu, mluvíš, pustíš a text se vloží tam, kde máš kurzor.
Přepis dělá model **CzechSpeech** na tvé grafice (RTX 4050), zvuk nikdy neodchází z počítače.
Text pak může uhladit **Claude Haiku 5.5** (výplňová slova, přeřeknutí, odrážky) a umí i příkazy nad označeným textem.
To je jediná část, kde text odchází ven (k Anthropicu), a jen když nastavíš API klíč. Druhou volitelnou výjimkou je kanál (webhook).

Web projektu: <https://malaveselahranolka.github.io/mluvka/>

## Instalace

Otevři PowerShell a vlož tenhle příkaz:

```powershell
irm https://raw.githubusercontent.com/malaveselahranolka/mluvka/main/instalace.ps1 | iex
```

Co skript udělá:

1. Ověří Windows 64bit a PowerShell 5.1+.
2. Najde Python 3.11+, a když chybí, nabídne instalaci přes `winget`.
3. Stáhne kód do `%LOCALAPPDATA%\Mluvka\app`.
4. Vytvoří `%LOCALAPPDATA%\Mluvka\venv` a nainstaluje balíčky.
5. Stáhne whisper.cpp b5454 (s grafikou NVIDIA verzi pro CUDA 12.4, jinak pro procesor) do `%LOCALAPPDATA%\Mluvka\bin\whisper`.
6. Stáhne model `ggml-czechspeech-q5_0.bin` (574 MB) a ověří jeho SHA256.
7. Vytvoří zástupce na Ploše a v nabídce Start.

**Požadavky:** Windows 10 nebo 11 64bit, asi 2,5 GB volného místa. Grafika NVIDIA není nutná, ale bez ní přepis běží na procesoru a trvá déle.

**Aktualizace:** spusť stejný příkaz znovu. Přepíše se jen kód, model ani nastavení se nestahují a nemažou.

Model CzechSpeech je pod licencí CC BY-NC 4.0, tedy zdarma pro nekomerční použití.

## macOS (beta)

Experimentální podpora pro **macOS 14+ (Apple Silicon)**. Zatím nebyla testovaná na skutečném Macu.

Otevři Terminál a spusť:

```bash
curl -fsSL https://raw.githubusercontent.com/malaveselahranolka/mluvka/main/instalace.sh | bash
```

Skript přes Homebrew nainstaluje `whisper-cpp` (Metal), Python 3.12 a `python-tk@3.12`, stáhne kód a model `ggml-czechspeech-q5_0.bin` do `~/Library/Application Support/Mluvka` a vytvoří `~/Applications/Mluvka.app`.

Po instalaci:

1. V **Nastavení systému → Soukromí a zabezpečení** povol pro Mluvku / Python tato 3 oprávnění: **Mikrofon**, **Zpřístupnění** (vkládání přepisu) a **Sledování vstupu** (zachycení globální klávesy).
2. V **Nastavení systému → Klávesnice** nastav **Stiskem 🌐 → Nedělat nic**, aby výchozí klávesa pro diktát (**Fn / 🌐**) neotevírala výběr emodži.

**Co na Macu zatím nefunguje:**

- učení oprav z cizích aplikací,
- lokální doladění modelu (vyžaduje Windows s NVIDIA GPU; měření přesnosti modelu funguje).

## Rychlý test (5 minut)

1. Otevři **Mluvka** z plochy nebo z nabídky Start (zástupce s ikonou švestkového kolečka).
   Otevře se okno aplikace a vpravo dole v liště naskočí ikona. Do pár sekund se objeví „Mluvka je připravená.“
   Okno můžeš zavřít, Mluvka běží dál v liště (kliknutím na ikonu se okno znovu otevře).
2. Otevři Poznámkový blok a klikni do něj.
3. **Drž pravý Ctrl**, řekni větu a pusť. Text se vloží.
4. Delší diktát: **dvakrát ťukni** na pravý Ctrl. Nahrává se bez držení (volné ruce), ukončíš to dalším stiskem pravého Ctrl.
5. **Esc** během nahrávání zruší diktát, nic se nevloží.
6. **Alt+Shift+Z** vloží poslední přepis znovu.
7. Příkaz (potřebuje API klíč pro Claude, viz níž): označ text, **drž Shift + pravý Ctrl**, řekni pokyn
   („přepiš to formálněji“, „udělej z toho odrážky“) a pusť. Výsledek nahradí označený text.

## Co vyzkoušet

- [ ] Minuta souvislého mluvení (volné ruce) se přepíše za pár sekund (měřeno: viz „Přesnost a rychlost“).
- [ ] Čísla, data a jména, diakritika.
- [ ] Řekni „nový řádek“ nebo „nový odstavec“, vloží se zalomení.
- [ ] Slovník: přidej vlastní slova (třeba přes prompt pro AI asistenta) a řekni „Claude Code“, „AGENTS.md“, „Obsidian“.
- [ ] Historie: uprav v okně přepsané slovo a podívej se na návrh „Přidat do Oprav“.
- [ ] Mikrofon: v okně pod **Nastavení** nebo v liště Mluvka → Mikrofon → vyber vstup.
- [ ] Soukromí: ověření, že nic neodchází (níž).

## Okno Mluvky

Okno otevřeš kliknutím na ikonu Mluvky v oznamovací oblasti lišty (nebo spuštěním zástupce Mluvky). Změny se ukládají hned a běžící Mluvka je převezme do 2 sekund.

| sekce | co tam je |
|---|---|
| **Přehled** | jak diktovat (klávesa pro diktát, volné ruce, `Esc`, příkaz pro Claude), 4 statistiky za posledních 7 dní (slova, diktáty, ušetřený čas, rychlost mluvení) a posledních 5 diktátů |
| **Historie** | všechny diktáty po dnech, vyhledávání (bez ohledu na velikost písmen a diakritiku), rozkliknutí celého textu, kopírování, úprava přepisu (ukládá do Paměti chyb a navrhuje „Přidat do Oprav“), mazání i „Smazat vše“ (načítání po 100) |
| **Slovník** | karta **Doplnit slovník pomocí AI** (prompt pro tvého AI asistenta: zkopírovat prompt → vložit JSON odpověď → náhled → jedním klikem importovat), **Slova, která má Mluvka znát** (+ přepínač posílání modelu jako nápovědy), **Opravy** („klód“ → „Claude“) a **Paměť chyb** |
| **Zkratky** | seznam dvojic „Když řeknu“ → „Vložený text“ (řekneš celou zkratku a vloží se celý uložený víceřádkový text, např. podpis nebo šablona) |
| **Trénink** | nahrávání 98 vět vlastním hlasem, měření lokální přesnosti (WER), volitelné ukládání zvuku běžných diktátů, příprava lokálního balíčku pro darování nahrávek a přepínání **Osobního modelu** |
| **AI úpravy** | zapnutí/vypnutí uhlazování přes Claude Haiku 5.5, vložení a ověření API klíče, přehled, že odchází jen text a nikdy zvuk |
| **Nastavení** | klávesa pro diktát (Pravý Ctrl, Pravý Shift, Caps Lock, F9), mikrofon, zvuky, spouštět po přihlášení, kanál (HTTPS webhook + testovací zpráva) a jak dlouho držet historii (1 / 7 / 30 / 90 / 365 dní) |
| **Soukromí** | přehled toho, co zůstává v PC a co může odejít podle nastavení, stav síťové pojistky (seznam povolených hostitelů), tlačítka „Otevřít složku s daty“ a „Smazat historii“ |

### Doplnění slovníku pomocí AI asistenta

V sekci **Slovník** je karta **Doplnit slovník pomocí AI** (prompt pro tvého AI asistenta):

1. Klikni na **Zkopírovat prompt** a pošli ho svému AI asistentovi (např. tam, kde už zná tvoje projekty, jména kolegů a pracovní pojmy).
2. AI vrátí JSON se slovy a častými omyly při přepisu. Vlož odpověď do pole v Mluvce a klikni na **Načíst**.
3. Mluvka zobrazí náhled připravených slov a oprav (duplicity, prázdné hodnoty a shodné dvojice rovnou přeskočí; při chybném tvaru JSONu ohlásí chybu).
4. Jedním klikem na **Přidat vše** celý výběr naimportuješ.

## Paměť chyb

Když v **Historii** opravíš přepis diktátu (ikona tužky → úprava textu → `Ctrl+Enter` nebo uložit):

- U záznamu přibude štítek **opraveno** (původní přepis zůstává uložený v historii).
- Mluvka porovná původní a opravený text po slovech (náhrady do 4 slov na každé straně) a uloží zjištěné dvojice do `%LOCALAPPDATA%\Mluvka\pamet_chyb.jsonl`.
- Hned pod upraveným záznamem nabídne výzvu „Přidat do Oprav: …?“ s tlačítkem **Přidat** (pokud už taková oprava v `slovnik.opravy` není).
- Přehled posledních až 20 dvojic najdeš pod **Slovník → Paměť chyb** včetně počtu výskytů; od dvou opakování tam má dvojice tlačítko **Přidat do Oprav** a paměť můžeš kdykoliv vymazat.
- Pokud máš zapnuté **AI úpravy**, nejnovější příklady z Paměti chyb (posledních až **5** úprav) se posílají **Claude Haiku 5.5** jako **text** spolu s přepisem, aby se při čištění řídil tvými konkrétními opravami. **Zvuk se nikdy neposílá.**

Mluvka se navíc umí **učit automaticky z oprav přímo v jiných aplikacích** (Nastavení → **Učit se z mých oprav v jiných aplikacích**, výchozí zapnuto). Po vložení diktátu sleduje jen fokusované textové pole dané aplikace po dobu až 120 s (nikdy ne pole pro heslo a nic neodesílá ani neukládá cizí text). Když některé vložené slovo opravíš, dvojice podobných slov se rovnou uloží do Paměti chyb i do **Slovník → Opravy**, záznam v **Historii** dostane štítek **opraveno** a Mluvka ukáže krátké upozornění `Naučeno: …`.

## Trénink

Stránka **Trénink** slouží ke změření přesnosti na tvém vlastním hlase, přípravě nahrávek pro případné doladění modelu a přepínání modelů:

- **Nahrávání vět**: sada **98** českých vět (`mluvka\trenink_vety.txt`). Větu nahraješ přes **Nahrát** → **Hotovo** (`Esc` nahrávání zruší), můžeš si ji přehrát, přeskočit nebo nahrát znovu. Nahrávky (16 kHz mono WAV) se ukládají lokálně do `%LOCALAPPDATA%\Mluvka\trenink\vety\` a indexují do `%LOCALAPPDATA%\Mluvka\trenink\vety.tsv`.
- **Měření přesnosti (WER)**: jakmile nahraješ alespoň **10** vět, odemkne se tlačítko **Změřit**. Mluvka pustí nahrávky čistým lokálním přepisem přes `whisper-server.exe` (bez slovníku a bez AI úprav), spočítá chybovost slov (WER), výsledek uloží do `trenink\mereni.json` a vypíše až 5 nejhorších vět s vyznačenými rozdíly.
- **Ukládat zvuk diktátů**: volitelný přepínač (ve výchozím stavu **vypnutý**). Po zapnutí ukládá WAV nahrávky běžných diktátů do `%LOCALAPPDATA%\Mluvka\trenink\diktaty\`. Při smazání položky nebo vypršení historie se smažou i odpovídající WAV soubory.
- **Osobní model**: přepínač modelů (`*.bin`) uložených ve složce `%LOCALAPPDATA%\Mluvka\models\`. U každého modelu zobrazí i poslední naměřený WER ze souboru `trenink\vysledky.json` (Common Voice, FLEURS, Vlastní věty), pokud je změřený. Po přepnutí se nový model načte při další diktaci.
- **Darování nahrávek** (sekce **Darovat nahrávky**): tlačítko **Připravit balíček…** po potvrzení **tří povinných souhlasů** (a čtvrtého volitelného, pokud chceš přidat i běžné diktáty) vytvoří čistě lokální ZIP v Dokumentech (např. `%USERPROFILE%\Documents\Mluvka-dar-2026-11-13.zip`). Obsahuje jen WAV soubory, anonymní `metadata.jsonl`, `souhlas.txt` (s náhodným `darce_id`) a `README.txt` — bez nastavení, API klíčů, historie, názvů aplikací a logů. **Nic se nikam neodesílá.**

## Přesnost a rychlost

Mluvka používá ve výchozím nastavení model **CzechSpeech q5_0** (`"model_soubor": "ggml-czechspeech-q5_0.bin"`, ten stahuje instalátor) a **beam search 5** (`"beam_size": 5` v `config.json`). Model jde přepnout v okně na stránce Trénink → Osobní model. `"beam_size": 1` je rychlejší greedy dekódování za cenu drobné přesnosti.

Měření z 13. 11. 2026 (RTX 4050, 500 vět Common Voice cs, které CzechSpeech při tréninku neviděl, a 40 vět FLEURS-cs):

| model / nastavení | Common Voice | FLEURS | rychlost |
|---|---:|---:|---:|
| CzechSpeech q5_0, greedy (`beam_size: 1`) | – | 6,82 % | cca 1,3 s / min |
| **CzechSpeech q5_0, beam 5 (výchozí)** | **7,24 %** | **6,00 %** | cca 3,0 s / min |
| CzechSpeech q8_0, beam 5 (lokální kvantizace, nestahuje se) | 7,09 % | 5,87 % | cca 3,4 s / min |
| doladěný na Common Voice (`ggml-cv1-q5_0.bin`), beam 5 | 7,00 % | 6,28 % | cca 3,0 s / min |
| NVIDIA Canary-1B-v2 (int8 ONNX, pro srovnání, v Mluvce není) | 12,70 % | 9,14 % | – |
| CzechSpeech f16, beam 5 | 7,24 % | 5,59 % | cca 4–5 s / min |
| CzechSpeech q8_0, beam 8 | 7,21 % | 5,87 % | cca 6 s / min |

Start Mluvky do „připravená“ trvá cca 3 s.

Delší diktát se přepisuje už během nahrávání, po úsecích asi 25 s. Spojení s Claude se zahřívá hned při puštění klávesy. Skutečný diktát 60 s (13. 11. 2026): text vložený **2,6 s** po puštění klávesy (přepis 0,8 s + úprava Haiku 1,6 s). Rozpad času je v `mluvka.log` (`prepis_s`, `ai_s`, `celkem_s`).

Canary-1B-v2 má volnější licenci (CC-BY-4.0), ale v češtině dělá skoro dvakrát víc chyb než CzechSpeech, proto ho Mluvka nepoužívá.

Doladění na 10 000 nahrávkách Common Voice (LoRA, 2,4 h na RTX 4050) přineslo zlepšení jen v rámci šumu a na FLEURS se mírně zhoršilo. Proto výchozím modelem není. Je v nabídce Osobní model, kdyby ti s tvým mikrofonem seděl víc. Skutečný přínos čekám od doladění na tvém hlase: nahraj věty v Tréninku a spusť doladění podle sekce níž.

Většina zbylých „chyb“ je jiný zápis čísel a jednotek („18“ místo „osmnáct“, „100 m“ místo „100 metrů“).
Měření si zopakuješ příkazem `%LOCALAPPDATA%\Mluvka\venv\Scripts\python.exe bench.py` v této složce
(výsledek je v `%LOCALAPPDATA%\Mluvka\bench\vysledek.md`).

Poznámka pro budoucí úpravy: zkrácený `audio_ctx` doporučovaný kartou modelu na whisper.cpp s CUDA přesnost rozbije
(WER 30 % i víc). Mluvka proto posílá vždy plný kontext 1500 a je tak rychlejší i přesnější.

## Doladění modelu (pro pokročilé)

Ve složce `trenink\` tohoto repozitáře jsou skripty pro vlastní LoRA doladění modelu CzechSpeech na datech Common Voice a tvých nahraných větách, převod výsledku do `.bin` (ggml) pro `whisper-server.exe` a srovnávací A/B měření (pracovní data i mezivýstupy leží v `%LOCALAPPDATA%\Mluvka\trenink\`):

| skript | co dělá | prostředí (venv) |
|---|---|---|
| `trenink\priprav_data.py` | připraví 16kHz WAVy a `manifest.tsv` z Common Voice cs (`%LOCALAPPDATA%\Mluvka\trenink\cv\*.parquet`) a tvých vět (`%LOCALAPPDATA%\Mluvka\trenink\vety.tsv`) | `venv-train` |
| `trenink\doladit.py` | LoRA doladění HF modelu v `%LOCALAPPDATA%\Mluvka\trenink\base\` (`bfloat16`), kombinuje `cv_train` a 5× převzorkovaný `vety_train` | `venv-train` |
| `trenink\do_ggml.py` | sloučí LoRA adaptér se základním modelem, převede do `ggml` a kvantizuje (`q5_0`, s `--q8` i `q8_0`) | `venv-train` |
| `trenink\ab_test.py` | změří WER `.bin` modelů proti sobě přes lokální `whisper-server.exe` a zapíše `vysledky.json` + markdown report | `venv` (aplikační) |

Co je dobré vědět před spuštěním:

- **Před tréninkem Mluvku zavři!** `doladit.py` kontroluje volnou VRAM a na 6GB RTX 4050 vyžaduje alespoň **3,5 GB** volné paměti grafiky (samotný `whisper-server.exe` zabírá cca 0,9 GB; jinak trénink rovnou skončí s chybou). Průběžné checkpointy se ukládají každých 500 kroků a při přerušení `Ctrl+C`.
- **Common Voice cs (licence CC0)**: `priprav_data.py` vyřazuje **jednoslovné klipy** a **klipy s číslovkami ≥ 11**, protože Common Voice píše čísla slovy (*osmnáct*, *dvacet pět*…), zatímco CzechSpeech a Mluvka mají psát číslice. Zůstávají klipy dlouhé 1–30 s.
- **První převod přes `do_ggml.py`**: pokud ještě nejsou v `%LOCALAPPDATA%\Mluvka\trenink\tools\`, skript jednou stáhne dva fixně připnuté převodní soubory (`convert-h5-to-ggml.py` z whisper.cpp v1.7.5 a `mel_filters.npz` z openai/whisper v20240930). Samotná aplikace Mluvka žádné modely ani soubory nestahuje.

### Příklady příkazů

```bat
:: 1. Příprava dat (omezení na 8 500 klipů = cca 2,1 h tréninku na RTX 4050; bez --max-train vše ~5 h)
%LOCALAPPDATA%\Mluvka\venv-train\Scripts\python.exe trenink\priprav_data.py --max-train 8500

:: Rychlý kouřový test celého postupu (24/12 klipů a 3 kroky):
:: %LOCALAPPDATA%\Mluvka\venv-train\Scripts\python.exe trenink\priprav_data.py --max-train 24 --max-test 12
:: %LOCALAPPDATA%\Mluvka\venv-train\Scripts\python.exe trenink\doladit.py --max-steps 3 --batch 1 --accum 1 --name smoke

:: 2. Doladění (Mluvka musí být zavřená!)
%LOCALAPPDATA%\Mluvka\venv-train\Scripts\python.exe trenink\doladit.py --name osobni

:: 3. Sloučení adaptéru a převod na kvantizovaný ggml .bin model
%LOCALAPPDATA%\Mluvka\venv-train\Scripts\python.exe trenink\do_ggml.py --adapter runs\osobni\adapter --name osobni

:: 4. A/B srovnání modelů (spouští se v aplikačním venv, bez PyTorche)
%LOCALAPPDATA%\Mluvka\venv\Scripts\python.exe trenink\ab_test.py --model %LOCALAPPDATA%\Mluvka\models\ggml-czechspeech-q5_0.bin --model %LOCALAPPDATA%\Mluvka\trenink\runs\osobni\ggml-osobni-q5_0.bin --sada cv_test --sada fleurs --beam 5
```

Hotový soubor `ggml-osobni-q5_0.bin` zkopíruj ze složky `%LOCALAPPDATA%\Mluvka\trenink\runs\osobni\` do `%LOCALAPPDATA%\Mluvka\models\` a přepni ho v okně pod **Trénink → Osobní model**.

## Nastavení v souboru

Všechno z okna je uložené v `%LOCALAPPDATA%\Mluvka\config.json` (v liště Mluvka → **Upravit nastavení…**).

| klíč | co dělá |
|---|---|
| `klavesa_vk` | klávesa pro diktát (kód VK). `163` = Pravý Ctrl, `161` = Pravý Shift, `20` = Caps Lock, `120` = F9 |
| `mikrofon` | část názvu mikrofonu, prázdné = výchozí ve Windows |
| `slovnik.slova` | slova a názvy, které má model čekat |
| `slovnik_do_promptu` | `true` (výchozí) = posílat `slovnik.slova` modelu jako nápovědu |
| `slovnik.opravy` | opravy `["co model napíše", "co chceš"]`, např. `["agens md", "AGENTS.md"]` |
| `zkratky` | zkratky `["co řekneš", "co se vloží"]`, např. `["můj podpis", "S pozdravem\nJan Novák"]` |
| `ai_uprava` | `true` (výchozí) = každý diktát ještě uhladí Claude Haiku 5.5 (výplňová slova, přeřeknutí, odrážky); přidá asi 1–2 s |
| `claude_api_klic` | API klíč pro Claude (nebo proměnná prostředí `ANTHROPIC_API_KEY`); prázdné = AI vypnutá, nic neodchází |
| `kanal_webhook` | HTTPS adresa kanálu, kam se má posílat každý diktát (viz níž) |
| `kanal_zapnuto` | `true` (výchozí) = posílat diktáty do nastaveného kanálu |
| `historie`, `historie_dny` | ukládat historii lokálně a kolik dní ji držet (výchozí `30`) |
| `uceni_z_oprav` | `true` (výchozí) = učit se automaticky z oprav vloženého diktátu v jiných aplikacích |
| `zvuky` | krátké tóny při začátku a konci nahrávání |
| `autostart` | spouštět Mluvku po přihlášení do Windows |
| `beam_size` | velikost beam search při přepisu (`5` výchozí = vyšší přesnost, `1` = rychlejší greedy) |
| `model_soubor` | název `.bin` modelu ze složky `models\` (výchozí `"ggml-czechspeech-q5_0.bin"`) |
| `trenink_ukladat_zvuk` | `true` = ukládat zvuk běžných diktátů do `trenink\diktaty\` (výchozí `false`) |
| `darce_id` | anonymní identifikátor dárce (UUID) vygenerovaný automaticky při prvním vytvoření balíčku v **Darovat nahrávky** |

## Kanál (posílání diktátů jinam)

Do `kanal_webhook` (nebo v okně pod **Nastavení → Kanál**) vlož HTTPS adresu webhooku. Mluvka pak každý hotový diktát pošle i tam. Vypnout to jde v okně nebo v liště: **Posílat do kanálu**.

- **Microsoft Teams**: v kanálu … → Workflows → „Post to a channel when a webhook request is received“ → zkopíruj URL.
- **Slack**: aplikace Incoming Webhooks → zkopíruj URL.
- **Discord**: nastavení kanálu → Integrace → Webhooky → Kopírovat URL.
- Jiná služba (n8n, Make, vlastní server): dostane JSON `{"text": "…"}`.

Text, který jde do kanálu, zpracuje daná služba (Microsoft, Slack, Discord…). Všechno ostatní zůstává na PC.

## Soukromí

- Přepis běží v `whisper-server.exe` na adrese 127.0.0.1, tedy jen uvnitř tvého PC. Model se načítá ze souboru, nic se nestahuje.
- Aplikace má v sobě síťovou pojistku: povolí jen spojení na 127.0.0.1, na `api.anthropic.com` (jen když je nastavený klíč) a na adresu kanálu. Všechno ostatní zablokuje a zapíše do logu.
- Text se vkládá přes schránku, ale s příznakem, aby se neukládal do historie schránky (Win+V) ani nesynchronizoval
  do cloudu. Po vložení se vrátí, co jsi ve schránce měl předtím.
- Kontrola binárek nástrojem REA (12. 11. 2026): `whisper-server.exe` umí síť jen pasivně (naslouchá na socketu, nemá
  `connect`, WinHTTP ani WinINet), knihovny `whisper.dll`, `ggml*.dll` a `ggml-cuda.dll` síť neimportují vůbec
  a v binárkách nejsou zabudované žádné URL.
- Ověření, že nic neodchází: během diktování spusť v PowerShellu

  ```powershell
  Get-NetTCPConnection | Where-Object { (Get-Process -Id $_.OwningProcess).ProcessName -match 'python|whisper' } | Select-Object LocalAddress,LocalPort,RemoteAddress,State,OwningProcess
  ```

  Smí tam být jen `127.0.0.1` a během AI úpravy spojení na `api.anthropic.com`.

### Složka s daty (`%LOCALAPPDATA%\Mluvka`)

Všechna data leží mimo OneDrive v `%LOCALAPPDATA%\Mluvka`:

```text
%LOCALAPPDATA%\Mluvka\
├── config.json              # všechna nastavení, slovník, zkratky, API klíč, model a beam_size
├── historie.jsonl           # lokální historie diktátů (při otevření z lišty vzniká historie.md)
├── pamet_chyb.jsonl         # náhrady slov z ručních úprav v Historii
├── mluvka.log               # technický log (jen časy a délky, žádný text diktátů)
├── whisper-server.log       # log lokálního whisper-serveru
├── models\                  # ggml modely (výchozí ggml-czechspeech-q5_0.bin + osobní *.bin)
├── trenink\                 # nahrávky pro trénink, měření a případné doladění modelu
│   ├── vety\                # nahrané tréninkové věty (NNN.wav)
│   ├── vety.tsv             # seznam nahraných vět (číslo, text, délka)
│   ├── diktaty\             # zvuk běžných diktátů (jen když zapneš „Ukládat zvuk diktátů“)
│   ├── mereni.json          # poslední lokální měření WER na vlastních větách
│   ├── vysledky.json        # srovnávací výsledky A/B testů (+ markdown reporty ab_*.md)
│   └── base\, cv\, data\, runs\, tools\  # podklady a běhy pro skripty ve složce trenink\
├── bin\whisper\             # whisper-server.exe, whisper-quantize.exe a CUDA knihovny
├── venv\                    # Python prostředí Mluvky (používá se i pro trenink\ab_test.py a bench.py)
├── venv-train\              # Python prostředí pro doladění (priprav_data, doladit, do_ggml)
├── zvuky\                   # start.wav a stop.wav
└── bench\                   # podklady a vysledek.md pro bench.py
```

## AI úpravy a příkazy (Claude Haiku 5.5)

- Přepis řeči je vždy lokální. Claude dostane jen **text** diktátu (plus slovník a posledních až 5 příkladů z **Paměti chyb**, pokud existují), nebo pokyn + označený text (příkaz), **nikdy zvuk**.
- Klíč: [console.anthropic.com](https://console.anthropic.com) → API Keys. Vlož ho v okně Mluvky v sekci **AI úpravy** → Uložit → Ověřit.
  Ukládá se do `%LOCALAPPDATA%\Mluvka\config.json` (mimo OneDrive). Jde to i přes proměnnou prostředí `ANTHROPIC_API_KEY`.
- Cena: 0,10 $ / 0,50 $ za milion vstupních / výstupních tokenů, jeden běžný diktát vyjde zhruba na 0,0003 $.
- Bez klíče Mluvka funguje dál, jen bez AI úprav a příkazů. Když Claude neodpoví (offline), vloží se lokální přepis.

## Model a licence

- **CzechSpeech** od Moravian AI ([huggingface.co/moravianai/CzechSpeech](https://huggingface.co/moravianai/CzechSpeech)), Whisper large-v3-turbo doladěný na 1 500 h češtiny.
  Licence **CC BY-NC 4.0**: zdarma pro nekomerční použití. Na komerční použití je potřeba licence od Moravian AI (info@moravian.ai).
- Náhrada bez omezení: model `ggml-large-v3-turbo-q5_0.bin` (MIT) stačí uložit do `%LOCALAPPDATA%\Mluvka\models\` a vybrat v okně pod **Trénink → Osobní model** (česky je ale méně přesný — při úvodním testu 11,4 % chyb proti 7,4 %).

## Když něco nejde

- Text se nevloží do okna spuštěného jako správce: Windows to zakazují. Pak spusť Mluvku taky jako správce,
  nebo použij v liště **Vložit poslední přepis** (dá přepis do schránky) a vlož ho Ctrl+V.
- Mikrofon nic nezachytil: v okně pod **Nastavení** (nebo v liště pod **Mikrofon**) zkontroluj vstup a ve Windows oprávnění k mikrofonu pro desktopové aplikace.
- Log: `%LOCALAPPDATA%\Mluvka\mluvka.log`.

## Odinstalace

V okně pod **Nastavení** (nebo v liště) vypni „Spouštět po přihlášení“ a ukonči Mluvku. Pak smaž zástupce a celou složku s daty:

```powershell
& "$env:LOCALAPPDATA\Mluvka\venv\Scripts\python.exe" -m mluvka.zastupci --odebrat
```

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Mluvka"
```

Ve složce s daty je i stažený kód (`app`), whisper.cpp (`bin`), model (`models`), historie a nastavení. Po smazání nezbude nic.
