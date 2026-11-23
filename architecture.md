# Altium Library Manager — Architecture and Integration Decisions

Status: historical architecture and decision log. The Rust migration is incomplete; see
the current status in [README.md](README.md). The Rust import bridge has mocked
tests but has not yet been verified end-to-end with Altium. Sections below also
describe the earlier Python implementation and planned features, not necessarily
the current Rust behavior.

Verification legend:
- ✅ **Verified**: confirmed from official documentation.
- ☑ **Reported**: confirmed by credible third-party sources, not yet tested by us.
- ❓ **Unverified**: must be tested before we rely on it.

---

## 1. Summary of the decision

```
┌─────────────────────────── Windows PC ───────────────────────────────┐
│                                                                       │
│  Rust desktop app (GTK4 XML interface)                                │
│   ├─ providers/   API providers (Mouser, Nexar, DigiKey…)              │
│   │               + manual-assist link providers (all others)          │
│   ├─ ranking      exact-match logic, recommendation                    │
│   ├─ downloads/   cache with provenance, safe extraction   (M2)        │
│   ├─ components/  parameter mapping, validation            (M4)        │
│   ├─ db           SQLite (component DB, search log)                    │
│   ├─ secrets      Windows DPAPI-encrypted API keys                     │
│   └─ altium/      job-file bridge                          (M3)        │
│          │ writes job.json        ▲ reads result.json                  │
│          ▼                        │                                    │
│  Altium Designer AD26  ── DelphiScript project "ALM_Import.PrjScr" ──  │
│     SchServer / PCBServer API: open source + destination libs,        │
│     copy component, set parameters, link footprint, save              │
└───────────────────────────────────────────────────────────────────────┘
```

| Decision | Choice | Why |
|---|---|---|
| Language | Rust 2021 edition | Native Windows executable, strong type safety and no Python runtime. |
| UI | **GTK4** | The interface is defined in `resources/app.ui` and loaded through GtkBuilder. |
| Runtime dependencies | Reqwest, rusqlite, serde, GTK4 | Each dependency is used for HTTP, local storage, serialization or the desktop UI. |
| Altium modification | DelphiScript running **inside Altium**, driven by a JSON job file | Altium's supported way to edit libraries is its scripting API (SchServer/PCBServer). Python never writes `.SchLib`/`.PcbLib` binaries itself (F5-3). |
| Altium extension (C# SDK) | Rejected for now | Needs the Altium SDK, a build pipeline and per-version redeployment, which is too heavy for one user. EasyEDALoader shows it can be done, but it is GPL-3.0. |
| Data store | SQLite file + JSON settings in `%APPDATA%\AltiumLibraryManager\` | Part of the standard library, kept outside the source tree. |
| Credentials | Windows DPAPI (`CryptProtectData` via `ctypes`), with an environment-variable override | Encrypted per Windows user, never in the source code (R-9). |

## 2. Provider research (F1-4)

| Source | Official programmatic access | Access requirement | What it gives us | Decision |
|---|---|---|---|---|
| **Mouser Search API v1** | ✅ `POST https://api.mouser.com/api/v1/search/partnumber?apiKey=…` with an `Exact` option; up to 50 records per response. | Free My Mouser account plus a Search API request form; key sent by email. | Exact MPN, manufacturer, description, datasheet URL, product page, Mouser P/N, packaging attributes. **No CAD availability.** | **M1 primary provider.** It is the simplest legitimate integration (one key, no OAuth). |
| **Octopart / Nexar** (Altium) | ✅ GraphQL `supSearchMpn(q:)`. Token via OAuth2 client credentials. | Nexar account. Trial parts on sign-up (100 with a personal email, 1,000 with a business email). Each returned part counts against the limit. **`SupPart.cad` (ECAD models) is Enterprise-only; `bestDatasheet` is Pro.** | Manufacturer verification and Octopart URL on the free tier. CAD availability only on an Enterprise plan. | M1 optional provider (manufacturer verification). Do not depend on it for CAD data. |
| **Altium Manufacturer Part Search** | No external API. It is an Altium panel backed by Octopart/Nexar data. | Altium licence. | Same data as Nexar. | Manual: show instructions to open the panel in Altium. The Octopart link covers it on the web. |
| **DigiKey Product Information v4** | ☑ `POST https://api.digikey.com/products/v4/search/keyword`, OAuth2 client credentials (`/v1/oauth2/token`). The app must be subscribed to the product. | Free developer account. | Manufacturer, datasheet, package, DigiKey P/N. | Optional provider after M1. |
| **Component Search Engine / SamacSys Library Loader** | No public developer API found (re-checked 2026-11-19). The Supplyframe ToS §4.2(e) forbids "software or automated agents or scripts" that "generate automated searches, requests and queries, or … strip or mine data". | Free account; downloads need a login. Library Loader is SamacSys' own Windows client. | Altium-native libraries (zip with SchLib/PcbLib or IntLib). | **Browser search + in-app download:** open the CSE search in the user's browser and watch the Downloads folder (`alm/cse.py`). A `.epw` part file triggers the model download with the user's DPAPI-stored login (`alm/cse_client.py`, the user states they have the owner's permission); a full zip is imported directly. Also import from the official Library Loader's library. |
| **SnapMagic** (snapeda.com) | ☑ A Search API exists but is "request access" with no public documentation. | Partner approval. Website downloads need a login and are capped per day. | Altium-native symbol + footprint + STEP. | **Manual-assist** link. Revisit if API access is granted. |
| **Ultra Librarian** | No public API found. Native Altium export added Oct 2025. | Login required. | Altium-native export (zip). | **Manual-assist** link plus import from disk. |
| **LCSC official Open API** | ✅ Documented at lcsc.com/docs/openapi (keyword search, SHA-1 signed key/nonce/timestamp). | **Approval required** (contact LCSC). Keys start in a test environment. | LCSC part number, a reliable `LCSC Part #`. | Implement if the user obtains keys. Until then, enter `LCSC Part #` manually. |
| **EasyEDA component data** | **Unofficial and undocumented** (`easyeda.com/api/products/<LCSC>/components`), used by easyeda2kicad and EasyEDA-Importer. | None, but it can change or break without notice. | Symbol and footprint geometry plus STEP for LCSC parts (EasyEDA format, not Altium-native). | **Implemented 2026-11-19** as the opt-in, user-triggered "Fetch from EasyEDA (unofficial)" button, using EasyEDA-Importer's converter and builder (§3). |
| **Manufacturer CAD pages** (TI, ST, Microchip…) | Mostly redirect to Ultra Librarian or SamacSys. | Varies. | Varies. | Manual link via the datasheet or product page. |

Consequence: **no free, official API reports CAD-model availability.** The Symbol, Footprint and 3D columns therefore show **Unknown** for API results until we have Nexar Enterprise, SnapMagic API approval, or a downloaded package to inspect (M2). This is intentional (F2-2, G-5).

## 3. Existing open-source projects (§14)

| Project | Licence | What it does | Reuse decision |
|---|---|---|---|
| hiteshwar/EasyEDA-Importer | No LICENSE file, but the **owner is this tool's user** and granted permission (2026-11-19). | PowerShell/Python fetch EasyEDA data into a TSV part file, then a DelphiScript builds the footprint, 3D body, symbol and parameters. | **Reused:** `ee_convert.py` → `alm/easyeda/convert.py`; the builder routines → `alm/altium/ee_build.pas`. Local changes are marked "ALM:". |
| olback/library-loader | **AGPL-3.0** | Rust "[Unofficial] Samacsys Library Loader"; reads CSE login details from a config file and downloads parts directly (Eagle/KiCad; Altium not mentioned). | **Not used, no code copied.** Its direct-login approach relies on undocumented endpoints and conflicts with the Supplyframe ToS; we implement the browser workflow instead (original code). |
| lethargicpillow/Altium-Library-Loader | No licence; its README says "All rights belong to supplyframe.com and SamacSys". | Inno Setup repackaging of the proprietary SamacSys Library Loader installer. | **Not used.** The official Library Loader is used instead, and its output library is a source (no CSE credentials handled by us). |
| expired6978/EasyEDALoader | **GPL-3.0** | C# Altium extension (Altium.SDK) with a WPF standalone app. | **Ideas only.** Copying code would make our tool GPL-3.0. |
| Altium-Designer-addons/scripts-libraries | "No particular license" unless a script states one | Community script collection ("Scripts - Parts library", etc.). | Read for API usage patterns. Copy only scripts with an explicit permissive licence, and record each one here. |

## 4. Altium integration design (M3–M4)

Facts from the official documentation:
- ✅ Scripts run through the `ScriptingSystem:RunScript` process (`ProjectName=…PrjScr|ProcName=Unit.pas>Proc`) or `ScriptingSystem:RunScriptFile` (`FileName=…pas|ProcName=…`, no forms).
- ✅ A script project installed as a **Global Project** loads automatically when Altium starts.
- ☑ Launching from the shell with `X2.EXE -RScriptingSystem:RunScript(ProjectName="…"|ProcName="…")` is used in community batch scripts (AD19). ❓ This is not documented by Altium and must be tested on AD26.
- ☑ Library edits use the SchServer/PCBServer APIs (for example `PCBServer.CreatePCBLibComp`, `IPCB_Library.RegisterComponent`, with `PreProcess`/`PostProcess`). ❓ The exact AD26 signatures must be confirmed against the Altium Scripting API reference and with a tiny test script before we write more.

Bridge protocol (planned):
1. Python writes `%APPDATA%\AltiumLibraryManager\jobs\<id>\job.json`. It names the source library files (from the download cache), the component name, the destination SchLib/PcbLib, the parameters, and a conflict policy (always `skip-if-exists` unless the user confirmed otherwise).
2. Python backs up the destination libraries (timestamped copies) **before** the job runs.
3. The job is triggered by either (a) `X2.EXE -RScriptingSystem:RunScript(...)` if that works on AD26, or (b) the user clicking one menu or toolbar command "ALM: Run pending import" in Altium.
4. The script performs the copy through the Altium API, saves, and writes `result.json` (what it added, warnings, errors).
5. Python reads `result.json`, updates the database and shows the report.

DelphiScript is written in small, separately tested procedures (open lib → find component → copy → set parameter → link model → save). It is not one large script.

## 5. Matching and ranking rules (F2)

- MPN comparison is **case-insensitive and ignores whitespace only**. Hyphens, slashes and suffixes are significant (`MCP23017-E/ML` ≠ `MCP23017T-E/ML`; `T` is the tape-and-reel variant). Any other difference is labelled **Similar — not exact** and never ranked above an exact match.
- When an exact MPN appears under more than one manufacturer, a "manufacturer ambiguous" warning is shown.
- Score order: exact match → provenance (official API > downloaded package > manual link) → native Altium → completeness (symbol + footprint + 3D, counting only *Yes*, never *Unknown*) → datasheet available.
- The recommendation text states its reasons and its caveats (for example "CAD availability unknown — check SnapMagic/Ultra Librarian manually").

## 6. Source code layout

```
Altium_Library_Tool/
  alm/                      Python package (import name "alm")
    __main__.py             python -m alm → starts the UI
    config.py               paths, settings JSON (last-used libraries, etc.)
    credentials.py          DPAPI-encrypted credential store
    validation.py           file checks (OLE header for Altium libraries)
    models.py               SearchResult, Availability, MatchStatus, ProviderStatus
    ranking.py              exact-match normalisation, scoring, recommendation
    db.py                   SQLite: components, search_log
    log.py                  rotating log file in %APPDATA%
    providers/
      base.py               Provider interface + HTTP helper
      mouser.py             Mouser Search API (official)
      nexar.py              Nexar/Octopart GraphQL (official, optional; deferred, not in M1)
      links.py              manual-assist search URLs
    search.py               runs providers, collects statuses, ranks
    ui/app.py               Tkinter UI
    downloads/              (M2)
    components/             (M4) parameter mapping, validation
    altium/                 (M3) job writer/reader + DelphiScript project
  tests/                    unittest, offline (recorded fixtures)
```

## 7. Security notes

- API keys are encrypted with DPAPI and stored in `%APPDATA%\AltiumLibraryManager\credentials.json`. They are never written to logs.
- HTTP calls use HTTPS with timeouts and a generic User-Agent, and send only the MPN.
- Archive extraction (M2) rejects absolute paths, `..` entries, symlinks and oversized members, and extracts into a fresh directory.
- No Altium project files are ever uploaded.

## 8. Decision log

| Date | Decision |
|---|---|
| 2026-11-18 | Initial architecture: Python + Tkinter, standard library only, DelphiScript job-file bridge, Mouser as the M1 provider, Nexar optional. |
| 2026-11-18 | Bridge verified on AD26: `X2.EXE -RScriptingSystem:RunScriptFile(FileName=…|ProcName=Run)` is forwarded to the running Altium. Job/result files are key=value text (TStringList), not JSON. The script is generated per job from `alm/altium/alm_import.pas`. Symbol copy = `Replicate` plus copying the missing child objects (Replicate drops pins). Footprint copy = `CreatePCBLibComp` plus replicated primitives (the embedded STEP is kept). Copy counts are verified before saving. The user chose manual download + import (M2 auto-download postponed). |
| 2026-11-19 | **Library-search refresh (verified AD26).** `IntegratedLibraryManager.FindDatafileInStandardLibs` (the lookup used for placement, model preview and browse) is refreshed only after the saving script run has *ended*, from the still-loaded document, and not if the library was open in an editor while saved. Not fixed by close/reopen, `DM_Compile`, `InstallLibrary`, board-registration messages or timestamp changes. Import is therefore prepare (close editor docs, refuse if modified) → import (`KeepOpen=1`) → finish (check lookup, close, reopen the user's docs). |
| 2026-11-19 | **CSE integration = browser workflow, no in-app login.** The user asked for an in-app email/password login with requirement 9: "verify it is permitted, otherwise use the supported workflow". Findings: there is no public CSE API, the only official client is SamacSys' Library Loader, the Supplyframe ToS §4.2(e) prohibits automated searches/requests, and olback/library-loader is AGPL and unofficial. So: open the CSE search in the browser (the user signs in there; the browser keeps the session), watch the Downloads folder (`DownloadWatcher`: ignores pre-existing files, waits for `.crdownload`/`.part` to finish and the size to settle, prefers files named after the MPN), copy the file into `%APPDATA%\…\downloads`, then the normal import (auto-import is optional and on by default). No CSE credentials are requested, stored or sent. |
| 2026-11-19 (later) | **CSE in-app login and model download added at the user's request.** The user stated that they have the owner's permission. This overrides the earlier "browser only" decision for downloads.<br>**Protocol** (verified live): `GET https://componentsearchengine.com/ga/model.php?partID=<id>` with HTTP Basic auth. It returns 401 `realm="Samacsys Parts Database"` without a login, accepts the user's real login, and returns `Error: no part data available for partID N` for unknown IDs.<br>**Part ID source:** the `.epw` file that CSE's download button saves. Searching is still done in the browser, because CSE pages are behind Cloudflare and are not scraped.<br>**Implementation:** `alm/cse_client.py` is original code; no AGPL code was copied (olback/library-loader was used only to learn the protocol).<br>**Safety:** redirects are refused, so the `Authorization` header can't leak; the download size is capped; the result must be a real zip.<br>**Credentials:** DPAPI ("Remember login") or session-only; never logged or committed. |
| 2026-11-20 | **CSE direct search + download** (user's own method, see downloader.py): `GET https://ad.componentsearchengine.com/ga/search.php?kws=<MPN>&v=1` (Basic auth) returns JSON `Parts{id:{PartNumber, Manufacturer, ModelAvailable, Have3D, Symbol, Footprint, Confidence, …}}`. Exact-MPN filter (case/whitespace only), manufacturer from the Manufacturer field or a chooser, then `model.php` and the normal import. **Celestial Altium Library** (issus/altium-library): no MPN API; DbLib + per-user cloud SQL login + MS SQL drivers, so not automated. It is offered as a documented fallback; its custom licence allows commercial use but not reselling the models. |
| 2026-11-19 | Multi-symbol sources: the read-only `schlib_reader` lists symbols; the job's `Component=` imports one. Sources added: the SamacSys Library Loader library, and EasyEDA by LCSC number (fetch → part file → `ee_build.pas` into new staging libs → normal import). |
| 2026-11-20 | **Search provider: Mouser API replaced by LCSC (no key).** The user asked for this to make the app easier for others, and stated they have LCSC's permission. `alm/providers/lcsc.py` uses the public LCSC/JLCPCB parts search (`POST jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList`, JSON `{keyword, currentPage, pageSize}`; the item fields used are componentCode, componentModelEn, componentBrandEn, componentSpecificationEn, describe, stockCount, dataManualOfficialLink/dataManualUrl and lcscGoodsUrl; only https links are kept). Exact listings are sorted by stock; LCSC Part # is taken only from an exact MPN plus the same manufacturer. The Mouser provider, the API-keys dialog and a stored `mouser_api_key` are removed. "Mouser (web)" stays as a site link. Also removed: the SamacSys library source button. Added: Ultra Librarian (browser download + automatic import; the CAPTCHA is not bypassed) and "Look for download". |
