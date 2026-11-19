<p align="center"><img src="alm/ui/app_preview.png" width="110" alt="Altium Library Manager icon"></p>

# Altium Library Manager

**Get a part into your own Altium library in a few clicks:** symbol, footprint and 3D model, linked and ready to place.

Type a manufacturer part number (MPN), click **From Mouser (ECAD model)…**, click the ECAD model icon on the Mouser page that opens, and the part is imported into **your own** `.SchLib` / `.PcbLib` automatically:
- the footprint is linked to the symbol;
- the 3D model is attached;
- the parameters are filled in;
- your libraries are backed up first.

Free and open source (MIT). No Python or other software to install. It works with Altium Designer on Windows 10/11.

![Main window](docs/screenshot.png)

---

## 1. Download and start (2 minutes)

1. Go to the **[Releases page](https://github.com/Shihabul-glitch/Altium_Library_Mangement/releases/latest)** and download **`AltiumLibraryManager.exe`**.
2. Put it anywhere, for example on your Desktop or in `Documents`, and **double-click it**. There is nothing to install.
3. **The first time, Windows may show "Windows protected your PC".** The app is free and unsigned (a code-signing certificate costs money). Click **More info → Run anyway**. Windows remembers your choice.
4. **Start Altium Designer.** The app works through Altium itself; if Altium isn't running when you import, the app offers to start it.

> **Before you use your real libraries, try it on copies of them.** The app backs up your libraries before every import and never overwrites existing parts, but trying it on copies first is good practice.

## 2. One-time setup

| What | Where | Needed? |
|---|---|---|
| Your **destination libraries** (where parts go) | Step 3 → **Destination SchLib / Destination PcbLib → Select…** | **Yes**, once. The app remembers them. |
| A **Component Search Engine** account (free) | Create one at [componentsearchengine.com](https://componentsearchengine.com/). Mouser asks for this account when you download an ECAD model. | For Mouser and CSE downloads |
| Your own **Mouser API key** (free) | [Get a key](https://www.mouser.com/api-search/), then in the app: **Settings → API keys…** | Optional: only for the search table in step 1 and 2 |
| CSE login inside the app | **Settings → Component Search Engine…** | Optional: see [Component Search Engine](#c-component-search-engine-cse) |

Keys and logins are stored **encrypted for your Windows account** (Windows DPAPI). They are never stored in the app file or uploaded anywhere except to the service they belong to. Every user enters **their own** Mouser key; none is built in.

## 3. Get a part: four ways

Enter the MPN in **1. Part number** first. Most buttons use it.

### A. Mouser (recommended, easiest)
1. In **3. Import into Altium**, click **From Mouser (ECAD model)…**. Mouser opens in your browser with the part.
2. On the part page, click the **ECAD Model** icon (symbol, footprint and 3D). If asked, sign in with your Component Search Engine account.
3. Your browser saves `LIB_<part>.zip`. The app notices it, shows the download progress, and **imports the part automatically**: symbol, footprint and 3D model.

### B. A library you downloaded yourself
From Ultra Librarian, SnapMagic, Component Search Engine, or a manufacturer website: click **Select file…** and pick the `.zip`, `.IntLib`, `.LibPkg`, `.SchLib` or `.PcbLib`. You can also click **Select folder…**. The app finds the symbol and footprint libraries inside. Then click **Import into Altium**.

The **Find a library** buttons on the right open each site's search for your MPN, or **Open ALL sites** opens them all.

### C. Component Search Engine (CSE)
1. Click **From Component Search Engine (CSE)…**. CSE opens in your browser.
2. Click the part's download button.
   - If you get a full library zip, it is imported directly.
   - If you get a small Library Loader file (`.epw`), the app downloads the model itself with your CSE login. Set the login up once in **Settings → Component Search Engine…**: enter your email and password, tick **Remember login**, then click **Sign in & test**. **Log out** deletes the saved login.

### D. LCSC / JLCPCB parts via EasyEDA
Type the LCSC number (for example `C2040`) in **EasyEDA by LCSC #** and click **Fetch**. The app downloads the EasyEDA data and builds the part in Altium, then you click **Import into Altium**. `LCSC Part #` is filled in automatically.

*EasyEDA data is community-made and comes from an undocumented EasyEDA service, so always check the pinout and footprint.*

## 4. What happens when you import

- **Backup:** both destination libraries are copied to `%APPDATA%\AltiumLibraryManager\backups\` first.
- **Copy:** the symbol and its footprint are copied into your libraries, and the footprint is **linked** to the symbol.
- **3D:** the 3D model is attached when the download has one but the footprint doesn't, as with Mouser and SamacSys parts.
- **Parameters:** `Manufacturer`, `Manufacturer Part Number`, `Datasheet`, `LCSC Part #` and Description are set. An empty field leaves the library's own value alone.
- **Checks:** pin and footprint counts must match, otherwise **nothing is saved**. Every pin number must have a matching pad; this is an automatic check only, so still compare pin functions with the datasheet.
- **Ready to place:** the app confirms Altium can find the new footprint immediately, with no restart. Libraries you had open in Altium are closed during the import and reopened afterwards.

**Duplicates:** with **Skip (never overwrite)**, a part that already exists is left alone. With **Import with new name (_1)**, the new copy gets a suffix. Existing parts are never replaced or deleted.

Everything is reported in **Messages**: green is OK, orange is a warning, red is an error.

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| "Windows protected your PC" | Click **More info → Run anyway** (see above). |
| Antivirus warns about the .exe | One-file Python apps sometimes trigger false alarms. You can check the source code here, or [run from source](#run-from-source-for-developers) instead. |
| "A destination library is open in Altium with unsaved changes" | Save or close that library in Altium, then import again. |
| "No answer from Altium" | Make sure Altium is running and no dialog is waiting in Altium, then retry. |
| Footprint "not found" when placing | The destination PcbLib must be in your open project or installed in Altium. Alternatively, choose **Footprint link: Full library path** before importing. |
| Mouser/CSE download is not picked up | The app watches your normal Downloads folder. If your browser saves somewhere else, set that folder in **Settings → Component Search Engine…**. |
| CSE keeps showing its login page | Sign out and in again on componentsearchengine.com, or use the **Mouser** button, which gives the same files. |
| Mouser search says "Not configured" | Add your own Mouser API key in **Settings → API keys…**. The import works without a key. |
| The 3D model sits in the wrong place | Open the footprint in Altium, press **3**, and move or rotate the 3D body. Downloaded models are placed at the footprint origin. |

Your data lives in `%APPDATA%\AltiumLibraryManager\` (settings, encrypted logins, backups, log file `alm.log`). Open it with **Settings → Open data folder**. To uninstall, delete the `.exe` and that folder.

## Run from source (for developers)

- **Requirements:** Windows, Python 3.10+ from [python.org](https://www.python.org/downloads/). No `pip install` is needed to run.
- **Start:** double-click `Altium Library Manager.bat`, or run `python -m alm`.
- **Tests:** `python -m unittest discover -s tests -v` (offline; Altium is not needed).
- **Build the .exe:** `pip install pyinstaller pillow`, then `python tools/build_exe.py`. The result is `dist/AltiumLibraryManager.exe`. `tools/make_icon.py` draws the icon.
- **Design notes:** `architecture.md`, `progress.md`, `requirement.md`, `roadmap.md`. Altium-side code is DelphiScript in `alm/altium/*.pas`; test changes on temporary libraries.

Contributions are welcome: fork, change, run the tests, and open a pull request.

## Credits and licence

- The EasyEDA converter (`alm/easyeda/convert.py`) and part builder (`alm/altium/ee_build.pas`) come from [EasyEDA-Importer](https://github.com/hiteshwar/EasyEDA-Importer), by the same author.
- Licensed under [MIT](LICENSE): free to use, copy, modify and share, including commercially; keep the licence notice. **No warranty.** Always check footprints and pinouts against the datasheet before ordering PCBs.
- Not affiliated with Altium, Mouser, SamacSys/Supplyframe (Component Search Engine), EasyEDA, LCSC or JLCPCB. Library data from those sites is subject to their own terms.
