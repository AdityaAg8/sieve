# Font licences

All three families are self-hosted as static `.woff2` files under `web/public/fonts/`. Full licence
text for each family is committed alongside the font files; this document names the licence, the
download source, and the date each was fetched.

Clash Display and Geist were replaced on 2026-09-16 (the redesign) and
their files and licence texts removed from the tree. The families below are the ones in use.

## Plus Jakarta Sans

- **Files:** `PlusJakartaSans-Variable.woff2` (one variable file, `wght` 600–800, latin subset)
- **Publisher:** Tokotype (The Plus Jakarta Sans Project Authors)
- **Licence:** SIL Open Font License, Version 1.1 (OFL 1.1)
- **Downloaded from:** Google Fonts, `https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@600;700;800`
  (the `fonts.gstatic.com` latin-subset woff2 that CSS names)
- **Date fetched:** 2026-09-16
- **Full licence text:** [`OFL-PlusJakartaSans.txt`](./OFL-PlusJakartaSans.txt), fetched the same day from
  `https://github.com/tokotype/PlusJakartaSans/blob/master/OFL.txt`
- **Copyright header (verbatim):**
  ```
  Copyright 2020 The Plus Jakarta Sans Project Authors (https://github.com/tokotype/PlusJakartaSans)
  ```

## IBM Plex Sans

- **Files:** `IBMPlexSans-Variable.woff2` (one variable file, `wght` 400–600, latin subset)
- **Publisher:** IBM Corp.
- **Licence:** SIL Open Font License, Version 1.1 (OFL 1.1), Reserved Font Name "Plex"
- **Downloaded from:** Google Fonts, `https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600`
  (the `fonts.gstatic.com` latin-subset woff2 that CSS names)
- **Date fetched:** 2026-09-16
- **Full licence text:** [`OFL-IBMPlexSans.txt`](./OFL-IBMPlexSans.txt), fetched the same day from
  `https://github.com/IBM/plex/blob/master/LICENSE.txt`
- **Copyright header (verbatim):**
  ```
  Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"
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

Only what `layout.tsx` references is committed: one latin-subset variable file for each sans family
(the weight range it declares), and two static JetBrains Mono weights. Re-run the download commands
above to get other subsets or weights.
