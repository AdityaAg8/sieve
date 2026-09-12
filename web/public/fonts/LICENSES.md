# Font licences

All three families are self-hosted as static `.woff2` files under `web/public/fonts/`. Full licence
text for each family is committed alongside the font files; this document names the licence, the
download source, and the date each was fetched.

## Clash Display

- **Files:** `ClashDisplay-Regular.woff2`, `ClashDisplay-Medium.woff2`, `ClashDisplay-Semibold.woff2`
- **Publisher:** Indian Type Foundry, distributed via Fontshare
- **Licence:** ITF Free Font License (FFL), Version 2.0 — free for commercial and personal use,
  self-hosting explicitly permitted (see FFL §01)
- **Downloaded from:** `https://www.fontshare.com/fonts/clash-display` (package fetched via the
  Fontshare download API, `https://api.fontshare.com/v2/fonts/download/clash-display`)
- **Date fetched:** 2026-09-12
- **Full licence text:** [`FFL-ClashDisplay.txt`](./FFL-ClashDisplay.txt)

## Geist

- **Files:** `Geist-Regular.woff2`, `Geist-Medium.woff2`, `Geist-SemiBold.woff2`
- **Publisher:** Vercel (The Geist Project Authors)
- **Licence:** SIL Open Font License, Version 1.1 (OFL 1.1)
- **Downloaded from:** `https://github.com/vercel/geist-font/releases` (release `v1.7.2`,
  asset `geist-font-v1.7.2.zip`)
- **Date fetched:** 2026-09-12
- **Full licence text:** [`OFL-Geist.txt`](./OFL-Geist.txt)
- **Copyright header (verbatim):**
  ```
  Copyright 2024 The Geist Project Authors (https://github.com/vercel/geist-font)

  This Font Software is licensed under the SIL Open Font License, Version 1.1.
  This license is copied below, and is also available with a FAQ at:
  https://openfontlicense.org
  ```

## JetBrains Mono

- **Files:** `JetBrainsMono-Regular.woff2`, `JetBrainsMono-Medium.woff2`
- **Publisher:** JetBrains s.r.o.
- **Licence:** SIL Open Font License, Version 1.1 (OFL 1.1)
- **Downloaded from:** `https://github.com/JetBrains/JetBrainsMono/releases` (release
  `v2.304`, asset `JetBrainsMono-2.304.zip`)
- **Date fetched:** 2026-09-12
- **Full licence text:** [`OFL-JetBrainsMono.txt`](./OFL-JetBrainsMono.txt)
- **Copyright header (verbatim):**
  ```
  Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)

  This Font Software is licensed under the SIL Open Font License, Version 1.1.
  This license is copied below, and is also available with a FAQ at:
  https://scripts.sil.org/OFL
  ```

## Weights shipped

Only the weights `layout.tsx` actually references are committed (Regular/Medium/SemiBold-ish per
family) to keep the repo lean; the full family download (all weights, italics, variable fonts) is
not committed. Re-run the download commands above against the same release tags to get the rest.
