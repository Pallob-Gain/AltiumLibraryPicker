# Progress

## Environment (verified 2026-11-18)
- Windows 11 Pro; Altium Designer **26.10.1.5** at `C:\Program Files\Altium\AD26`.
- Python **3.12.10** installed per-user (Tk 8.6, SQLite 3.49.1).
- The Mouser API key is stored with DPAPI encryption. The live search was verified.

## Scope change (2026-11-18, user request)
The user chose a **manual download + automatic import** workflow. Automatic downloading (M2) is postponed. The Altium import and linking work (M3 + M4) was pulled forward and is done.

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
