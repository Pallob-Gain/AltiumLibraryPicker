# Altium Library Manager — Product Requirements

| Item | Value |
|---|---|
| Document | `requirement.md` |
| Status | Baseline v1.0 (2026-11-18) |
| Previous content | The original file (`Requirment.md`) was empty, so nothing had to be preserved. It was renamed to `requirement.md`. |
| Related | `architecture.md`, `roadmap.md`, `progress.md`, `README.md` |

Keywords: **SHALL** = mandatory, **SHOULD** = recommended, **MAY** = optional, **FUTURE** = not part of the first version.

---

## 1. Objective

Minimise the time between knowing a component's Manufacturer Part Number (MPN) and having a verified, ready-to-use component in the user's existing Altium libraries.

### 1.1 Manual workflow being replaced

1. Search Altium Manufacturer Part Search.
2. Search Component Search Engine / Library Loader.
3. Search Octopart.
4. Search SnapMagic (formerly SnapEDA).
5. Search Ultra Librarian.
6. Search LCSC / EasyEDA.
7. If nothing suitable exists, create the symbol and footprint manually.

### 1.2 Global constraints

| ID | Requirement |
|---|---|
| G-1 | The application SHALL work without Claude, AI API subscriptions, MCP servers or other paid AI services once development is finished. |
| G-2 | The application SHALL run on Windows alongside Altium Designer (installed version: AD26, 26.10.1.5). |
| G-3 | The application SHALL use official APIs or documented integrations where they exist. |
| G-4 | The application SHALL NOT bypass logins, CAPTCHAs, access restrictions or download licensing requirements. |
| G-5 | The application SHALL NOT claim a source was searched when it was not. |
| G-6 | The application SHALL minimise dependencies and prioritise working functionality over styling. |
| G-7 | The application SHALL avoid unstable or undocumented endpoints where possible. Any unofficial integration SHALL be opt-in and clearly labelled "unofficial". |

---

## 2. Feature 1 — MPN search

| ID | Requirement |
|---|---|
| F1-1 | The application SHALL provide a simple input for an MPN (for example STM32F103C8T6, ESP32-S3-WROOM-1-N8R2, MCP23017T-E/ML, TPA3116D2DADR, STDRIVE141TR). |
| F1-2 | The application SHALL identify the manufacturer, or help the user verify it. If one MPN maps to several manufacturers, it SHALL say so. |
| F1-3 | The application SHALL cover these sources: Altium Manufacturer Part Search, Component Search Engine / Library Loader, Octopart, SnapMagic, Ultra Librarian, LCSC / EasyEDA, and optional direct manufacturer CAD resources. |
| F1-4 | Each provider's official API, documented integration, download mechanism, access requirements and limits SHALL be investigated and recorded in `architecture.md`. |
| F1-5 | Where a source cannot be queried through a supported interface, the application SHALL offer a manual-assisted workflow that opens the correct search page for the MPN. |
| F1-6 | Each search SHALL report every provider's status: *searched (n results)*, *not configured*, *error*, or *manual — not searched*. |

## 3. Feature 2 — Search results and ranking

| ID | Requirement |
|---|---|
| F2-1 | Each result SHALL show: exact MPN, manufacturer, source/provider, schematic symbol availability, PCB footprint availability, 3D STEP availability, native Altium format availability, download availability, LCSC part number (only if verified), datasheet link, source URL, and confidence/verification status. |
| F2-2 | Availability SHALL be tri-state: Yes / No / Unknown. "Unknown" SHALL NOT be shown as "Yes". |
| F2-3 | Results SHALL be ranked by: (1) exact MPN match, (2) correct package and pin configuration, (3) reliable source with traceable provenance, (4) native Altium compatibility, (5) complete symbol + footprint + 3D, (6) ease of download and import. |
| F2-4 | The application SHALL show a recommended source with a short explanation. |
| F2-5 | A similar part SHALL NEVER be presented as an exact match. If there is no exact match, the application SHALL say so. |
| F2-6 | When an MPN has several package variants, the application SHALL NOT silently substitute another package. |

## 4. Feature 3 — Automatic downloading

| ID | Requirement |
|---|---|
| F3-1 | Where supported, the application SHALL automatically download the schematic symbol, PCB footprint, 3D STEP model and any associated metadata. |
| F3-2 | It SHALL support `.SchLib`, `.PcbLib` and `.IntLib` through documented import workflows. |
| F3-3 | For archives, it SHALL identify the relevant files. |
| F3-4 | Where automatic download is unavailable, it SHALL provide a button that opens the exact component page for manual download. |
| F3-5 | It SHALL allow importing a previously downloaded component package from disk. |
| F3-6 | Downloads SHALL be stored in a structured local cache with provenance information (source, URL, date, hash). |
| F3-7 | It SHALL avoid repeating downloads it already has. |

## 5. Feature 4 — Destination library selection (high priority)

| ID | Requirement |
|---|---|
| F4-1 | The user SHALL be able to browse to and select an existing destination `.SchLib`. |
| F4-2 | The user SHALL be able to browse to and select an existing destination `.PcbLib`. Paths are user-chosen and never hard-coded. |
| F4-3 | The application SHALL remember the last-used library selections. |
| F4-4 | FUTURE: separate library selections per category (resistors, capacitors, ICs, connectors, other). |

## 6. Feature 5 — Import into Altium

| ID | Requirement |
|---|---|
| F5-1 | After download, the application SHALL show a preview and an Import button. |
| F5-2 | On confirmation, it SHALL: open the destination libraries through supported Altium interfaces; add the symbol to the `.SchLib`; add the footprint to the `.PcbLib`; import or attach the STEP model; link the symbol to the footprint; set the footprint library reference; preserve component parameters; save through Altium; and report what was added or changed. |
| F5-3 | Libraries SHALL be modified only through Altium's supported scripting or extension interfaces. Undocumented binary library structures SHALL NOT be written directly. |
| F5-4 | An existing component SHALL NEVER be overwritten automatically. On a name clash, the application SHALL offer: Skip / Inspect existing / Import with a new name / Replace (only after explicit confirmation). |
| F5-5 | A backup SHALL be created before any modification. |

## 7. Feature 6 — Automatic footprint linking

| ID | Requirement |
|---|---|
| F6-1 | When importing a symbol and footprint together, the application SHALL link them automatically. |
| F6-2 | It SHALL verify the footprint name, footprint library, pin-to-pad mapping, pad designators and relevant package information. |
| F6-3 | If the pin mapping cannot be verified, it SHALL show a warning requiring manual review. |
| F6-4 | A matching pin count and pad count SHALL NOT, on its own, mark a component as validated. |
| F6-5 | It SHOULD support multi-unit symbols, thermal or exposed pads, and unusual pin configurations where technically feasible. |

## 8. Feature 7 — Component parameters

| ID | Requirement |
|---|---|
| F7-1 | Required parameters: `Manufacturer`, `Manufacturer Part Number`, `Description`, `Datasheet`, `LCSC Part #`. |
| F7-2 | The parameter name `LCSC Part #` is mandatory and SHALL be spelled exactly that way. |
| F7-3 | Optional parameters: `Supplier`, `Supplier Part Number`, `Package`, `Value`, `Voltage Rating`, `Current Rating`, `Tolerance`, `Temperature Rating`. |
| F7-4 | The LCSC number SHALL be filled in automatically only when it is reliably retrieved. Otherwise it SHALL be left blank for manual entry. |
| F7-5 | The application SHALL NEVER invent an LCSC number or electrical specifications. Example values in this document are not real data for any component. |
| F7-6 | A configurable parameter-mapping system SHALL convert each source's naming convention into the standard names. |

## 9. Feature 8 — Preview and verification

| ID | Requirement |
|---|---|
| F8-1 | Before import, the application SHALL show what is available for: symbol, footprint, 3D model, datasheet, pin count, package dimensions and source information. |
| F8-2 | It SHOULD show visual previews where technically possible. It SHALL NOT fabricate previews or mark geometry as verified without checking it. |
| F8-3 | It SHALL warn on: missing symbol, missing footprint, missing 3D model, pin/pad mismatch, duplicate component, suspicious package mismatch, and an unverified LCSC number. |
| F8-4 | Every check SHALL be labelled as one of: *Automatically checked*, *Needs manual verification*, or *Verified by user*. |

## 10. Feature 9 — No library found

| ID | Requirement |
|---|---|
| F9-1 | When nothing suitable is found, the application SHALL show: "No suitable library was found for this exact component." |
| F9-2 | It SHALL then offer: open the manufacturer datasheet; create a symbol manually; create a footprint manually; import an existing local library; request a model from a supported provider (if available). |
| F9-3 | Automatic creation from datasheets SHALL NOT be part of the first version (see FUTURE). |

## 11. Feature 10 — User interface

| ID | Requirement |
|---|---|
| F10-1 | The application SHALL have a simple Windows desktop UI. Top: MPN field and Search button. Middle: results (source, symbol, footprint, 3D, recommendation). Bottom: Download, Select SchLib, Select PcbLib, Import, and status/validation messages. |
| F10-2 | The technology SHALL be lightweight and maintainable. Rust + GTK4 for the XML-defined desktop UI and an Altium-side DelphiScript bridge are used; see `architecture.md`. |
| F10-3 | There SHALL be no unnecessary styling or animation. |

## 12. Feature 11 — Local component database

| ID | Requirement |
|---|---|
| F11-1 | A local SQLite database SHALL record: MPN, manufacturer, LCSC part number, source/provider, downloaded file paths, destination SchLib, destination PcbLib, footprint name, import date and verification status. |
| F11-2 | Searching for an MPN that was already imported SHALL show that it exists and where it is stored. |

## 13. Feature 12 — Future improvements (not in the first version)

- Bulk MPN import from CSV
- Import of an entire BOM
- Automatic missing-component detection
- Automatic 3D model matching
- Datasheet-assisted symbol generation
- IPC-compliant footprint generation
- Library duplicate detection
- Component version tracking
- Alternative component suggestions
- LCSC stock and pricing lookup
- One-click placement into an open schematic
- Optional Altium MCP integration
- Per-category destination libraries (F4-4)

## 14. Existing projects and documentation to investigate

| Resource | Purpose |
|---|---|
| https://github.com/hiteshwar/EasyEDA-Importer | EasyEDA → Altium importer (DelphiScript + PowerShell) |
| https://github.com/Altium-Designer-addons/scripts-libraries | Community Altium scripts |
| https://github.com/expired6978/EasyEDALoader | C# Altium extension for EasyEDA parts |
| https://www.altium.com/documentation/altium-designer/scripting | Official scripting documentation |
| https://www.altium.com/documentation/altium-designer/scripting/examples-reference | Official script examples |
| https://www.altium.com/documentation/altium-designer/components-libraries/file-based-libraries/schematic | File-based schematic libraries |

Things to reuse as ideas: selecting existing destination libraries, creating library components, linking footprints, adding parameters, and handling scripting limitations. Code SHALL NOT be copied unless its licence permits it. Findings are recorded in `architecture.md` §3.

## 15. Reliability and safety

| ID | Requirement |
|---|---|
| R-1 | Existing components SHALL NEVER be deleted without confirmation. |
| R-2 | Symbols or footprints SHALL NEVER be replaced silently. |
| R-3 | Destination libraries SHALL be backed up before modification. |
| R-4 | Operations SHOULD be transactional, with recovery after partial failure. |
| R-5 | Failed imports SHALL be reported clearly. |
| R-6 | Other components in destination libraries SHALL be preserved. |
| R-7 | Downloaded file types SHALL be validated. |
| R-8 | Archive extraction SHALL be protected against path traversal and zip bombs. |
| R-9 | Provider credentials SHALL be stored securely (encrypted per Windows user) and never in source code. |
| R-10 | Private Altium project files SHALL NOT be sent to third parties without authorisation. |
| R-11 | Testing SHALL use temporary libraries before production libraries are touched. |
| R-12 | Project data SHALL be preserved if the application crashes. |
| R-13 | Compatibility SHALL be verified against the installed Altium version (AD26 26.10.1.5). |

## 16. Development milestones

Details and acceptance criteria are in `roadmap.md`.

1. **M1 Search application** — MPN input, at least one legitimate provider, fallback links for the rest. No import.
2. **M2 Download manager** — verified automatic download from at least one source, plus manual import from disk.
3. **M3 Altium integration** — proof-of-concept import of one symbol and footprint into temporary libraries.
4. **M4 Parameters and linking** — footprint linkage and the `LCSC Part #` parameter.
5. **M5 Full workflow** — search, download, preview, verify and import in one UI.
6. **M6 Reliability** — tests, error handling, backups, duplicate detection, import log and documentation.

Each milestone SHALL end with: what was implemented, how to run it, what to test, what works, what remains incomplete, and known risks and limitations.

## 17. Deliverables

- Project files: `requirement.md`, `architecture.md`, `roadmap.md`, `progress.md`, `README.md`.
- Source code modules: search providers, downloads, Altium integration, component data and parameter mapping, local database, user interface, validation and logging.
- Automated tests added with each module.

## 18. Working constraints

- Development usage is limited. Favour working functionality, efficient development and minimal dependencies.
- Do not repeatedly rewrite the architecture.
- Do not generate large amounts of untested DelphiScript. Add Altium script code in small pieces that have been tested on AD26.
- Do not claim an API exists unless it has been verified.
