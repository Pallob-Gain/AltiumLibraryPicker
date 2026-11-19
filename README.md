# Altium Library Manager

A Windows tool that takes you from a manufacturer part number (MPN) to a linked symbol and footprint in **your own** Altium `.SchLib` / `.PcbLib`.

It runs without Claude, AI services or subscriptions, using only the Python standard library.

## Requirements
- Windows 10/11 and Altium Designer (tested on AD26 26.10.1.5)
- Python 3.10 or newer from [python.org](https://www.python.org/downloads/). Tick **"Add python.exe to PATH"** during setup. No `pip install` is needed; the app uses only the Python standard library.
- Optional: a free [Mouser Search API key](https://www.mouser.com/api-search/), for MPN search. Set it under Settings → API keys. It is stored encrypted for your Windows account and never written to the project folder.

## Download and start
1. On GitHub, click **Code → Download ZIP** and unzip it anywhere. You can also run `git clone https://github.com/Shihabul-glitch/Altium_Library_Mangement.git`.
2. Double-click **`Altium Library Manager.bat`**, or run `python -m alm` in the folder.
3. Have Altium Designer running. The app drives Altium through its scripting system, so nothing needs to be installed in Altium.

**Try it on copies of your libraries first.** Both destination libraries are backed up before every import, but test on copies before using it on libraries that matter.

## Workflow
1. **Part number:** paste the MPN and press Enter.
   - Mouser checks the exact MPN and fills in the manufacturer, description and datasheet parameters.
   - Green rows are exact matches. Orange rows are *similar, NOT exact*: they are different parts, so never use them.
2. **Find a library:** click a site button, or **Open ALL sites**, to open each site's search page for the MPN. Download the Altium library manually.
3. **Import into Altium:**
   1. Choose a source. There are three ways:
      - **Select file…** and pick what you downloaded: `.SchLib`, `.PcbLib`, `.LibPkg`, `.IntLib` or `.zip`. **Select folder…** also works. The app finds the source SchLib and PcbLib itself, and Altium extracts an IntLib first.
      - **From SamacSys library…** picks a part from the library the official **SamacSys Library Loader** adds parts to. Install the official loader from componentsearchengine.com and log in *in the loader*; this app never asks for your CSE password. With the loader running, download the part on Component Search Engine, then click the button. The first time, you select the SamacSys SchLib and PcbLib once.
      - **LCSC # + Fetch from EasyEDA (unofficial)** downloads the part's EasyEDA data by LCSC number (e.g. `C2040`) and builds it in Altium into new staging libraries. It uses the EasyEDA-Importer code. The data comes from an undocumented EasyEDA API (community library), so check the footprint and pinout against the datasheet. `LCSC Part #` is filled in automatically.
   2. **Component to import:** if the source library holds several symbols, pick one. The app pre-selects the one matching the MPN or LCSC number.
   3. Select your **Destination SchLib** and **Destination PcbLib**. They are remembered.
   4. Check the parameters. `LCSC Part #` is left blank unless you type it in (find the C-number with the LCSC button) or fetched the part by LCSC number.
   5. Click **Import into Altium**. Altium must be running; if it isn't, the app starts it.

What the import does, all inside Altium through its scripting API:
- Backs up both destination libraries to `%APPDATA%\AltiumLibraryManager\backups\<time>\`.
- Copies the symbol, and the footprint(s) the symbol references, including the embedded 3D STEP body.
- Links the symbol to the copied footprint. The library reference is set to your destination PcbLib's file name by default; full path and "any library" are also available.
- Sets the parameters `Manufacturer`, `Manufacturer Part Number`, `Datasheet` and `LCSC Part #`, plus the component Description. A blank field leaves the library's own value unchanged.
- Verifies the copy: pin and primitive counts must match the source, otherwise **nothing is saved**.
- Checks every symbol pin designator against the footprint's pad designators. This is an automatic check only; you still need to check pin *functions* against the datasheet.
- Saves, and reports everything in **Messages**.
- Checks that Altium's own library search finds the new footprint, so it is ready to place without restarting Altium. Destination libraries you had open are closed during the import and reopened afterwards. (AD26 only refreshes its library search when a library that is *not* open in an editor is saved.) If a footprint is still reported "not found", the destination PcbLib is probably neither in an open project nor installed; the "Full library path" link mode works without either. Otherwise, restart Altium.

### Safety rules
- An existing symbol or footprint is never overwritten. With **Skip**, a duplicate symbol is not imported, and a footprint whose name already exists is reused (with a warning to verify it). With **Import with new name**, the new copy gets a `_1` suffix.
- If anything fails, nothing is saved and the libraries the job opened are closed without saving.
- If a destination library is open in Altium **with unsaved changes**, the import refuses to run. Save or close the library first.

## Data folder `%APPDATA%\AltiumLibraryManager\`
`settings.json` · `credentials.json` (encrypted with Windows DPAPI) · `alm.db` (imported components and search log) · `alm.log` · `backups\` · `jobs\` (each import's job, script and result) · `extracted\` · `easyeda\` (EasyEDA downloads, `cache\` part files, `3D\` STEP models, `stage\` built staging libraries)

## Contributing
Changes are welcome: fork the repository, make the change, run the tests (below), and open a pull request. The design notes are in `architecture.md`. Altium-side code is plain DelphiScript in `alm/altium/*.pas`; keep changes small and test them on temporary libraries.

## Credits
The EasyEDA converter (`alm/easyeda/convert.py`) and the part builder (`alm/altium/ee_build.pas`) come from **EasyEDA-Importer** (github.com/hiteshwar/EasyEDA-Importer), by the same author, and are included under this project's licence.

## Licence
[MIT](LICENSE): free to use, copy, modify and redistribute, including commercially; keep the licence notice. There is no warranty, so always verify footprints and pinouts against the datasheet.

Not affiliated with Altium, Mouser, SamacSys/Supplyframe, EasyEDA, LCSC or JLCPCB. Library data from those sites is subject to their own terms.

## Tests
`python -m unittest discover -s tests -v` runs offline and does not need Altium.

## Documents
`requirement.md` · `architecture.md` · `roadmap.md` · `progress.md`
