# Altium Library Manager — Roadmap

Each milestone can be tested on its own and ends with the report defined in `requirement.md` §16.

## M1 — Search application (current)
**Scope:** Tkinter UI, MPN search, Mouser Search API (official), optional Nexar/Octopart, manual-assist links for all other sources, exact-match ranking and recommendation, an "already in local DB" check, destination SchLib/PcbLib selection remembered between sessions, encrypted API key storage, logging.
**Out of scope:** downloading and importing.
**Acceptance:**
- [ ] Searching `STM32F103C8T6` with a Mouser key shows an exact-match row with the manufacturer and a datasheet link.
- [ ] Searching a nonsense MPN shows "No exact match".
- [ ] Without any keys, every provider is listed as *not configured* or *manual*, and nothing claims to have been searched.
- [ ] Manual-assist buttons open the correct search page for the MPN (including MPNs containing `/`).
- [ ] The library selections persist after restart.
- [ ] `python -m unittest` passes offline.

## M2 — Download manager
- Cache layout `cache/<MPN>/<source>/<timestamp>/` with `provenance.json` (URL, date, SHA-256).
- "Import package from disk": a zip or folder containing `.SchLib`/`.PcbLib`/`.IntLib`/`.step`. Safe extraction and file-type validation (OLE header for Altium libraries, ISO-10303 header for STEP).
- Inspect the package: list the files, flag what is missing (symbol, footprint, 3D).
- Automatic download for at least one source that has a legitimate download API. Candidates: LCSC Open API (if approved) or the opt-in unofficial EasyEDA provider; decide with the user.

## M3 — Altium integration proof of concept
- First test whether `X2.EXE -RScriptingSystem:RunScript` works on AD26.
- Write a minimal DelphiScript that copies one named component from a source SchLib into a **temporary** destination SchLib and saves it. Then do the same for a PcbLib.
- Job/result JSON bridge, backup before running, skip-if-exists.

## M4 — Parameters and linking
- Parameter mapping table (JSON, user-editable). Set the standard parameters, including `LCSC Part #` (blank unless verified).
- Add the footprint model link (name + library) to the symbol.
- Pin-to-pad check: compare symbol pin designators with footprint pad designators, report EP/thermal pads, and mark the result "needs manual review" when they differ.
- STEP: confirm whether the footprint already embeds a 3D body; otherwise attach the STEP from the cache.

## M5 — Full workflow
- One window: search → pick result → download or import from disk → preview/verification panel → Import → report. Duplicate dialog (Skip / Inspect / New name / Replace with confirmation).

## M6 — Reliability
- Expanded tests, recovery from partial failure (restore from backup), duplicate detection across libraries, an import log viewer, and user documentation.

## Future (not scheduled)
Listed in `requirement.md` §13.
