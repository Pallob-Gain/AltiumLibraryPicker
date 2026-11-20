# Progress

## Environment (verified 2026-11-18)
- Windows 11 Pro; Altium Designer **26.10.1.5** at `C:\Program Files\Altium\AD26`.
- Python **3.12.10** installed per-user (Tk 8.6, SQLite 3.49.1).
- The Mouser API key is stored with DPAPI encryption. The live search was verified.

## Scope change (2026-11-18, user request)
The user chose a **manual download + automatic import** workflow. Automatic downloading (M2) is postponed. The Altium import and linking work (M3 + M4) was pulled forward and is done.

---

## 2026-11-20 — v1.1.0: "Get from CSE (direct)" ✅ verified live

- **Get from CSE (direct)** (step 3, first button): MPN → CSE search (`ad.componentsearchengine.com/ga/search.php?kws=<MPN>&v=1`, Basic auth, the method from the user's own `downloader.py`) → **exact** MPN matches that have a model → download (`model.php`) with progress → automatic import with backup, duplicate handling, verification and 3D attach.
  - Several manufacturers: the app uses the Manufacturer field if it identifies one part, otherwise shows a chooser (manufacturer, description, package, 3D, CSE "confidence").
  - No match: Messages lists similar but NOT identical parts and suggests the other sources.
- The login comes only from the app's encrypted store (Settings → Component Search Engine). **No credentials in code.** The user's separate `downloader.py` (outside this repo) still contains them; it must not be published.
- **Celestial Altium Library:** researched. It has about 257k parts with 3D models, and its custom licence allows commercial use but not reselling the models. Access is only through Altium's database library: a per-user portal login, a cloud SQL server, and Microsoft SQL drivers. There is no MPN web lookup, so it can't be fetched automatically. Added a "Celestial Library" button (setup guide) and a README section as the fallback.
- **Verified live with the user's account** (login read from the encrypted store):
  - Search: 50YXJ22MTA5X11 → 1 exact; STM32F103C8T6 → 2 exact (STMicroelectronics and a "Microchip" listing); MCP23017T-E/ML → 1 exact.
  - Direct download of 50YXJ22MTA5X11: 1.6 MB, with Altium SchLib/PcbLib and STEP.
  - **Full GUI run:** STM32F103C8T6 with Manufacturer=STMicroelectronics was picked automatically, downloaded, and imported into a temporary library. The symbol is linked to `QFP50P900X900X160-48N` and the footprint has its 3D body (checked from disk).
- 69 offline tests pass.

---

## 2026-11-19 — v1.0.0: one-file Windows app ✅

- **`AltiumLibraryManager.exe`** (about 12 MB, PyInstaller one-file, windowed) includes Python, Tk and the Altium scripts. No installation is needed. It is published on the GitHub Releases page. Rebuild with `python tools/build_exe.py`.
- **App icon** (`alm/ui/app.ico`, drawn by `tools/make_icon.py`) is used for the .exe and the window.
- **Mouser API key:** verified it is not in any project file or anywhere in the Git history. Every user enters their own key under Settings → API keys (DPAPI-encrypted).
- **UI made beginner/laptop friendly:**
  - The window sizes itself to the screen.
  - Step 3 is split into "Downloaded library" and "Get it automatically" rows.
  - The site buttons are in two columns.
  - Messages and the status bar always stay visible.
- **README** rewritten for beginners: download → double-click → SmartScreen note → setup table → four ways to get a part → what the import does → troubleshooting.
- **Verified:**
  - Inside the packed .exe, the app finds both Altium scripts and the icon, and locates Altium.
  - The window opens in about 3.5 s.
  - 66 tests pass.

---

## 2026-11-19 (latest) — Mouser ECAD download + automatic 3D model ✅ verified with your real download

### 1. What was implemented
- **Why:** the CSE download button sent you to a login page again, but Mouser's ECAD Model icon worked. Your real Mouser download (`LIB_50YXJ22MTA5X11.zip`) is the **full SamacSys package**: `Altium/*.SchLib`, `Altium/*.PcbLib`, `3D/*.stp`, plus the other ECAD formats. No API and no CSE request from the app are needed.
- **Step 3 → "From Mouser (ECAD model)…"** opens the Mouser search for the MPN, watches Downloads, and imports automatically. It shares the download watcher with the CSE button; only one can wait at a time.
- **The 3D model is attached automatically.** SamacSys PcbLibs have no embedded 3D body, so when a package contains exactly one STEP, the import adds it as a 3D body to each newly copied footprint that has none. It is placed at the footprint origin, unrotated. This reuses EasyEDA-Importer's verified 3D-body code, and Messages shows "Added 3D model …".

### 2. Verified
- Your real Mouser zip imported into a temporary library:
  - symbol 2/2 pins, footprint 5/5 primitives, pin check passed, footprint found by Altium's library search;
  - the footprint has **1 3D body** after reopening the saved library from disk.
- The full GUI flow with the Mouser button on the same file: opened Mouser, showed progress, picked the file up, imported automatically, and the 3D body is present.
- 66 offline tests pass.

### 3. Please check
In Altium, open the footprint's 3D view, press 3, and check the STEP sits correctly on the pads. SamacSys models are normally made for the footprint origin.

---

## 2026-11-19 (later) — CSE login + automatic model download ✅ login verified live; full download pending one real part

### 1. What was implemented
- **Settings → Component Search Engine…**:
  - Email and password fields.
  - **Remember login**, encrypted with DPAPI and restored at startup; when unticked, the login is kept for this session only.
  - **Sign in & test**, which asks CSE whether the login is valid.
  - **Log out**, which deletes the saved login.
  - The login status is shown next to the CSE button in step 3: "not signed in", "signed in" or "login rejected".
- `alm/cse_client.py` is original code. It reads `.epw` part files (plain, or inside a zip) and downloads the model zip from CSE with your login, showing progress.
  - It refuses redirects, so the login can't be forwarded elsewhere.
  - It caps the download size and checks the result is a real zip.
  - It shows CSE's own error text.
  - It reports which folders the zip holds if there is no Altium library in it.
- **From Component Search Engine…** now also handles `.epw`: CSE download button → app downloads the model → automatic import. It still imports full library zips directly.

### 2. Verified
- Against the live server:
  - No login gives 401 (`Basic realm="Samacsys Parts Database"`).
  - **Your real login was accepted.**
  - An unknown part ID gives "Error: no part data available for partID 1", which is now shown as a clear message.
- 64 offline tests pass, 11 of them new for the client.
- The password never appears in the code, logs, settings or the repo; the live tests passed it through environment variables only.

### 3. Still to test (needs one click on CSE)
A real model download needs a real part ID. That comes from the `.epw` that CSE's download button saves. CSE pages are behind Cloudflare and are not scraped.
1. In the app, enter an MPN and select destination copies.
2. Settings → Component Search Engine → **Sign in & test**.
3. Click **From Component Search Engine…**, then click the part's download button on CSE.

Whether CSE's model zip contains Altium files (SchLib/PcbLib/IntLib) depends on your CSE account's ECAD setting. If it has no Altium library, the app says so and lists the folders it found.

---

## 2026-11-19 — Component Search Engine integration (browser workflow) ✅ (pending your test with a real CSE account)

### 1. What was implemented
- **Step 3 → "From Component Search Engine…"**:
  1. It opens CSE's search for the MPN in your browser.
  2. It watches your Downloads folder, with live status: *waiting* → *Downloading LIB_x.zip: 1.2 MB* → *Download complete*. It handles Chrome/Edge `.crdownload` and Firefox `.part` files.
  3. It copies the file into the app's data folder, extracts it (zip, or an IntLib through Altium), picks the component, and **imports automatically** into the selected destination with the existing backup, duplicate handling and verification.
  4. Clicking the button again stops waiting. A timeout after 15 minutes gives a clear message.
- **Settings → Component Search Engine…**: explains the sign-in, opens CSE to sign in or out, chooses the watched folder, and turns automatic import on or off.
- `alm/cse.py`: original code. `tests/test_cse.py` has 8 tests.

### 2. Verification: is direct CSE login/download allowed? (requirement 9)
**No, not verified as permitted, so it is not implemented.**
- No public CSE API exists. The only official client is SamacSys' Library Loader.
- The Supplyframe Terms of Service §4.2(e) prohibit "software or automated agents or scripts" that "generate automated searches, requests and queries, or … strip or mine data".
- olback/library-loader is AGPL-3.0 and describes itself as unofficial.

Therefore the in-app email/password login, "Remember Login" and logout (requirements 1, 2 and part of 7) are **not implemented**. Sign-in, "remember me" and sign-out happen in your browser on componentsearchengine.com. The app never asks for, stores or sends CSE credentials. MPN search and automatic import (requirements 3 and 5) work through the browser. Download (requirement 4) is your click on CSE's own Download button.

### 3. How to run / what to test
1. Select the destination libraries (use copies first) and enter the MPN.
2. Click **From Component Search Engine…**, sign in on CSE in the browser, click Download, and choose Altium Designer.
3. The app imports the part by itself. Check Messages, then check the part in Altium.

Please also tell me what the CSE zip contains for Altium (IntLib, or SchLib/PcbLib), because both are supported but only a simulated download was tested.

### 4. What works (verified)
- A simulated Chrome-style download (`LIB_50YXJ22MTA5X11.zip.crdownload` growing, then renamed) was detected with progress, claimed, and auto-imported through the running AD26 into a temporary library.
  - Symbol and footprint were added and linked.
  - Existing parts were untouched.
  - Backups were made.
- 53 offline unit tests pass.

### 5. Limitations
- Not tested against a real CSE download (no CSE account here). If CSE's Altium zip layout differs, the import reports what it found.
- If the official Library Loader is running, it may move the zip out of Downloads first. The app copies the file the moment it is complete; if the Library Loader wins, use **From SamacSys library…**.

---

## 2026-11-19 — Fix "footprint not found", SamacSys source, EasyEDA by LCSC number ✅ (pending your test)

### 1. What was implemented
- **Bug fix: "no footprint" after import until Altium restart.** The library files were always correct. AD26's library search, which placement, model preview and the PCB-model browse dialog all use, was not refreshed.
  - Verified cause: Altium refreshes it only after the script run that saved the library has ended, from the still-loaded document, and not if the library was open in an editor while saved.
  - The import now makes separate Altium runs:
    - **prepare** closes destinations open in an editor; it refuses if they have unsaved changes.
    - **import** saves and leaves the libraries loaded.
    - **finish** checks Altium's library search, closes, and reopens what you had open.
  - Messages now say *"Altium library search finds footprint X (ready to place)"*.
- **Component to import:** a new read-only SchLib lister (`alm/schlib_reader.py`) fills a dropdown. It pre-selects the symbol matching the MPN or LCSC number, and only that symbol (and its footprints) is imported.
- **From SamacSys library…** uses the library the official SamacSys Library Loader fills. No CSE credentials are handled by this app.
- **LCSC # → Fetch from EasyEDA (unofficial)** uses your EasyEDA-Importer code, vendored with your permission.
  - Converter: `alm/easyeda/convert.py`, with LCSC/model-id input hardening.
  - Builder routines: `alm/altium/ee_build.pas`, with the dialog removed and a non-interactive entry added.
  - The part is built in Altium into new staging libraries, then imported with the normal verified import.

### 2. How to run
Double-click `Altium Library Manager.bat`, then follow step 3 in `README.md`.

### 3. What you should test
1. On **copies** of your libraries, with the project open in Altium, import a part.
   - Without restarting Altium, place it on a schematic: the footprint must be found.
   - In the SchLib editor, the footprint preview must show.
   - In the PCB Model dialog, the footprint must be listed.
2. Repeat with the destination libraries open in Altium.
3. LCSC: enter `C2040` (or a part you use), click Fetch, then Import. Check the 3D model and pinout.
4. SamacSys: download a part on Component Search Engine with the official Library Loader running, then click **From SamacSys library…**.

### 4. What works (verified on AD26 with temporary libraries in a temporary project)
- Reproduced your bug, and confirmed that closing or reopening the libraries, compiling the project, and installing the library do **not** fix it.
- With the new sequence, the footprint is found immediately when the libraries are open, open again, and closed. Libraries you had open are reopened; closed ones stay closed.
- A failed import (wrong component name) saves nothing and leaves nothing loaded.
- EasyEDA C2040 (RP2040):
  - The download gave 57 pins and 57 pads, with the STEP model.
  - The build produced 57 pads and 87 objects.
  - The import gave pins 57/57 and primitives 87/87, and the pin check passed.
  - `LCSC Part #`=C2040, and the library search finds the footprint.
- The vendored converter reproduces all 6 of EasyEDA-Importer's test fixtures.
- 45 offline unit tests pass.

### 5. What remains incomplete
- The SamacSys path is **not tested** with a real Library Loader install (none on this PC). It uses the same, tested import.
- The EasyEDA builder uses EasyEDA-Importer's default parameter map. The app's own parameter fields are applied on top.

### 6. Known risks and limitations
- The EasyEDA API is undocumented and may change or break. Its data is community-made, so always check the footprint and pinout against the datasheet.
- An import now takes a few seconds longer, because it makes 3 or 4 short Altium runs.
- The library-search check only covers libraries in an open project or installed libraries. For others, it reports "not found" with an explanation.

---

## M1 — Search ✅
Mouser API search with exact-match ranking, plus buttons for 8 library sites (each site and "Open ALL") and the Altium Manufacturer Part Search helper. Verified live: STM32F103C8T6, MCP23017T-E/ML and STDRIVE141TR came back as exact matches with the correct manufacturer. `XYZ123QQQ` returned "No exact match".

## M3 + M4 — Import into Altium, linking, parameters ✅ (pending your test on real libraries)

### 1. What was implemented
- `alm/sources.py`: works out the source SchLib/PcbLib from a `.SchLib`, `.PcbLib`, `.LibPkg` (with or without `DocumentPath` entries), `.IntLib`, `.zip` (safe extraction) or a folder.
- `alm/altium/alm_import.pas`: the DelphiScript that runs inside Altium. It copies the symbol and its referenced footprints, re-links the footprint, sets the parameters, verifies the copy, runs the pin-to-pad check, handles name conflicts (skip or rename), saves, and closes what it opened. It also handles IntLib extraction (`IntegratedLibraryManager.ExtractSourcesToPath`).
- `alm/altium/bridge.py`: writes the job, launches `X2.EXE -RScriptingSystem:RunScriptFile(...)`, and parses the result.
- `alm/importer.py`: validates inputs, **backs up the destinations**, runs the job, and records the import in the database.
- UI rebuilt around the workflow (steps 1–3, plus Messages). Includes a double-click launcher.

### 2. How to run
Double-click `Altium Library Manager.bat`. See `README.md`.

### 3. What you should test
1. Make **copies** of your real SchLib and PcbLib in a test folder and use those as the destinations first.
2. Import the Ultra Librarian STDRIVE141TR package in `Downloads\ul_STDRIVE141TR\Altium`. Then try a SamacSys/CSE download, a SnapMagic zip and an IntLib.
3. In Altium, check each imported part:
   - The symbol looks right.
   - Footprint view shows the footprint.
   - The 3D view shows the STEP model.
   - Parameters include `LCSC Part #`.
   - The part places on a schematic, and Update PCB finds the footprint.
4. Import the same part again with **Skip**, then with **Import with new name**.

### 4. What works (verified here on AD26, using temporary libraries only)
- Ultra Librarian STDRIVE141TR into a test library that already held another part:
  - Symbol: 32/32 pins.
  - Footprint: 205/205 primitives, with the embedded `VFQFN32-5x5_STM.step` body.
  - Linked to `TestDest.PcbLib`.
  - Parameters added.
  - Pin check: all 32 pin designators have pads.
  - The existing part was preserved. All of this was re-checked by reopening the saved files from disk.
- A repeat import with Skip skipped the part. With Rename it created `STDRIVE141TR_1` linked to `VFQFN32-5x5_STM_1`.
- After a script failure, nothing was saved and the partial changes were discarded.
- IntLib extraction (XT60-M.IntLib) produced the SchLib and PcbLib.
- 37 offline unit tests pass.

### Findings about the Altium API (AD26)
- `ISch_Component.Replicate` does **not** copy pins or graphics, only parameters and implementations. The script copies the missing child objects itself and then verifies the counts.
- Altium does not mark a document as modified when a script edits it, so the "unsaved changes" check cannot detect changes made by a failed job. Instead, the job closes every document it opened, which discards partial changes.
- DelphiScript routines must be declared before they are called.

### 5. What remains incomplete
- If a footprint name already exists in the destination, Skip mode links the symbol to the existing footprint. The geometry is not compared; you get a warning instead.
- The `-L`/`-M` density variants in Ultra Librarian packages are not imported, only the footprint(s) the symbol references.
- No Replace option. Replacing an existing component needs a confirmation flow, which is planned.
- Pin *function* mapping cannot be verified automatically, and the result is always reported as such.
- Automatic downloading (M2) is postponed by your choice.

### 6. Known risks and limitations
- (Superseded 2026-11-19: the import now refuses if a destination library has unsaved edits.)
- Library vendors format symbols differently. Only Ultra Librarian has been tested; SamacSys, SnapMagic and EasyEDA exports may show new cases. The copy-count verification will stop the import rather than save a broken part.
- Backups accumulate in `%APPDATA%\AltiumLibraryManager\backups\` and are never deleted automatically.
