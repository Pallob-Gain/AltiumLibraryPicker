"""Tkinter user interface.

Workflow: paste MPN -> Search -> open library sites -> download manually ->
select the downloaded library -> select destination SchLib/PcbLib -> Import into Altium.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..altium import bridge
from ..config import Settings, app_dir
from ..credentials import CredentialStore
from ..db import Database
from ..importer import ImportRequest, record_import, run_import, validate
from ..models import ProviderState
from ..providers.links import ALTIUM_MPS_HELP, MANUAL_SOURCES
from ..providers.mouser import CRED_NAME as MOUSER_CRED
from ..search import SearchOutcome, run_search
from ..cse import CSE_HOME, DownloadWatcher, claim, default_downloads_dir, human_size
from ..cse_client import (CRED_EMAIL, CRED_PASSWORD, CseAuthError, CseClient, CseError,
                          exact_matches, parse_epw, read_epw)
from ..snapeda_client import (CRED_PASSWORD as SNAP_PASSWORD, CRED_SESSION as SNAP_SESSION,
                              CRED_USER as SNAP_USER,
                              SNAPEDA_HOME, SnapAuthError, SnapClient, SnapError)
from ..snapeda_client import exact_matches as snap_exact_matches
from ..easyeda.fetch import UNOFFICIAL_NOTE, clean_lcsc, fetch_and_build
from ..schlib_reader import best_match, list_symbols
from ..sources import resolve_source
from ..validation import check_library

ALL_COMPONENTS = "(all components)"
# browser sites whose ECAD download is picked up from the Downloads folder:
# key -> (button text, what to click on the site)
WATCH_SITES = {
    "cse": ("From Component Search Engine (CSE)...",
            "Open the part and click its download button (ECAD model)."),
    "mouser_web": ("From Mouser (ECAD model)...",
                   "Open the part and click the ECAD Model icon (SamacSys symbol, footprint, 3D) "
                   "- sign in with your Component Search Engine account if Mouser asks."),
}

log = logging.getLogger(__name__)

RESULT_COLUMNS = [  # id, heading, width
    ("match", "Match", 135), ("mpn", "MPN", 170), ("mfr", "Manufacturer", 150),
    ("source", "Source", 70), ("symbol", "Symbol", 55), ("footprint", "Footprint", 65),
    ("model3d", "3D", 40), ("datasheet", "Datasheet", 65), ("supplier_pn", "Supplier P/N", 130),
]
PARAM_FIELDS = ["Manufacturer", "Manufacturer Part Number", "Description", "Datasheet", "LCSC Part #"]
LINK_MODES = {"Library file name (recommended)": "name", "Full library path": "path",
              "Any library": "any"}
DOWNLOAD_TYPES = [("Altium libraries / packages", "*.SchLib *.PcbLib *.IntLib *.LibPkg *.zip"),
                  ("All files", "*.*")]


class App:
    def __init__(self, root: tk.Tk, settings: Settings, creds: CredentialStore, db: Database,
                 providers: list):
        self.root, self.settings, self.creds, self.db, self.providers = (
            root, settings, creds, db, providers)
        self.outcome: SearchOutcome | None = None
        self.work_dir = app_dir()
        self._watcher: DownloadWatcher | None = None
        self._auto_import_next = False
        self._progress_text = ""
        self._step_file = ""
        self._step_place = None        # StepPlacement for _step_file (from CSE), None = unknown
        # CSE login: restored from the encrypted store ("Remember login"), or session-only
        self._cse_login = (creds.get(CRED_EMAIL), creds.get(CRED_PASSWORD))
        self._snap_login = (creds.get(SNAP_USER), creds.get(SNAP_PASSWORD))
        self._snap: SnapClient | None = None     # signed-in SnapEDA session (memory only)
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._build()

    # ================= layout =================
    def _build(self) -> None:
        r = self.root
        r.title(f"Altium Library Manager {__version__}")
        # fit small / scaled laptop screens (taskbar + title bar need ~90 px)
        w = min(1180, r.winfo_screenwidth() - 40)
        h = min(900, r.winfo_screenheight() - 90)
        r.geometry(f"{w}x{h}+{max(0, (r.winfo_screenwidth() - w) // 2)}+10")
        r.minsize(min(980, w), min(700, h))

        menubar = tk.Menu(r)
        m = tk.Menu(menubar, tearoff=False)
        m.add_command(label="API keys...", command=self._api_keys_dialog)
        m.add_command(label="Component Search Engine...", command=self._cse_dialog)
        m.add_command(label="SnapEDA...", command=self._snap_dialog)
        m.add_command(label="Open data folder (backups, logs)",
                      command=lambda: os.startfile(self.work_dir))
        menubar.add_cascade(label="Settings", menu=m)
        r.config(menu=menubar)

        # ---- 1. Search ----
        top = ttk.LabelFrame(r, text="1. Part number", padding=6)
        top.pack(fill="x", padx=8, pady=(8, 4))
        self.mpn_var = tk.StringVar(value=self.settings.get("last_mpn", ""))
        entry = ttk.Entry(top, textvariable=self.mpn_var, width=40, font=("Segoe UI", 11))
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self.on_search())
        entry.focus_set()
        self.search_btn = ttk.Button(top, text="Search", command=self.on_search)
        self.search_btn.pack(side="left", padx=6)
        self.search_info = ttk.Label(top, text="", foreground="#555")
        self.search_info.pack(side="left", padx=6)

        # ---- 2. Results + library sites ----
        mid = ttk.Frame(r)
        # packed at the end of _build (bottom-up) so a short window shrinks the results table
        res = ttk.LabelFrame(mid, text="2. Search results (double-click opens the source page)",
                             padding=6)
        res.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(res, columns=[c[0] for c in RESULT_COLUMNS], show="headings",
                                 height=4, selectmode="browse")
        for cid, head, width in RESULT_COLUMNS:
            self.tree.heading(cid, text=head)
            self.tree.column(cid, width=width, stretch=cid in ("mfr", "mpn"))
        self.tree.tag_configure("exact", background="#e3f6e3")
        self.tree.tag_configure("similar", background="#fff1d6")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._open_selected("source_url"))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._use_selected_result())
        rb = ttk.Frame(res)
        rb.pack(fill="x", pady=(4, 0))
        ttk.Button(rb, text="Open source page",
                   command=lambda: self._open_selected("source_url")).pack(side="left")
        ttk.Button(rb, text="Open datasheet",
                   command=lambda: self._open_selected("datasheet_url")).pack(side="left", padx=4)

        sites = ttk.LabelFrame(mid, text="Find a library (opens the site's search page)", padding=6)
        sites.pack(side="left", fill="y", padx=(8, 0))
        # two columns so it fits short screens
        for i, src in enumerate(MANUAL_SOURCES):
            label = "CSE (SamacSys)" if src.key == "cse" else src.name
            ttk.Button(sites, text=label, width=17,
                       command=lambda s=src: self._open_site(s)).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=1, pady=1)
        last = (len(MANUAL_SOURCES) + 1) // 2
        ttk.Button(sites, text="Altium Manufacturer Part Search",
                   command=self._altium_mps).grid(row=last, column=0, columnspan=2,
                                                  sticky="ew", padx=1, pady=1)
        ttk.Button(sites, text="Open ALL sites", command=self._open_all_sites).grid(
            row=last + 1, column=0, columnspan=2, sticky="ew", padx=1, pady=(6, 0))

        # ---- 3. Import ----
        imp = ttk.LabelFrame(r, text="3. Import into Altium", padding=6)
        # (packed at the end of _build)
        imp.columnconfigure(1, weight=1)
        ttk.Label(imp, text="Downloaded library:").grid(row=0, column=0, sticky="w")
        bf = ttk.Frame(imp)
        bf.grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Button(bf, text="Select file (SchLib / PcbLib / IntLib / LibPkg / zip)...",
                   command=self._pick_download_file).pack(side="left")
        ttk.Button(bf, text="Select folder...", command=self._pick_download_folder).pack(
            side="left", padx=4)

        ttk.Label(imp, text="Get it automatically:").grid(row=1, column=0, sticky="w")
        af = ttk.Frame(imp)
        af.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 4))
        self.cse_direct_btn = ttk.Button(af, text="Get from CSE (direct)",
                                         command=self._cse_direct)
        self.cse_direct_btn.pack(side="left", padx=(0, 4))
        ttk.Button(af, text="Get from SnapEDA (direct)", command=self._snap_direct).pack(
            side="left", padx=(0, 4))
        self.mouser_btn = ttk.Button(af, text=WATCH_SITES["mouser_web"][0],
                                     command=lambda: self._cse_toggle("mouser_web"))
        self.mouser_btn.pack(side="left")
        self.cse_btn = ttk.Button(af, text=WATCH_SITES["cse"][0],
                                  command=lambda: self._cse_toggle("cse"))
        self.cse_btn.pack(side="left", padx=(4, 2))
        self._site_btns = {"cse": self.cse_btn, "mouser_web": self.mouser_btn}
        self.cse_login_var = tk.StringVar()
        ttk.Label(af, textvariable=self.cse_login_var, foreground="#555").pack(side="left",
                                                                                padx=(0, 6))
        ttk.Button(af, text="From SamacSys library...", command=self._from_samacsys).pack(
            side="left")
        ttk.Label(af, text="    EasyEDA by LCSC #:").pack(side="left")
        self.lcsc_var = tk.StringVar()
        lcsc_entry = ttk.Entry(af, textvariable=self.lcsc_var, width=10)
        lcsc_entry.pack(side="left", padx=4)
        lcsc_entry.bind("<Return>", lambda e: self._fetch_lcsc())
        self.lcsc_btn = ttk.Button(af, text="Fetch", command=self._fetch_lcsc)
        self.lcsc_btn.pack(side="left")

        self.src_sch = tk.StringVar()
        self.src_pcb = tk.StringVar()
        self.dst_sch = tk.StringVar(value=self.settings.get("schlib", ""))
        self.dst_pcb = tk.StringVar(value=self.settings.get("pcblib", ""))
        self.comp_var = tk.StringVar()
        rows = [(2, "Source SchLib:", self.src_sch, ".SchLib", None),
                (3, "Source PcbLib:", self.src_pcb, ".PcbLib", None),
                (5, "Destination SchLib:", self.dst_sch, ".SchLib", "schlib"),
                (6, "Destination PcbLib:", self.dst_pcb, ".PcbLib", "pcblib")]
        for i, label, var, ext, key in rows:
            ttk.Label(imp, text=label).grid(row=i, column=0, sticky="w")
            ttk.Entry(imp, textvariable=var, state="readonly").grid(row=i, column=1, sticky="ew",
                                                                   padx=4, pady=1)
            ttk.Button(imp, text="Select...",
                       command=lambda v=var, x=ext, k=key: self._pick_lib(v, x, k)).grid(
                row=i, column=2)
        ttk.Label(imp, text="Component to import:").grid(row=4, column=0, sticky="w")
        self.comp_box = ttk.Combobox(imp, textvariable=self.comp_var, state="readonly")
        self.comp_box.grid(row=4, column=1, sticky="ew", padx=4, pady=1)
        self.src_sch.trace_add("write", lambda *a: self._refresh_components())

        pf = ttk.LabelFrame(imp, text="Component parameters (blank = keep library value; "
                                      "'LCSC Part #' is always created)", padding=4)
        pf.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.param_vars: dict[str, tk.StringVar] = {}
        for i, name in enumerate(PARAM_FIELDS):
            var = tk.StringVar()
            self.param_vars[name] = var
            ttk.Label(pf, text=name + ":").grid(row=i // 2, column=(i % 2) * 2, sticky="w",
                                                padx=(0 if i % 2 == 0 else 12, 2))
            ttk.Entry(pf, textvariable=var, width=52).grid(row=i // 2, column=(i % 2) * 2 + 1,
                                                          sticky="ew", pady=1)
        pf.columnconfigure(1, weight=1)
        pf.columnconfigure(3, weight=1)

        of = ttk.Frame(imp)
        of.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(of, text="If the name already exists:").pack(side="left")
        self.conflict_var = tk.StringVar(value=self.settings.get("on_conflict", "skip"))
        ttk.Radiobutton(of, text="Skip (never overwrite)", value="skip",
                        variable=self.conflict_var).pack(side="left", padx=4)
        ttk.Radiobutton(of, text="Import with new name (_1)", value="rename",
                        variable=self.conflict_var).pack(side="left", padx=4)
        ttk.Label(of, text="   Footprint link:").pack(side="left")
        current = self.settings.get("link_mode", "name")
        self.link_var = tk.StringVar(value=next(
            (k for k, v in LINK_MODES.items() if v == current), list(LINK_MODES)[0]))
        ttk.Combobox(of, textvariable=self.link_var, values=list(LINK_MODES), state="readonly",
                     width=30).pack(side="left", padx=4)
        self.import_btn = ttk.Button(of, text="Import into Altium", command=self.on_import)
        self.import_btn.pack(side="right")

        # ---- Messages ----
        msg = ttk.LabelFrame(r, text="Messages", padding=4)
        # (packed at the end of _build)
        self.info = tk.Text(msg, height=9, wrap="word", font=("Segoe UI", 9))
        sb = ttk.Scrollbar(msg, orient="vertical", command=self.info.yview)
        self.info.configure(yscrollcommand=sb.set)
        self.info.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.info.tag_configure("bold", font=("Segoe UI", 9, "bold"))
        self.info.tag_configure("warn", foreground="#a05a00")
        self.info.tag_configure("err", foreground="#b00020")
        self.info.tag_configure("ok", foreground="#1b7a1b")
        self._set_info([("Paste an MPN and press Search, or go straight to step 3 with a library "
                         "you already downloaded.", "")])

        self._cse_update_login_label()
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(r, textvariable=self.status_var, relief="sunken", anchor="w",
                  padding=(6, 2)).pack(fill="x", side="bottom")
        msg.pack(side="bottom", fill="both", padx=8, pady=(0, 4))
        imp.pack(side="bottom", fill="x", padx=8, pady=4)
        mid.pack(fill="both", expand=True, padx=8)

    # ================= background tasks =================
    def _run_bg(self, label: str, func, on_done) -> None:
        if self._busy:
            self.status_var.set("Busy - please wait for the current task to finish.")
            return
        self._busy = True
        for b in (self.search_btn, self.import_btn, self.lcsc_btn, self.cse_direct_btn):
            b.state(["disabled"])
        self.status_var.set(label)

        def worker():
            try:
                self._queue.put((func(), None))
            except Exception as e:
                log.exception("Background task failed")
                self._queue.put((None, e))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(200, lambda: self._poll(on_done))

    def _poll(self, on_done) -> None:
        if self._progress_text:                       # set by worker threads (plain str)
            self.status_var.set(self._progress_text)
        try:
            value, err = self._queue.get_nowait()
        except queue.Empty:
            self.root.after(200, lambda: self._poll(on_done))
            return
        self._busy = False
        self._progress_text = ""
        for b in (self.search_btn, self.import_btn, self.lcsc_btn, self.cse_direct_btn):
            b.state(["!disabled"])
        if err is not None:
            self._auto_import_next = False
            if isinstance(err, CseAuthError):
                self._cse_update_login_label(rejected=True)
            self.status_var.set(f"Failed: {err}")
            self._set_info([(f"Failed: {err}", "err")])
            return
        on_done(value)

    # ================= search =================
    def on_search(self) -> None:
        mpn = self.mpn_var.get().strip()
        if not mpn:
            self.status_var.set("Enter a manufacturer part number.")
            return
        self.settings.set("last_mpn", mpn)
        self._run_bg(f"Searching '{mpn}'...",
                     lambda: run_search(mpn, self.providers, MANUAL_SOURCES), self._show_outcome)

    def _show_outcome(self, o: SearchOutcome) -> None:
        self.outcome = o
        self.tree.delete(*self.tree.get_children())
        for i, res in enumerate(o.results):
            self.tree.insert("", "end", iid=str(i),
                             tags=("exact" if res.match.name == "EXACT" else "similar",),
                             values=(res.match.value, res.mpn, res.manufacturer, res.source,
                                     res.symbol.value, res.footprint.value, res.model3d.value,
                                     "Yes" if res.datasheet_url else "-", res.supplier_pn))
        api = [s for s in o.statuses if s.state is not ProviderState.MANUAL]
        self.search_info.configure(text="  ".join(
            f"{s.provider}: {s.state.value}" + (f" ({s.count})" if s.state is ProviderState.OK
                                                else (f" - {s.message}" if s.message else ""))
            for s in api) + "   | Library sites: not searched, use the buttons")
        self._fill_params(o)
        lines = [(o.recommendation + "\n", "bold")]
        try:
            for c in self.db.find_component(o.query):
                lines.append((f"ALREADY IMPORTED: {c['mpn']} -> {c['dest_schlib']} / "
                               f"{c['dest_pcblib']} (footprint {c['footprint_name']}, "
                               f"{c['import_date']}, {c['verification']})\n", "warn"))
            self.db.log_search(o.query, {s.provider: s.state.value for s in o.statuses},
                               o.exact_count)
        except Exception:
            log.exception("Database access failed")
        for w in o.warnings:
            lines.append(("Warning: " + w + "\n", "warn"))
        self._set_info(lines)
        self.status_var.set(f"{len(o.results)} result(s), {o.exact_count} exact.")

    def _fill_params(self, o: SearchOutcome) -> None:
        best = o.best
        values = {"Manufacturer": best.manufacturer if best else "",
                  "Manufacturer Part Number": best.mpn if best else o.query,
                  "Description": best.description if best else "",
                  "Datasheet": best.datasheet_url if best else "",
                  "LCSC Part #": best.lcsc if best else ""}   # lcsc only if verified (never guessed)
        for k, v in values.items():
            self.param_vars[k].set(v)

    def _use_selected_result(self) -> None:
        res = self._selected_result()
        if not res:
            return
        if res.match.name != "EXACT":
            self.status_var.set("Selected row is NOT an exact match - parameters not changed.")
            return
        self.param_vars["Manufacturer"].set(res.manufacturer)
        self.param_vars["Manufacturer Part Number"].set(res.mpn)
        self.param_vars["Description"].set(res.description)
        self.param_vars["Datasheet"].set(res.datasheet_url)
        self.status_var.set(f"Parameters filled from {res.source} result.")

    # ================= sites =================
    def _current_mpn(self) -> str | None:
        mpn = self.mpn_var.get().strip()
        if not mpn:
            self.status_var.set("Enter an MPN first.")
            return None
        return mpn

    def _open_site(self, src) -> None:
        mpn = self._current_mpn()
        if mpn:
            webbrowser.open(src.url(mpn))

    def _open_all_sites(self) -> None:
        mpn = self._current_mpn()
        if mpn:
            for src in MANUAL_SOURCES:
                webbrowser.open(src.url(mpn))

    def _altium_mps(self) -> None:
        mpn = self._current_mpn()
        if mpn:
            self.root.clipboard_clear()
            self.root.clipboard_append(mpn)
            messagebox.showinfo("Altium Manufacturer Part Search", ALTIUM_MPS_HELP, parent=self.root)

    def _selected_result(self):
        sel = self.tree.selection()
        if not sel or not self.outcome:
            return None
        return self.outcome.results[int(sel[0])]

    def _open_selected(self, attr: str) -> None:
        res = self._selected_result()
        url = getattr(res, attr, "") if res else ""
        if url.startswith(("https://", "http://")):
            webbrowser.open(url)
        else:
            self.status_var.set("No link available for the selected row.")

    # ================= source / destination selection =================
    def _pick_download_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Select the downloaded library",
                                          initialdir=self.settings.get("last_download_dir") or None,
                                          filetypes=DOWNLOAD_TYPES)
        if path:
            self._load_download(Path(path))

    def _pick_download_folder(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Select the downloaded folder",
                                       initialdir=self.settings.get("last_download_dir") or None)
        if path:
            self._load_download(Path(path))

    def _load_download(self, path: Path) -> None:
        self.settings.set("last_download_dir", str(path if path.is_dir() else path.parent))
        try:
            pkg = resolve_source(path, self.work_dir / "extracted")
        except Exception as e:
            log.exception("Resolving source failed")
            self._auto_import_next = False
            messagebox.showerror("Cannot read download", str(e), parent=self.root)
            return
        if pkg.intlib and not (pkg.schlib and pkg.pcblib):
            self._extract_intlib(pkg.intlib)
            return
        self._apply_package(pkg)

    def _apply_package(self, pkg) -> None:
        self.src_sch.set(str(pkg.schlib) if pkg.schlib else "")
        self.src_pcb.set(str(pkg.pcblib) if pkg.pcblib else "")
        # one STEP in the package (SamacSys/Mouser ship it separately): attached to imported
        # footprints that have no 3D body of their own
        self._step_file = str(pkg.step_files[0]) if len(pkg.step_files) == 1 else ""
        self._step_place = None
        lines = [("Downloaded library loaded:\n", "bold"),
                 (f"  Symbol library:    {pkg.schlib or 'NOT FOUND'}\n", "" if pkg.schlib else "err"),
                 (f"  Footprint library: {pkg.pcblib or 'NOT FOUND'}\n", "" if pkg.pcblib else "err")]
        if pkg.step_files:
            lines.append((f"  STEP files in package: {', '.join(p.name for p in pkg.step_files)}"
                          + (" - attached to the footprint if it has no 3D body of its own\n"
                             if self._step_file else
                             " - several, so none is attached automatically\n"), ""))
        for n in pkg.notes:
            lines.append(("  Note: " + n + "\n", "warn"))
        part_id = _package_part_id(pkg) if self._step_file else None
        if part_id and all(self._cse_login):
            # SamacSys/CSE (also via Mouser): the STEP needs CSE's rotation/offset - not in the zip
            email, password = self._cse_login

            def task():
                try:
                    return CseClient(email, password).placement(part_id), ""
                except CseError as e:            # not fatal: the part is still imported
                    return None, str(e)

            def done(result):
                place, err = result
                self._step_place = place
                if place is not None:
                    lines.append(("  3D placement from Component Search Engine: "
                                  f"{place.describe()}\n", "ok"))
                else:
                    lines.append(("  Could not get the 3D placement from Component Search "
                                  f"Engine{': ' + err if err else ''}. The model will be placed "
                                  "at the footprint origin, unrotated - check the 3D view.\n",
                                  "warn"))
                self._set_info(lines)
                self._continue_auto_import(pkg)

            self._set_info(lines)
            self._run_bg("Getting the 3D model placement from Component Search Engine...",
                         task, done)
            return
        if part_id:
            lines.append(("  Sign in under Settings > Component Search Engine so the app can get "
                          "this part's exact 3D placement. Without it the model is placed at the "
                          "footprint origin, unrotated - check the 3D view.\n", "warn"))
        self._set_info(lines)
        self._continue_auto_import(pkg)

    def _continue_auto_import(self, pkg) -> None:
        if self._auto_import_next:
            self._auto_import_next = False
            if pkg.schlib and pkg.pcblib:
                self.root.after(100, lambda: self.on_import(auto=True))
            else:
                self.status_var.set("Automatic import skipped: the download has no "
                                    "SchLib/PcbLib (see Messages).")

    def _extract_intlib(self, intlib: Path) -> None:
        x2 = self._x2()
        if not x2:
            return
        out = bridge.new_job_dir(self.work_dir / "extracted")

        def task():
            rep = bridge.extract_intlib(x2, intlib, out, self.work_dir)
            if not rep.ok:
                raise RuntimeError("IntLib extraction failed: " + "; ".join(rep.errors))
            return resolve_source(out, self.work_dir / "extracted")

        self._run_bg("Extracting IntLib sources in Altium...", task, self._apply_package)

    def _pick_lib(self, var: tk.StringVar, ext: str, key: str | None) -> None:
        initial = os.path.dirname(var.get()) if var.get() else (
            self.settings.get("last_download_dir") if key is None else None)
        path = filedialog.askopenfilename(
            parent=self.root, title=f"Select {ext}", initialdir=initial or None,
            filetypes=[(f"Altium library (*{ext})", f"*{ext}"), ("All files", "*.*")])
        if not path:
            return
        path = os.path.normpath(path)
        err = check_library(path, ext)
        if err:
            messagebox.showerror("Invalid library", err, parent=self.root)
            return
        var.set(path)
        if key:
            self.settings.set(key, path)
        else:
            self._step_file = ""          # a hand-picked source: no package STEP
            self._step_place = None

    # ================= component choice (multi-part source libraries) =================
    def _refresh_components(self, prefer: str = "") -> None:
        names: list[str] = []
        path = self.src_sch.get()
        if path and os.path.isfile(path):
            try:
                names = [s.name for s in list_symbols(path)]
            except Exception:
                log.exception("Listing symbols failed")
        self.comp_box["values"] = names + ([ALL_COMPONENTS] if len(names) > 1 else [])
        pick = prefer if prefer in names else best_match(
            names, self.param_vars["Manufacturer Part Number"].get(), self.mpn_var.get(),
            self.param_vars["LCSC Part #"].get(), self.lcsc_var.get())
        if not pick and len(names) == 1:
            pick = names[0]
        self.comp_var.set(pick)
        if len(names) > 1 and not pick:
            self.status_var.set(f"The source library has {len(names)} symbols - choose the "
                                "component to import.")

    # ================= Component Search Engine (browser workflow, see alm/cse.py) =========
    def _cse_folder(self) -> Path:
        return Path(self.settings.get("cse_watch_dir") or default_downloads_dir())

    def _cse_toggle(self, site: str = "cse") -> None:
        if self._watcher:
            self._cse_stop("Stopped waiting for the download.")
            return
        mpn = self._current_mpn()
        if not mpn:
            return
        folder = self._cse_folder()
        if not folder.is_dir():
            messagebox.showerror("Component Search Engine", f"Download folder not found:\n{folder}"
                                 "\n\nSet it under Settings > Component Search Engine.",
                                 parent=self.root)
            return
        auto = bool(self.settings.get("cse_auto_import", True))
        if auto:
            if check_library(self.dst_sch.get(), ".SchLib") or \
                    check_library(self.dst_pcb.get(), ".PcbLib"):
                messagebox.showerror("Component Search Engine", "Select the destination SchLib and "
                                     "PcbLib first - the download is imported into them "
                                     "automatically.", parent=self.root)
                return
        src = next(s for s in MANUAL_SOURCES if s.key == site)
        webbrowser.open(src.url(mpn))
        self._watcher = DownloadWatcher(folder, mpn)
        for key, btn in self._site_btns.items():
            if key == site:
                btn.configure(text="Stop waiting for the download")
            else:
                btn.state(["disabled"])
        signed_in = all(self._cse_login)
        steps = [(f"{src.name} - opened in your browser for {mpn}\n", "bold"),
                 (f"  1. {WATCH_SITES[site][1]}\n", ""),
                 (f"  2. This app watches {folder}. A full library zip is imported directly; "
                  "a Library Loader part file (.epw) makes the app download the model itself "
                  + ("with your saved CSE login.\n" if signed_in else
                     "- for that, sign in under Settings > Component Search Engine.\n"),
                  "" if signed_in else "warn")]
        if auto:
            steps.append((f"  It is then imported automatically into:\n    {self.dst_sch.get()}\n"
                          f"    {self.dst_pcb.get()}\n  (backed up first, never overwrites "
                          "existing parts).\n", ""))
        self._set_info(steps)
        self._cse_poll()

    def _cse_stop(self, status: str) -> None:
        self._watcher = None
        for key, btn in self._site_btns.items():
            btn.configure(text=WATCH_SITES[key][0])
            btn.state(["!disabled"])
        self.status_var.set(status)

    def _cse_poll(self) -> None:
        w = self._watcher
        if not w:
            return
        try:
            st = w.poll()
        except Exception as e:
            log.exception("Download watcher failed")
            self._cse_stop(f"Watching the download folder failed: {e}")
            return
        if st.state == "waiting":
            self.status_var.set(st.message + " ...")
        elif st.state == "downloading":
            self.status_var.set(f"{st.message}: {human_size(st.size)}")
        elif st.state == "timeout":
            self._cse_stop("Component Search Engine: " + st.message + " Click the button to "
                           "try again.")
            return
        elif st.state == "ready":
            self._cse_stop(f"Download complete: {st.path.name} ({human_size(st.size)})")
            self._cse_received(st)
            return
        self.root.after(1000, self._cse_poll)

    def _cse_received(self, st) -> None:
        try:
            copy = claim(st.path, self.work_dir)
        except OSError as e:
            messagebox.showerror("Component Search Engine",
                                 f"Could not read {st.path}:\n{e}\n\nIf the SamacSys Library Loader "
                                 "is running it may have moved the file - use 'From SamacSys "
                                 "library...' instead.", parent=self.root)
            return
        try:
            epw = read_epw(copy)
        except (CseError, OSError) as e:
            messagebox.showerror("Component Search Engine", str(e), parent=self.root)
            return
        if epw:
            self._cse_fetch(epw)
            return
        if not st.matches_mpn:
            self.status_var.set(f"Note: {st.path.name} does not contain the MPN in its name - "
                                "check the component.")
        self._auto_import_next = bool(self.settings.get("cse_auto_import", True))
        self._load_download(copy)

    # ---- CSE direct: search by MPN -> download -> import (no browser) ----
    def _cse_direct(self) -> None:
        mpn = self._current_mpn()
        if not mpn:
            return
        if check_library(self.dst_sch.get(), ".SchLib") or \
                check_library(self.dst_pcb.get(), ".PcbLib"):
            messagebox.showerror("Component Search Engine", "Select the destination SchLib and "
                                 "PcbLib first - the part is imported into them.", parent=self.root)
            return
        email, password = self._cse_login
        if not (email and password):
            messagebox.showinfo("Component Search Engine", "Sign in with your free Component "
                                "Search Engine account first (Settings > Component Search "
                                "Engine).", parent=self.root)
            self._cse_dialog()
            return
        self._run_bg(f"Searching Component Search Engine for {mpn}...",
                     lambda: (mpn, CseClient(email, password).search(mpn)), self._cse_direct_found)

    def _cse_direct_found(self, result) -> None:
        mpn, parts = result
        self._cse_update_login_label(verified=True)
        exact = exact_matches(parts, mpn)
        if not exact:
            near = [f"{p.mpn} ({p.manufacturer})" for p in parts[:8]]
            self._set_info(
                [(f"Component Search Engine has no exact {mpn} with a symbol and footprint.\n",
                  "warn")]
                + ([("  Similar, NOT the same part: " + ", ".join(near) + "\n", "")] if near else [])
                + [("  Try: From Mouser (ECAD model), Ultra Librarian / SnapMagic (Find a library "
                    "buttons), EasyEDA by LCSC #, or the Celestial Altium Library (see README).\n",
                    "")])
            self.status_var.set(f"No exact CSE model for {mpn}.")
            return
        want_mfr = self.param_vars["Manufacturer"].get().strip().lower()
        same_mfr = [p for p in exact if want_mfr and p.manufacturer.lower() == want_mfr]
        if len(exact) == 1:
            self._cse_direct_download(exact[0])
        elif len(same_mfr) == 1:
            self._cse_direct_download(same_mfr[0])
        else:
            self._cse_choose(exact)

    def _cse_choose(self, parts, pick=None, site: str = "Component Search Engine") -> None:
        pick = pick or self._cse_direct_download
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Choose the part - {site}")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, padding=8, text=f"{parts[0].mpn} exists under several manufacturers on "
                  f"{site}. Choose the right one:").pack(anchor="w")
        cols = (("mfr", "Manufacturer", 160), ("desc", "Description", 330), ("pkg", "Package", 150),
                ("3d", "3D", 40), ("conf", "Checked", 150))
        tree = ttk.Treeview(dlg, columns=[c[0] for c in cols], show="headings", height=6,
                            selectmode="browse")
        for cid, head, w in cols:
            tree.heading(cid, text=head)
            tree.column(cid, width=w, stretch=cid == "desc")
        for i, p in enumerate(parts):
            tree.insert("", "end", iid=str(i), values=(p.manufacturer, p.description, p.package,
                                                       "Yes" if p.has_3d else "-",
                                                       getattr(p, "confidence", "")))
        tree.pack(fill="both", expand=True, padx=8)
        tree.selection_set("0")

        def ok():
            sel = tree.selection()
            if sel:
                dlg.destroy()
                pick(parts[int(sel[0])])
        tree.bind("<Double-1>", lambda e: ok())
        bb = ttk.Frame(dlg, padding=8)
        bb.pack(fill="x")
        ttk.Button(bb, text="Download and import", command=ok).pack(side="right")
        ttk.Button(bb, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

    def _cse_direct_download(self, part) -> None:
        email, password = self._cse_login
        for key, val in (("Manufacturer", part.manufacturer), ("Manufacturer Part Number", part.mpn),
                         ("Description", part.description), ("Datasheet", part.datasheet)):
            if val and not self.param_vars[key].get().strip():
                self.param_vars[key].set(val)
        out = self.work_dir / "downloads" / f"cse-{part.part_id}-{os.getpid()}-{id(part)}"
        label = f"{part.mpn} ({part.manufacturer})"

        def progress(done, total):
            self._progress_text = (f"Downloading {label} from Component Search Engine: "
                                   f"{human_size(done)}" + (f" of {human_size(total)}" if total
                                                            else ""))

        def done(path):
            self._auto_import_next = True        # the user asked for this part: import it
            self._load_download(path)

        self._run_bg(f"Downloading {label} from Component Search Engine...",
                     lambda: CseClient(email, password).download(part.part_id, out, progress),
                     done)

    def _cse_fetch(self, epw) -> None:
        """Download the model for a CSE part file with the user's CSE login, then import."""
        email, password = self._cse_login
        if not (email and password):
            messagebox.showwarning(
                "Component Search Engine", f"Got the part file for {epw.mpn or epw.part_id}. "
                "To download the model, sign in under Settings > Component Search Engine, then "
                "click the download button on CSE again.", parent=self.root)
            self._cse_dialog()
            return
        for key, val in (("Manufacturer Part Number", epw.mpn), ("Manufacturer", epw.manufacturer)):
            if val and not self.param_vars[key].get().strip():
                self.param_vars[key].set(val)
        out = self.work_dir / "downloads" / f"cse-{epw.part_id}-{os.getpid()}-{id(epw)}"

        def progress(done, total):
            self._progress_text = (f"Downloading {epw.mpn or epw.part_id} from Component Search "
                                   f"Engine: {human_size(done)}"
                                   + (f" of {human_size(total)}" if total else ""))

        def task():
            return CseClient(email, password).download(epw.part_id, out, progress)

        def done(path):
            self._cse_update_login_label(verified=True)
            self._auto_import_next = bool(self.settings.get("cse_auto_import", True))
            self._load_download(path)

        self._run_bg(f"Downloading {epw.mpn or epw.part_id} from Component Search Engine...",
                     task, done)

    def _cse_update_login_label(self, verified: bool = False, rejected: bool = False) -> None:
        email, password = self._cse_login
        if rejected:
            text = "CSE: login rejected"
        elif email and password:
            text = f"CSE: {email}" + (" (signed in)" if verified else "")
        else:
            text = "CSE: not signed in"
        self.cse_login_var.set(text)

    def _cse_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Component Search Engine")
        dlg.transient(self.root)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, wraplength=580, justify="left", text=(
            "Your componentsearchengine.com login is used only to download models of parts you "
            "click on CSE (same as the SamacSys Library Loader). It is sent only to "
            "componentsearchengine.com over HTTPS. With 'Remember login' it is stored encrypted "
            "for your Windows account (DPAPI); otherwise it is kept only until the app closes."
        )).grid(row=0, column=0, columnspan=3, sticky="w")
        email_var = tk.StringVar(value=self._cse_login[0])
        pw_var = tk.StringVar(value=self._cse_login[1])
        remember = tk.BooleanVar(value=bool(self.creds.get(CRED_EMAIL)))
        ttk.Label(frm, text="Email:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=email_var, width=40).grid(row=1, column=1, sticky="w",
                                                              pady=(8, 0))
        ttk.Label(frm, text="Password:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm, textvariable=pw_var, width=40, show="*").grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(frm, variable=remember, text="Remember login (encrypted, restored when "
                        "the app starts)").grid(row=3, column=1, sticky="w")
        status = tk.StringVar(value=self.cse_login_var.get())
        ttk.Label(frm, textvariable=status, foreground="#555").grid(row=4, column=1, sticky="w")

        def store(email, pw):
            self._cse_login = (email, pw)
            try:
                if remember.get():
                    self.creds.set(CRED_EMAIL, email)
                    self.creds.set(CRED_PASSWORD, pw, strip=False)
                else:
                    self.creds.set(CRED_EMAIL, "")
                    self.creds.set(CRED_PASSWORD, "")
            except Exception as e:
                log.exception("Saving CSE login failed")
                messagebox.showerror("Error", f"Could not save the login: {e}", parent=dlg)

        def sign_in():
            email, pw = email_var.get().strip(), pw_var.get()
            if not (email and pw):
                status.set("Enter email and password.")
                return
            status.set("Checking with componentsearchengine.com ...")
            dlg.update_idletasks()
            try:
                CseClient(email, pw).check_login()
            except CseAuthError as e:
                status.set(str(e))
                self._cse_update_login_label(rejected=True)
                return
            except CseError as e:
                status.set(f"Could not check the login: {e}")
                return
            store(email, pw)
            self._cse_update_login_label(verified=True)
            status.set(f"Signed in as {email}" + (" (remembered)" if remember.get() else
                                                  " (this session only)"))

        def log_out():
            self._cse_login = ("", "")
            self.creds.set(CRED_EMAIL, "")
            self.creds.set(CRED_PASSWORD, "")
            pw_var.set("")
            self._cse_update_login_label()
            status.set("Logged out - the saved login was deleted.")

        lb = ttk.Frame(frm)
        lb.grid(row=5, column=1, sticky="w", pady=4)
        ttk.Button(lb, text="Sign in & test", command=sign_in).pack(side="left")
        ttk.Button(lb, text="Log out", command=log_out).pack(side="left", padx=4)
        ttk.Button(lb, text="Open componentsearchengine.com",
                   command=lambda: webbrowser.open(CSE_HOME)).pack(side="left", padx=4)

        ttk.Separator(frm).grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(frm, text="Watch this download folder:").grid(row=7, column=0, sticky="w")
        folder = tk.StringVar(value=str(self._cse_folder()))
        ttk.Entry(frm, textvariable=folder, width=50).grid(row=7, column=1, padx=4, sticky="w")

        def browse():
            p = filedialog.askdirectory(parent=dlg, initialdir=folder.get())
            if p:
                folder.set(os.path.normpath(p))
        ttk.Button(frm, text="Browse...", command=browse).grid(row=7, column=2)
        auto = tk.BooleanVar(value=bool(self.settings.get("cse_auto_import", True)))
        ttk.Checkbutton(frm, variable=auto, text="Import into the destination libraries "
                        "automatically when the download is complete").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=6)

        def save():
            if not Path(folder.get()).is_dir():
                messagebox.showerror("Folder not found", folder.get(), parent=dlg)
                return
            same = Path(folder.get()) == default_downloads_dir()
            self.settings.set("cse_watch_dir", "" if same else folder.get())
            self.settings.set("cse_auto_import", auto.get())
            email, pw = email_var.get().strip(), pw_var.get()
            if (email, pw) != self._cse_login and email and pw:
                store(email, pw)              # saved without testing; tested on first download
                self._cse_update_login_label()
            self.status_var.set("Component Search Engine settings saved.")
            dlg.destroy()
        ttk.Button(frm, text="Save && close", command=save).grid(row=9, column=2, sticky="e")

    # ================= SnapEDA direct: search -> sign in -> download -> import =================
    def _snap_client(self) -> SnapClient:
        """The session's SnapEDA client (created on the worker thread, reused afterwards)."""
        user, pw = self._snap_login
        if self._snap is None or (self._snap._user, self._snap._pw) != (user, pw):
            # a remembered login also keeps the signed-in session (SnapEDA limits sign-ins)
            saved = self.creds.get(SNAP_SESSION) if self.creds.get(SNAP_USER) == user else ""
            self._snap = SnapClient(user, pw, session=saved)
        return self._snap

    def _snap_save_session(self) -> None:
        """Keep the signed-in SnapEDA session (encrypted) when the login is remembered."""
        try:
            if self._snap is not None and self.creds.get(SNAP_USER) and                     self._snap.session != self.creds.get(SNAP_SESSION):
                self.creds.set(SNAP_SESSION, self._snap.session, strip=False)
        except Exception:
            log.exception("Saving the SnapEDA session failed")

    def _snap_direct(self) -> None:
        mpn = self._current_mpn()
        if not mpn:
            return
        if check_library(self.dst_sch.get(), ".SchLib") or \
                check_library(self.dst_pcb.get(), ".PcbLib"):
            messagebox.showerror("SnapEDA", "Select the destination SchLib and PcbLib first - the "
                                 "part is imported into them.", parent=self.root)
            return
        if not all(self._snap_login):
            messagebox.showinfo("SnapEDA", "Sign in with your SnapEDA account first "
                                "(Settings > SnapEDA).", parent=self.root)
            self._snap_dialog()
            return
        self._run_bg(f"Searching SnapEDA for {mpn}...",
                     lambda: (mpn, self._snap_client().search(mpn)), self._snap_found)

    def _snap_found(self, result) -> None:
        mpn, parts = result
        exact = snap_exact_matches(parts, mpn)
        if not exact:
            near = [f"{p.mpn} ({p.manufacturer})" for p in parts[:8]]
            self._set_info(
                [(f"SnapEDA has no exact {mpn} with a symbol and footprint.\n", "warn")]
                + ([("  Similar, NOT the same part: " + ", ".join(near) + "\n", "")] if near else [])
                + [("  Try: Get from CSE (direct), From Mouser (ECAD model), or EasyEDA by LCSC #.\n",
                    "")])
            self.status_var.set(f"No exact SnapEDA model for {mpn}.")
            return
        want_mfr = self.param_vars["Manufacturer"].get().strip().lower()
        same_mfr = [p for p in exact if want_mfr and (p.manufacturer.lower() == want_mfr or
                                                       p.manufacturer.lower().startswith(want_mfr + " "))]
        if len(exact) == 1:
            self._snap_download(exact[0])
        elif len(same_mfr) == 1:
            self._snap_download(same_mfr[0])
        else:
            self._cse_choose(exact, pick=self._snap_download, site="SnapEDA")

    def _snap_download(self, part) -> None:
        for key, val in (("Manufacturer", part.manufacturer), ("Manufacturer Part Number", part.mpn),
                         ("Description", part.description)):
            if val and not self.param_vars[key].get().strip():
                self.param_vars[key].set(val)
        out = self.work_dir / "downloads" / f"snapeda-{part.unipart_id}-{os.getpid()}-{id(part)}"
        label = f"{part.mpn} ({part.manufacturer})"

        def progress(done, total):
            self._progress_text = (f"Downloading {label} from SnapEDA: {human_size(done)}"
                                   + (f" of {human_size(total)}" if total else ""))

        def done(path):
            self._snap_save_session()
            self._auto_import_next = True        # the user asked for this part: import it
            self._load_download(path)

        self._run_bg(f"Signing in to SnapEDA and downloading {label}...",
                     lambda: self._snap_client().download(part, out, progress), done)

    def _snap_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("SnapEDA")
        dlg.transient(self.root)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, wraplength=560, justify="left", text=(
            "Your snapeda.com login is used only to download the Altium models of parts you ask "
            "for with 'Get from SnapEDA (direct)'. It is sent only to www.snapeda.com over HTTPS. "
            "With 'Remember login' it is stored encrypted for your Windows account (DPAPI); "
            "otherwise it is kept only until the app closes.")).grid(row=0, column=0, columnspan=2,
                                                                       sticky="w")
        user_var = tk.StringVar(value=self._snap_login[0])
        pw_var = tk.StringVar(value=self._snap_login[1])
        remember = tk.BooleanVar(value=bool(self.creds.get(SNAP_USER)) or not self._snap_login[0])
        ttk.Label(frm, text="Username or email:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=user_var, width=40).grid(row=1, column=1, sticky="w",
                                                             pady=(8, 0))
        ttk.Label(frm, text="Password:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm, textvariable=pw_var, width=40, show="*").grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(frm, variable=remember, text="Remember login (encrypted, restored when "
                        "the app starts)").grid(row=3, column=1, sticky="w")
        status = tk.StringVar(value="Signed in" if all(self._snap_login) else "Not signed in")
        ttk.Label(frm, textvariable=status, foreground="#555", wraplength=420).grid(
            row=4, column=1, sticky="w")

        def store(user, pw):
            self._snap_login = (user, pw)
            try:
                self.creds.set(SNAP_USER, user if remember.get() else "")
                self.creds.set(SNAP_PASSWORD, pw if remember.get() else "", strip=False)
                if not remember.get():
                    self.creds.set(SNAP_SESSION, "")
            except Exception as e:
                log.exception("Saving SnapEDA login failed")
                messagebox.showerror("Error", f"Could not save the login: {e}", parent=dlg)

        def sign_in():
            user, pw = user_var.get().strip(), pw_var.get()
            if not (user and pw):
                status.set("Enter username and password.")
                return
            status.set("Checking with snapeda.com ...")
            dlg.update_idletasks()
            client = SnapClient(user, pw)
            try:
                client.login()
            except SnapAuthError as e:
                status.set(str(e))
                return
            except SnapError as e:
                status.set(f"Could not check the login: {e}")
                return
            store(user, pw)
            self._snap = client
            self._snap_save_session()
            status.set(f"Signed in as {user}" + (" (remembered)" if remember.get() else
                                                 " (this session only)"))

        def log_out():
            self._snap_login, self._snap = ("", ""), None
            self.creds.set(SNAP_USER, "")
            self.creds.set(SNAP_PASSWORD, "")
            self.creds.set(SNAP_SESSION, "")
            pw_var.set("")
            status.set("Logged out - the saved login was deleted.")

        lb = ttk.Frame(frm)
        lb.grid(row=5, column=1, sticky="w", pady=4)
        ttk.Button(lb, text="Sign in & test", command=sign_in).pack(side="left")
        ttk.Button(lb, text="Log out", command=log_out).pack(side="left", padx=4)
        ttk.Button(lb, text="Open snapeda.com",
                   command=lambda: webbrowser.open(SNAPEDA_HOME)).pack(side="left", padx=4)
        ttk.Button(frm, text="Close", command=dlg.destroy).grid(row=6, column=1, sticky="e")

    # ================= SamacSys Library Loader library =================
    def _from_samacsys(self) -> None:
        sch, pcb = self.settings.get("samacsys_schlib", ""), self.settings.get("samacsys_pcblib", "")
        if not (sch and os.path.isfile(sch) and pcb and os.path.isfile(pcb)):
            messagebox.showinfo(
                "SamacSys library", "Select the SchLib and PcbLib that the official SamacSys "
                "Library Loader adds parts to (see Library Loader settings). This is asked once.",
                parent=self.root)
            sch = filedialog.askopenfilename(parent=self.root, title="SamacSys symbol library",
                                             filetypes=[("Altium SchLib", "*.SchLib")])
            if not sch:
                return
            guess = os.path.splitext(sch)[0] + ".PcbLib"
            pcb = guess if os.path.isfile(guess) else filedialog.askopenfilename(
                parent=self.root, title="SamacSys footprint library",
                filetypes=[("Altium PcbLib", "*.PcbLib")])
            if not pcb:
                return
            sch, pcb = os.path.normpath(sch), os.path.normpath(pcb)
            for p, ext in ((sch, ".SchLib"), (pcb, ".PcbLib")):
                err = check_library(p, ext)
                if err:
                    messagebox.showerror("Invalid library", err, parent=self.root)
                    return
            self.settings.set("samacsys_schlib", sch)
            self.settings.set("samacsys_pcblib", pcb)
        self._step_file = ""
        self.src_pcb.set(pcb)
        self.src_sch.set(sch)          # triggers the component list (picks the MPN if present)
        found = self.comp_var.get()
        self._set_info([("Source: SamacSys library\n", "bold"), (f"  {sch}\n  {pcb}\n", ""),
                        (f"  Component: {found}\n" if found else
                         "  The MPN was not found in this library - download it on Component "
                         "Search Engine with the Library Loader running, then click the button "
                         "again, or choose a component.\n", "" if found else "warn")])

    # ================= EasyEDA (LCSC number) =================
    def _fetch_lcsc(self) -> None:
        lcsc = clean_lcsc(self.lcsc_var.get() or self.param_vars["LCSC Part #"].get())
        if not lcsc:
            messagebox.showerror("LCSC number", "Enter an LCSC part number, e.g. C2040.",
                                 parent=self.root)
            return
        self.lcsc_var.set(lcsc)
        x2 = self._x2()
        if not x2:
            return
        if not bridge.is_altium_running() and not messagebox.askokcancel(
                "Altium is not running", "Altium Designer will be started to build the part.",
                parent=self.root):
            return
        self._run_bg(f"Fetching {lcsc} from EasyEDA and building it in Altium...",
                     lambda: fetch_and_build(lcsc, self.work_dir / "easyeda", x2, self.work_dir),
                     self._staged_done)

    def _staged_done(self, st) -> None:
        lines = [(f"EasyEDA part {st.lcsc}: ", "bold"), (UNOFFICIAL_NOTE + "\n", "warn")]
        lines += [("  Converter: " + w + "\n", "warn") for w in st.part.warnings]
        lines += [("  Builder: " + w + "\n", "warn") for w in st.build.warnings]
        if st.problems:
            lines += [("ERROR: " + p + "\n", "err") for p in st.problems]
            self._set_info(lines)
            self.status_var.set("EasyEDA part could not be built (see Messages).")
            return
        # Fill blank fields only. LCSC Part # is set: the part was fetched by that number.
        self.param_vars["LCSC Part #"].set(st.lcsc)
        fill = {"Manufacturer": st.part.params.get("Manufacturer", ""),
                "Manufacturer Part Number": st.part.params.get("Manufacturer Part", ""),
                "Description": st.part.meta.get("DESC", "")}
        for k, v in fill.items():
            if v and not self.param_vars[k].get().strip():
                self.param_vars[k].set(v)
        self._step_file = ""                      # the EasyEDA builder embeds its own STEP
        self.src_pcb.set(str(st.pcblib))
        self.src_sch.set(str(st.schlib))
        self._refresh_components(prefer=st.build.symbol)
        lines += [(f"  Built symbol {st.build.symbol} ({st.part.pins} pins) and footprint "
                   f"{st.build.footprint} ({st.build.pads} pads, {st.build.primitives} objects) "
                   "in staging libraries.\n", "ok"),
                  ("  Choose the destination and click Import into Altium.\n", "bold")]
        self._set_info(lines)
        self.status_var.set(f"{st.lcsc} ready to import.")

    # ================= import =================
    def _x2(self) -> Path | None:
        x2 = bridge.find_x2(self.settings.get("x2_path", ""))
        if not x2:
            messagebox.showerror("Altium not found",
                                 "Altium Designer (X2.EXE) was not found. Set \"x2_path\" in "
                                 "settings.json in the data folder.", parent=self.root)
        return x2

    def on_import(self, auto: bool = False) -> None:
        """auto=True: started by a finished CSE download (destinations were shown up front)."""
        params = {k: v.get().strip() for k, v in self.param_vars.items()}
        req = ImportRequest(
            src_schlib=self.src_sch.get(), src_pcblib=self.src_pcb.get(),
            dest_schlib=self.dst_sch.get(), dest_pcblib=self.dst_pcb.get(),
            params={k: v for k, v in params.items() if k != "Description"},
            description=params["Description"], on_conflict=self.conflict_var.get(),
            link_mode=LINK_MODES.get(self.link_var.get(), "name"),
            source_label=Path(self.src_sch.get()).parent.name if self.src_sch.get() else "",
            component="" if self.comp_var.get() == ALL_COMPONENTS else self.comp_var.get(),
            step_file=self._step_file if os.path.isfile(self._step_file) else "")
        if req.step_file and self._step_place is not None:
            pl = self._step_place
            req.step_placement = (pl.rot_x, pl.rot_y, pl.rot_z, pl.x_mm, pl.y_mm, pl.z_mm)
            req.step_placement_note = ("placed as Component Search Engine specifies: "
                                       + pl.describe())
        if not req.component and len(self.comp_box["values"]) > 1 \
                and self.comp_var.get() != ALL_COMPONENTS:
            messagebox.showerror("Cannot import", "Choose the component to import (the source "
                                 "library contains several).", parent=self.root)
            return
        self.settings.set("on_conflict", req.on_conflict)
        self.settings.set("link_mode", req.link_mode)
        errors = validate(req)
        if errors:
            messagebox.showerror("Cannot import", "\n".join(errors), parent=self.root)
            return
        x2 = self._x2()
        if not x2:
            return
        timeout = 240
        if not bridge.is_altium_running():
            if not messagebox.askokcancel(
                    "Altium is not running",
                    "Altium Designer will be started to run the import. This can take a minute.",
                    parent=self.root):
                return
            timeout = 420
        if not auto and not messagebox.askokcancel(
                "Confirm import",
                f"Import from:\n  {req.src_schlib}\n  {req.src_pcblib}\n\ninto:\n  {req.dest_schlib}\n"
                f"  {req.dest_pcblib}\n\nBoth destination libraries are backed up first. Existing "
                f"components are never overwritten.", parent=self.root):
            return
        self._pending_req = req
        self._run_bg("Importing in Altium... (watch Altium for any dialog)",
                     lambda: run_import(req, x2, self.work_dir, timeout), self._show_report)

    def _show_report(self, report) -> None:
        req = self._pending_req
        try:
            record_import(self.db, req, report)
        except Exception:
            log.exception("Recording import failed")
        lines = []
        for line in report.summary().splitlines():
            tag = ("err" if line.startswith(("ERROR", "Result: FAILED", "PIN/PAD", "PIN CHECK"))
                   else "warn" if line.startswith(("Warning", "NOTE")) else
                   "ok" if line.startswith(("Result: SUCCESS", "Added", "Linked",
                                            "Altium library search finds")) else "")
            lines.append((line + "\n", tag))
        self._set_info(lines)
        log.info("Import report:\n%s", report.summary())
        if report.ok and report.added_symbols:
            self.status_var.set("Import finished - the component is ready to place in Altium.")
            messagebox.showinfo("Import finished",
                                "Added: " + ", ".join(report.added_symbols) +
                                "\n\nSee Messages for the verification details.", parent=self.root)
        elif report.ok:
            self.status_var.set("Nothing imported (see Messages).")
        else:
            self.status_var.set("Import FAILED - nothing was saved (see Messages).")
            messagebox.showerror("Import failed", "\n".join(report.errors) or "See Messages.",
                                 parent=self.root)

    # ================= misc =================
    def _set_info(self, parts: list[tuple[str, str]]) -> None:
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        for text, tag in parts:
            self.info.insert("end", text, tag)
        self.info.configure(state="disabled")

    def _api_keys_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("API keys")
        dlg.transient(self.root)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Mouser Search API key:").grid(row=0, column=0, sticky="w")
        var = tk.StringVar(value=self.creds.get(MOUSER_CRED))
        ttk.Entry(frm, textvariable=var, width=48, show="*").grid(row=0, column=1, padx=4)
        ttk.Button(frm, text="Get a key",
                   command=lambda: webbrowser.open("https://www.mouser.com/api-search/")).grid(
            row=0, column=2)
        ttk.Label(frm, foreground="#555", text=(
            "Stored encrypted for your Windows account (DPAPI). Leave blank and save to remove.\n"
            f"The environment variable {CredentialStore.env_name(MOUSER_CRED)} overrides it.")).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=6)

        def save():
            try:
                self.creds.set(MOUSER_CRED, var.get())
            except Exception as e:
                log.exception("Saving credential failed")
                messagebox.showerror("Error", f"Could not save key: {e}", parent=dlg)
                return
            self.status_var.set("API key saved.")
            dlg.destroy()

        ttk.Button(frm, text="Save", command=save).grid(row=2, column=2, sticky="e")


def run_gui(settings: Settings, creds: CredentialStore, db: Database, providers: list) -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    icon = Path(__file__).with_name("app.ico")      # also bundled inside the .exe
    if icon.exists():
        try:
            root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass
    App(root, settings, creds, db, providers)
    root.mainloop()


def _package_part_id(pkg) -> int | None:
    """CSE part ID from the package's .epw (SamacSys downloads, also via Mouser), else None."""
    for epw in pkg.epw_files:
        try:
            return parse_epw(epw.read_text(encoding="utf-8", errors="replace")).part_id
        except (CseError, OSError):
            continue
    return None
