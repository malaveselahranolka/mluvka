# Plán: nejpřesnější český model pro přepis řeči při stejné rychlosti

Stav k 13. 11. 2026. Fakta pocházejí z karty CzechSpeech, z katalogů datasetů a z našich měření. U čeho jsem si nebyl jistý, ověřoval jsem to u primárního zdroje.

## Výchozí bod

| | Parlament | FLEURS | Šum | Common Voice | Spontánní / telefon |
|---|---:|---:|---:|---:|---|
| CzechSpeech (karta, greedy) | 1,8 % | 7,3 % | 6,5 % | – | **neměřeno** |
| CzechSpeech q8_0 + beam 5 (naše měření) | – | 6,97 % (400 vět) | – | 7,09 % | – |
| whisper-large-v3-turbo, výchozí bod obou (karta) | 9,5 % | 11,4 % | 29,5 % | – | – |

- CzechSpeech viděl zhruba 1 415 h unikátního zvuku, z toho asi 89 % je parlament (ParCzech4Speech 1 336 h, YODAS 65 h, FLEURS 8 h, VoxPopuli 6 h). Spontánní řeč, nářečí ani telefon nikdo neměřil. **Právě tam je prostor ke zlepšení.**
- Jeho licence CC BY-NC 4.0 nedovoluje komerční použití. Pro model „pro všechny“ ho proto nejde použít jako základ ani jako učitele.
- **Rychlost se nezmění, když zůstane stejná architektura** (large-v3-turbo, 0,81 mld. parametrů). Zlepšení musí přijít z dat a tréninku, ne z většího modelu.

## Rozhodnutí na začátku

1. **Zlepšit stávající CzechSpeech (zvoleno 13. 11. 2026):** začínáme z nejsilnějšího bodu (parlament 1,8 %, FLEURS 7,3 %), takže je to rychlejší a levnější. Licence CC BY-NC 4.0 dovoluje upravený model sdílet a upravovat pro **nekomerční** účely s uvedením autora. Můžeme ho tedy dát zdarma všem pro osobní použití. Komerční použití potřebuje licenci od Moravian AI (info@moravian.ai). Stojí za to je oslovit: spolupráce na datech ze spontánní řeči by pomohla oběma stranám.
2. **Vlastní otevřený model:** trénovat od whisper-large-v3-turbo (MIT), pokud by bylo potřeba komerční použití bez licence od Moravian AI.

Obě cesty mají stejné kroky, liší se jen výchozím modelem.

Rozdíly při zlepšování CzechSpeech:
- **Pozor na zapomínání:** do tréninku vždy přimíchat ParCzech4Speech (asi 30–50 %, je CC BY a CzechSpeech ho zná), aby model neztratil parlament a čísla.
- **Nižší tempo učení:** 5e-6 až 1e-5 a 1–2 průchody daty. Model je už doladěný, potřebuje jen doučit nové domény.
- **CzechSpeech může být jedním z učitelů pro pseudo-štítky**, protože výsledek je stejně nekomerční.
- **Data s licencí NC** (PDTSC, OVM) by šla použít i k tréninku, ale raději je nechat jako čistý test, jinak nepoznáme skutečné zlepšení.

## Fáze 0: Měřítko (1–2 dny, lokálně, zdarma)

Bez férového testu nepoznáme, jestli je nový model lepší. Postavíme **českou testovací sadu** s jednou normalizací (malá písmena, bez interpunkce, čísla číslicemi). Každý model se změří s greedy i s beam 5.

| Část testu | Zdroj | K čemu slouží |
|---|---|---|
| Čtená řeč | FLEURS test (celých 723 vět), Common Voice test | srovnání s kartou |
| Parlament | ParCzech4Speech test (21 h, vzorek) | hlídá, že se neztratí silná stránka |
| Telefon | Vystadial (test) | slabina |
| Rozhovory a diskuse | OVM (Otázky Václava Moravce, 35 h), PDTSC (spontánní dialogy, 122 h) | slabina. Jen k testování, licence NC trénink pro otevřený model nedovoluje |
| Šum | parlament a FLEURS se šumem a ozvěnou | robustnost |
| Tvůj hlas | ověřené diktáty z Mluvky | to, co používáš |

**Cíl:** v průměru lepší než CzechSpeech, výrazně lepší na rozhovorech a telefonu a na parlamentu a FLEURS nejvýš o 0,2 bodu horší.

## Fáze 1: Data (1–2 týdny práce, hlavně stahování a čištění)

Data, která dovolují otevřené vydání:

| Data | Hodiny | Typ | Licence |
|---|---:|---|---|
| ParCzech4Speech | asi 2 630 | parlament | CC BY |
| Large Corpus of Czech Parliament Plenary Hearings | 444 | parlament (odstranit duplicity s ParCzech) | CC BY 4.0 |
| VoxPopuli cs | 62 | Evropský parlament | CC0 |
| Common Voice cs | asi 81 ověřených | čtené, tisíc lidí | CC0 |
| FLEURS cs train | 8 | čtené | CC BY |
| YODAS / Granary cs | desítky | YouTube (přepisy z Whisperu) | CC BY |
| Vystadial | asi 77 | **telefon**, dialogy | CC BY-SA. Jestli „SA“ zavazuje i model, je potřeba ověřit, jinak data vyřadit |

Chybí spontánní řeč. Získáme ji třemi cestami:

1. **Pseudo-štítky:** nasbírat hodiny české spontánní řeči s licencí CC (YouTube CC BY, otevřené podcasty). Přepsat je dvěma učiteli (whisper-large-v3 a model z kola 1) a ponechat jen úseky, kde se oba shodují (rozdíl do 10 %). Tak vznikl Distil-Whisper a filtr mu snížil chyby z 13,4 % na 11,4 %.
2. **Darování z Mluvky:** spontánní řeč se souhlasem lidí. Lokální balíček už umíme, chybí jen kam ho posílat (například neveřejný dataset na Hugging Face).
3. **Placená data, když bude rozpočet:** SpeechData.ai má 1 000 h českých rozhovorů s komerční licencí. Stojí asi 90 USD za hodinu, takže i 100 h je asi 9 000 USD.

Navíc:
- **Augmentace:** šum (MUSAN), ozvěna (RIR), převod na 8 kHz a telefonní kodeky (G.711, AMR), u 30–50 % úseků.
- **Jednotný zápis:** čísla číslicemi, což je styl diktování, který chceš.

## Fáze 2: Trénink v cloudu (asi 1 týden včetně pokusů)

- **Plný fine-tune** turba, ne LoRA. LoRA s malými daty se už ukázala jako slepá cesta. Učení 1e-5 až 2e-5 s rozehřátím, bf16, 2–3 průchody daty.
- **Poměr dat:** parlament nejvýš 50 %, spontánní řeč a pseudo-štítky asi 30 %, čtená řeč 10 %, telefon 10 %. Šum přimíchat augmentací.
- **Dvě kola:** v kole 1 natrénovat na lidských přepisech. Kolo 1 se pak stane druhým učitelem pro pseudo-štítky a kolo 2 se učí na všem.
- **Hardware:** RTX 4050 nestačí, plný fine-tune potřebuje přes 13 GB jen na optimalizátor. V cloudu na pronajaté A100 nebo H100:

| Rozsah | Hodiny GPU (A100) | Cena (RunPod A100) |
|---|---:|---:|
| 1 000 h dat × 3 průchody | 12–30 | asi 20–70 USD |
| 3 000 h × 3 | 36–90 | asi 50–200 USD |
| pseudo-štítky pro 10 000 h | 50–100 | asi 70–220 USD |
| **Reálně celkem, včetně pokusů a chyb** | | **asi 300–1 000 USD** |

  - Zdarma to jde přes EuroHPC (LUMI, na kterém vznikl CzechSpeech). Žádost ale trvá týdny.
- Každý mezistav se změří na testu z fáze 0 a vezme se nejlepší.

## Fáze 3: Osobní vrstva (lokálně, RTX 4050)

- Na nejlepší základní model přidat LoRA naučenou na tvých ověřených diktátech (cíl 30–60 min a víc).
- Nasadit jen tehdy, když je model lepší na tvých testovacích diktátech a zároveň se nezhorší obecně. Pipeline na to už máme (`trenink/`).

## Fáze 4: Vydání a nasazení

- Převod do whisper.cpp ggml q8_0. Pipeline máme a je ověřená, převod sedí s oficiálními soubory.
- Rychlost bude stejná jako teď: stejná architektura, q8_0 + beam 5, minuta diktátu asi 2,6 s i s Haiku.
- Vydání na Hugging Face s kartou modelu, testovací sadou, receptem a licencí (Apache-2.0 nebo CC BY, uvést zdroje dat).
- V Mluvce bude jako výchozí model. CzechSpeech zůstane volitelný.

## Rizika

- **Licence:** dopad „SA“ (Vystadial) a licence jednotlivých videí na YouTube. Ověřit, nebo data vynechat.
- **Kvalita pseudo-štítků:** filtr shody upřednostní snadné úseky. Část rozporných úseků proto zkontrolovat ručně a měřit na lidsky přepsaných testech.
- **Málo spontánních dat:** volně dostupná prakticky nejsou. Největší páka je darování a placená data.
- **Bez měřítka z fáze 0 nic nenasazujeme.** Platí pravidlo z našich pokusů: nasadit jen model, který změřeně vyhraje.

## Co udělám hned, když řekneš „jdeme do toho“

1. Fáze 0: testovací sada a změření CzechSpeech, turba a Canary na všech jejích částech, včetně spontánní řeči a telefonu.
2. Spočítám skutečnou velikost dat a ověřím licence Vystadialu a YODAS.
3. Předložím přesný rozpočet a jednu zkušební hodinu tréninku v cloudu na změření rychlosti.
