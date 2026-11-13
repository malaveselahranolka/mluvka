# Prompty pro média webu Mluvky

Barvy značky: švestka `#33223A`, meruňka `#E8894A` / `#FFB37A`, ojíněná švestka (světlý podklad) `#F7F5F8`.
Ve videu nemá být žádný čitelný text (modely ho kazí). Text dodá web.

## Veo: launch video (3 klipy po 8 s, 16:9, 1080p)

Klipy se na webu spojí do jednoho videa (ffmpeg). Zvuk: bez mluveného slova a bez hudby, jen jemný ambient, aby video šlo pustit i bez ztlumení.

### Klip 1: klávesa
```
Extreme macro shot of a person's finger pressing and holding down the right Ctrl key on a dark matte laptop keyboard. Soft warm apricot-orange light (#FFB37A) glows from under the pressed key, the rest of the keyboard in deep plum-purple shadows (#33223A). Shallow depth of field, slow push-in, 35mm anamorphic look, subtle film grain. After two seconds the finger lifts and the glow fades softly. No text, no logos, no letters readable on the keys. Ambient sound: quiet room tone, a soft mechanical key click on press and release.
```

### Klip 2: člověk diktuje
```
Medium shot of a man in his thirties sitting at a wooden desk in a bright Central European apartment in the morning, speaking calmly and naturally toward his laptop as if dictating an email, one hand resting near the keyboard. A small bowl of ripe purple plums and apricots sits on the desk beside a cup of coffee. Window light from the left, soft warm tones, muted plum and apricot color palette, the laptop screen faces away from the camera so no screen content is visible. Gentle handheld camera, slow drift to the right, 35mm film look. No text anywhere. Ambient sound: quiet apartment, distant street, no music, his voice is a soft unintelligible murmur.
```

### Klip 3: znak
```
A single ripe purple plum with a dusty bloom lies on a smooth apricot-colored surface, top-down view. The plum slowly rotates and morphs in one continuous fluid motion into a flat round deep-plum disc with five vertical rounded apricot-colored bars in its center, like an audio level meter, and the bars gently pulse as if listening to speech. Clean studio light, soft shadow, minimal composition, centered, lots of negative space. No text, no letters. Ambient sound: a soft low whoosh during the morph, then silence.
```

## Stav (13. 11. 2026)

- Všechny tři klipy hotové, spojené do `mluvka-video.mp4` (18 s, 1,4 MB): klávesa → člověk diktuje → švestka se mění ve znak. Klip 3 je zároveň smyčka `svestka.mp4`.
- Klip 1 a 3 vznikly ve Veo přes Gemini, klip 2 přes Google Flow (flow.google.com) poté, co v Gemini došel denní limit. Flow má vlastní kvótu a účtuje kredity (30 za klip).
- Dlouhé prompty s hex kódy ve Veo selhávají („Sorry, something went wrong"). Zkrácené bez značek a hex kódů projdou.
- AI obrázky web nepoužívá (rozhodnutí uživatele): aplikaci ukazují skutečné screenshoty v rámečcích zařízení.
