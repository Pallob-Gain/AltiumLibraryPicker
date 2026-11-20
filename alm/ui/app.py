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

from .. import __version__, lcsc_lookup
from ..altium import bridge
from ..config import Settings, app_dir
from ..credentials import CredentialStore
from ..db import Database
from ..importer import ImportRequest, record_import, run_import, validate
from ..models import ProviderState
from ..providers.links import ALTIUM_MPS_HELP, MANUAL_SOURCES
from ..search import SearchOutcome, run_search
from ..cse import (CSE_HOME, DownloadWatcher, claim, default_downloads_dir, file_key, human_size,
                   recent_download)
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
from . import theme

# Ultra Librarian login: kept for the person to paste into the sign-in page in their browser
# (the download itself needs the site's "I'm not a robot" check, so it happens there)
UL_EMAIL, UL_PASSWORD = "ul_email", "ul_password"
UL_LOGIN_URL = "https://app.ultralibrarian.com/account/login"
CLIPBOARD_CLEAR_MS = 30_000

ALL_COMPONENTS = "(all components)"
# browser sites whose ECAD download is picked up from the Downloads folder:
# key -> (button text, what to click on the site)
WATCH_SITES = {
    # Ultra Librarian shows a reCAPTCHA before every download, so the person does the download
    # in their own browser (signed in there, "Remember My Login") and the app imports the zip.
    "ultralibrarian": ("From Ultra Librarian...",
                       "Open the part (sign in once, tick 'Remember My Login'), click Download "
                       "Now, choose 'Altium Designer' and 'STEP', accept the terms, tick "
                       "'I'm not a robot' and click Download Now again."),
    # no site: use a library zip already downloaded (last 30 min), else wait for the next one
    "download": ("Look for download",
                 "Download the part's library zip in your browser from any site "
                 "(Mouser, CSE, Ultra Librarian, SnapEDA, manufacturer...)."),
}
RECENT_DOWNLOAD_SECS = 30 * 60

log = logging.getLogger(__name__)

RESULT_COLUMNS = [  # id, heading, width
    ("match", "Match", 150), ("mpn", "MPN", 170), ("mfr", "Manufacturer", 150),
    ("package", "Package", 150), ("stock", "LCSC stock", 85), ("datasheet", "Datasheet", 85),
    ("supplier_pn", "LCSC #", 90),
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
        self._watch_auto = False        # import what the watcher finds automatically
        self._used_downloads: set = set()   # cse.file_key of downloads already used
        self._auto_import_next = False
        self._progress_text = ""
        self._step_file = ""
        self._step_place = None        # StepPlacement for _step_file (from CSE), None = unknown
        # CSE login: restored from the encrypted store ("Remember login"), or session-only
        self._cse_login = (creds.get(CRED_EMAIL), creds.get(CRED_PASSWORD))
        self._snap_login = (creds.get(SNAP_USER), creds.get(SNAP_PASSWORD))
        self._snap: SnapClient | None = None     # signed-in SnapEDA session (memory only)
        self._ul_login = (creds.get(UL_EMAIL), creds.get(UL_PASSWORD))
        try:                     # search uses LCSC now: delete a Mouser API key saved by <= 1.2
            if creds.get("mouser_api_key"):
                creds.set("mouser_api_key", "")
        except Exception:
            log.warning("Could not remove the old Mouser API key")
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        theme.apply(root)
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
        m.add_command(label="Component Search Engine...", command=self._cse_dialog)
        m.add_command(label="SnapEDA...", command=self._snap_dialog)
        m.add_command(label="Ultra Librarian...", command=self._ul_dialog)
        m.add_separator()
        m.add_command(label="Open data folder (backups, logs)",
                      command=lambda: os.startfile(self.work_dir))
        menubar.add_cascade(label="Settings", menu=m)
        r.config(menu=menubar)

        # ---- 1. Search ----
        top = ttk.LabelFrame(r, text="1. Part number", padding=8)
        top.pack(fill="x", padx=12, pady=(10, 6))
        self.mpn_var = tk.StringVar(value=self.settings.get("last_mpn", ""))
        entry = ttk.Entry(top, textvariable=self.mpn_var, width=40, font=("Segoe UI", 11))
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self.on_search())
        entry.focus_set()
        self.search_btn = ttk.Button(top, text="Search", style="Accent.TButton",
                                     command=self.on_search)
        self.search_btn.pack(side="left", padx=8)
        self.search_info = ttk.Label(top, text="", style="Muted.TLabel")
        self.search_info.pack(side="left", padx=6)

        # ---- 2. Results + library sites ----
        mid = ttk.Frame(r)
        # packed at the end of _build (bottom-up) so a short window shrinks the results table
        res = ttk.LabelFrame(mid, text="2. Search results (double-click opens the source page)",
                             padding=8)
        res.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(res, columns=[c[0] for c in RESULT_COLUMNS], show="headings",
                                 height=4, selectmode="browse")
        for cid, head, width in RESULT_COLUMNS:
            self.tree.heading(cid, text=head)
            self.tree.column(cid, width=width, stretch=cid in ("mfr", "mpn"))
        self.tree.tag_configure("exact", background=theme.ROW_EXACT)
        self.tree.tag_configure("similar", background=theme.ROW_SIMILAR)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self._open_selected("source_url"))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._use_selected_result())
        rb = ttk.Frame(res)
        rb.pack(fill="x", pady=(6, 0))
        ttk.Button(rb, text="Open source page",
                   command=lambda: self._open_selected("source_url")).pack(side="left")
        ttk.Button(rb, text="Open datasheet",
                   command=lambda: self._open_selected("datasheet_url")).pack(side="left", padx=4)

        sites = ttk.LabelFrame(mid, text="Find a library (opens the site's search page)", padding=8)
        sites.pack(side="left", fill="y", padx=(10, 0))
        # two columns so it fits short screens
        for i, src in enumerate(MANUAL_SOURCES):
            label = "CSE (SamacSys)" if src.key == "cse" else src.name
            ttk.Button(sites, text=label, width=16, style="Small.TButton",
                       command=lambda s=src: self._open_site(s)).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=1, pady=1)
        n = len(MANUAL_SOURCES)
        all_btn = ttk.Button(sites, text="Open ALL sites", style="Small.TButton",
                             command=self._open_all_sites)
        if n % 2:                                   # fill the free cell of the last row
            all_btn.grid(row=n // 2, column=1, sticky="ew", padx=1, pady=1)
            last = n // 2 + 1
        else:
            all_btn.grid(row=n // 2, column=0, columnspan=2, sticky="ew", padx=1, pady=1)
            last = n // 2 + 1
        ttk.Button(sites, text="Altium Manufacturer Part Search", style="Small.TButton",
                   command=self._altium_mps).grid(row=last, column=0, columnspan=2,
                                                  sticky="ew", padx=1, pady=1)

        # ---- 3. Import ----
        imp = ttk.LabelFrame(r, text="3. Import into Altium", padding=8)
        # (packed at the end of _build)
        imp.columnconfigure(1, weight=1)
        ttk.Label(imp, text="Get the part:").grid(row=0, column=0, sticky="w")
        af = ttk.Frame(imp)
        af.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        self.cse_direct_btn = ttk.Button(af, text="Get from CSE (direct)",
                                         command=self._cse_direct)
        self.cse_direct_btn.pack(side="left", padx=(0, 4))
        ttk.Button(af, text="Get from SnapEDA (direct)", command=self._snap_direct).pack(
            side="left", padx=(0, 4))
        ttk.Separator(af, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        self.ul_btn = ttk.Button(af, text=WATCH_SITES["ultralibrarian"][0],
                                 command=lambda: self._cse_toggle("ultralibrarian"))
        self.ul_btn.pack(side="left", padx=(0, 4))
        self.look_btn = ttk.Button(af, text=WATCH_SITES["download"][0],
                                   command=lambda: self._cse_toggle("download"))
        self.look_btn.pack(side="left")
        self._site_btns = {"ultralibrarian": self.ul_btn, "download": self.look_btn}
        ttk.Separator(af, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        ttk.Label(af, text="EasyEDA by LCSC #").pack(side="left")
        self.lcsc_var = tk.StringVar()
        lcsc_entry = ttk.Entry(af, textvariable=self.lcsc_var, width=10)
        lcsc_entry.pack(side="left", padx=4)
        lcsc_entry.bind("<Return>", lambda e: self._fetch_lcsc())
        self.lcsc_btn = ttk.Button(af, text="Fetch", command=self._fetch_lcsc)
        self.lcsc_btn.pack(side="left")
        self.cse_login_var = tk.StringVar()       # which logins are saved (names only)

        ttk.Label(imp, text="Or a file:").grid(row=1, column=0, sticky="w")
        bf = ttk.Frame(imp)
        bf.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Button(bf, text="Select file (zip, IntLib, SchLib, PcbLib, LibPkg)...",
                   command=self._pick_download_file).pack(side="left")
        ttk.Button(bf, text="Select folder...", command=self._pick_download_folder).pack(
            side="left", padx=4)

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
                                      "'LCSC Part #' is always created)", padding=8)
        pf.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
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
        of.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
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
        self.import_btn = ttk.Button(of, text="Import into Altium", style="Accent.TButton",
                                     command=self.on_import)
        self.import_btn.pack(side="right")

        # ---- Messages ----
        msg = ttk.LabelFrame(r, text="Messages", padding=8)
        # (packed at the end of _build)
        self.info = tk.Text(msg, height=9, wrap="word")
        theme.style_text(self.info)
        sb = ttk.Scrollbar(msg, orient="vertical", command=self.info.yview)
        self.info.configure(yscrollcommand=sb.set)
        self.info.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._set_info([("Paste a part number and press Search, or go straight to step 3 with "
                         "a library you already downloaded.", "")])

        self._cse_update_login_label()
        self.status_var = tk.StringVar(value="Ready.")
        bar = ttk.Frame(r, style="Card.TFrame")
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, textvariable=self.cse_login_var, style="Status.TLabel").pack(side="right")
        ttk.Label(bar, textvariable=self.status_var, anchor="w", style="Status.TLabel").pack(
            side="left", fill="x", expand=True)
        ttk.Separator(r).pack(fill="x", side="bottom")
        msg.pack(side="bottom", fill="both", padx=12, pady=(0, 10))
        imp.pack(side="bottom", fill="x", padx=12, pady=6)
        mid.pack(fill="both", expand=True, padx=12)

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
                     lambda: run_search(mpn, self.providers, MANUAL_SOURCES),
                     lambda o: (self._show_outcome(o), self._show_lcsc(o)))

    def _show_lcsc(self, o: SearchOutcome) -> None:
        """Fill 'LCSC Part #' (and the EasyEDA box) from an exact-MPN LCSC listing."""
        if not any(s.provider == "LCSC" and s.state is ProviderState.OK for s in o.statuses):
            return                                   # the search line shows the error
        parts = [lcsc_lookup.LcscPart(r.lcsc, r.mpn, r.manufacturer, r.package, r.stock or 0)
                 for r in o.results if r.source == "LCSC" and r.lcsc and r.match.name == "EXACT"]
        maker = self.param_vars["Manufacturer"].get().strip()
        best = lcsc_lookup.pick(parts, maker)
        if not best:
            if not parts:
                self._append_info([(f"LCSC Part #: LCSC has no listing for exactly {o.query} - "
                                    "you can still use the download buttons in step 3.\n", "")])
            return
        top = best[0]
        current = self.param_vars["LCSC Part #"].get().strip()
        if not current or (o.best is not None and current == o.best.lcsc):   # auto-filled
            self.param_vars["LCSC Part #"].set(top.code)
        self.lcsc_var.set(top.code)                 # ready for 'EasyEDA by LCSC #: Fetch'
        line = (f"LCSC Part #: {top.code} ({top.manufacturer}"
                + (f", {top.package}" if top.package else "") + f", stock {top.stock:,})")
        if len(best) > 1:
            line += " - other listings: " + ", ".join(p.code for p in best[1:5])
        self._append_info([(line + "\n", "ok")])

    def _show_outcome(self, o: SearchOutcome) -> None:
        self.outcome = o
        self.tree.delete(*self.tree.get_children())
        for i, res in enumerate(o.results):
            self.tree.insert("", "end", iid=str(i),
                             tags=("exact" if res.match.name == "EXACT" else "similar",),
                             values=(res.match.value, res.mpn, res.manufacturer,
                                     res.package or "-",
                                     "-" if res.stock is None else f"{res.stock:,}",
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
        if res.lcsc:
            self.param_vars["LCSC Part #"].set(res.lcsc)
            self.lcsc_var.set(res.lcsc)
        self.status_var.set(f"Parameters filled from {res.source} result"
                            + (f" ({res.lcsc})." if res.lcsc else "."))

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

    def _cse_toggle(self, site: str) -> None:
        """Ultra Librarian: open the site and wait for its zip. Look for download: use a library
        downloaded in the last 30 minutes, else wait for the next one."""
        if self._watcher:
            self._cse_stop("Stopped waiting for the download.")
            return
        if site == "download":
            mpn = self.mpn_var.get().strip()
        else:
            mpn = self._current_mpn()
            if not mpn:
                return
        folder = self._cse_folder()
        if not folder.is_dir():
            messagebox.showerror("Download folder", f"Download folder not found:\n{folder}"
                                 "\n\nSet it under Settings > Component Search Engine.",
                                 parent=self.root)
            return
        auto = bool(self.settings.get("cse_auto_import", True)) and not (
            check_library(self.dst_sch.get(), ".SchLib")
            or check_library(self.dst_pcb.get(), ".PcbLib"))
        if site == "download":
            found = recent_download(folder, mpn, RECENT_DOWNLOAD_SECS,
                                    used=self._used_downloads)
            if found:
                # an older file is imported automatically only if its name has the MPN
                self.status_var.set(f"Found download: {found.path.name} "
                                    f"({human_size(found.size)})")
                self._cse_received(found, auto=auto and found.matches_mpn)
                return
        else:
            src = next(s for s in MANUAL_SOURCES if s.key == site)
            webbrowser.open(src.url(mpn))
        self._watcher = DownloadWatcher(folder, mpn)
        self._watch_auto = auto
        for key, btn in self._site_btns.items():
            if key == site:
                btn.configure(text="Stop waiting for the download")
            else:
                btn.state(["disabled"])
        if site == "download":
            steps = [("No library download from the last 30 minutes in your download folder - "
                      "waiting for one.\n", "bold"),
                     (f"  1. {WATCH_SITES[site][1]}\n", "")]
        else:
            steps = [(f"{src.name} - opened in your browser for {mpn}\n", "bold"),
                     (f"  1. {WATCH_SITES[site][1]}\n", "")]
        steps.append((f"  2. This app watches {folder} and uses the library zip that arrives "
                      "(symbol, footprint and 3D model if included).\n", ""))
        if auto:
            steps.append((f"  It is then imported automatically into:\n    {self.dst_sch.get()}\n"
                          f"    {self.dst_pcb.get()}\n  (backed up first, never overwrites "
                          "existing parts).\n", ""))
        else:
            steps.append(("  It is loaded as the source library; then click Import into "
                          "Altium.\n", ""))
        self._set_info(steps)
        if site == "ultralibrarian":
            self._ul_copy_buttons()
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
            self._cse_stop(st.message + " Click the button to "
                           "try again.")
            return
        elif st.state == "ready":
            self._cse_stop(f"Download complete: {st.path.name} ({human_size(st.size)})")
            self._cse_received(st)
            return
        self.root.after(1000, self._cse_poll)

    def _cse_received(self, st, auto: bool | None = None) -> None:
        try:
            self._used_downloads.add(file_key(st.path))   # 'Look for download' skips it later
            copy = claim(st.path, self.work_dir)
        except OSError as e:
            messagebox.showerror("Download",
                                 f"Could not read {st.path}:\n{e}\n\nIf the SamacSys Library Loader "
                                 "is running it may have moved the file - close it, or use "
                                 "'Get from CSE (direct)' instead.", parent=self.root)
            return
        try:
            epw = read_epw(copy)
        except (CseError, OSError) as e:
            messagebox.showerror("Component Search Engine", str(e), parent=self.root)
            return
        auto = self._watch_auto if auto is None else auto
        if epw:
            self._cse_fetch(epw, auto)
            return
        if not st.matches_mpn:
            self.status_var.set(f"Note: {st.path.name} does not contain the MPN in its name - "
                                "check the component.")
        self._auto_import_next = auto
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
                + [("  Try: Get from SnapEDA (direct), From Ultra Librarian, EasyEDA by LCSC #, "
                    "or the Celestial Altium Library (see README).\n",
                    "")])
            self.status_var.set(f"No exact CSE model for {mpn}.")
            return
        want_mfr = self.param_vars["Manufacturer"].get().strip()
        same_mfr = [p for p in exact if lcsc_lookup.same_maker(p.manufacturer, want_mfr)]
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

    def _cse_fetch(self, epw, auto: bool) -> None:
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
            self._auto_import_next = auto
            self._load_download(path)

        self._run_bg(f"Downloading {epw.mpn or epw.part_id} from Component Search Engine...",
                     task, done)

    def _cse_update_login_label(self, verified: bool = False, rejected: bool = False) -> None:
        """Which logins are saved - site names only, never the email."""
        names = []
        if rejected:
            names.append("CSE (rejected)")
        elif all(self._cse_login):
            names.append("CSE")
        if all(self._snap_login):
            names.append("SnapEDA")
        if all(self._ul_login):
            names.append("Ultra Librarian")
        self.cse_login_var.set(("Logins: " + ", ".join(names)) if names else
                               "No logins saved (Settings)")

    def _copy(self, text: str, secret: bool = False) -> None:
        """Put text on the clipboard; a password is removed again after 30 s."""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        if secret:
            def clear():
                try:
                    if self.root.clipboard_get() == text:
                        self.root.clipboard_clear()
                        self.root.clipboard_append("")
                except tk.TclError:
                    pass
            self.root.after(CLIPBOARD_CLEAR_MS, clear)

    def _ul_copy_buttons(self) -> None:
        """'Copy email' / 'Copy password' buttons inside Messages (Ultra Librarian sign-in)."""
        email, pw = self._ul_login
        if not (email and pw):
            return
        row = ttk.Frame(self.info, style="Card.TFrame")
        ttk.Button(row, text="Copy email", command=lambda: (
            self._copy(email), self.status_var.set("Email copied."))).pack(side="left")
        ttk.Button(row, text="Copy password", command=lambda: (
            self._copy(pw, secret=True),
            self.status_var.set("Password copied - it is removed from the clipboard in 30 s."))
                   ).pack(side="left", padx=4)
        self.info.configure(state="normal")
        self.info.insert("end", "  Sign-in on Ultra Librarian: ")
        self.info.window_create("end", window=row)
        self.info.insert("end", "\n")
        self.info.configure(state="disabled")

    def _ul_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Ultra Librarian")
        dlg.transient(self.root)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, wraplength=560, justify="left", text=(
            "Ultra Librarian asks for an \"I'm not a robot\" check before every download, so "
            "you sign in and download in your browser and the app imports the file. Save your "
            "login here to copy it into Ultra Librarian's sign-in page with one click. The app "
            "never sends it anywhere. With 'Remember login' it is stored encrypted for your "
            "Windows account (DPAPI); otherwise it is kept only until the app closes.")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        email, pw = self._ul_login
        filled = not (email or pw) and all(self._snap_login)
        if filled:                       # usually the same account as SnapEDA
            email, pw = self._snap_login
        email_var, pw_var = tk.StringVar(value=email), tk.StringVar(value=pw)
        remember = tk.BooleanVar(value=bool(self.creds.get(UL_EMAIL)) or not self._ul_login[0])
        ttk.Label(frm, text="Email:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frm, textvariable=email_var, width=40).grid(row=1, column=1, sticky="w",
                                                              pady=(10, 0))
        ttk.Label(frm, text="Password:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm, textvariable=pw_var, width=40, show="*").grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(frm, variable=remember, text="Remember login (encrypted, restored when "
                        "the app starts)").grid(row=3, column=1, sticky="w")
        status = tk.StringVar(value="Filled in from your SnapEDA login - click Save to keep it."
                              if filled else ("Saved" if all(self._ul_login) else "Not saved"))
        ttk.Label(frm, textvariable=status, style="Muted.TLabel", wraplength=420).grid(
            row=4, column=1, sticky="w")

        def save():
            e, p = email_var.get().strip(), pw_var.get()
            if not (e and p):
                status.set("Enter email and password.")
                return
            self._ul_login = (e, p)
            try:
                self.creds.set(UL_EMAIL, e if remember.get() else "")
                self.creds.set(UL_PASSWORD, p if remember.get() else "", strip=False)
            except Exception as ex:
                log.exception("Saving Ultra Librarian login failed")
                messagebox.showerror("Error", f"Could not save the login: {ex}", parent=dlg)
                return
            self._cse_update_login_label()
            status.set("Saved" + (" (remembered)" if remember.get() else " (this session only)"))

        def use_snap():
            if not all(self._snap_login):
                status.set("No SnapEDA login saved (Settings > SnapEDA).")
                return
            email_var.set(self._snap_login[0])
            pw_var.set(self._snap_login[1])
            status.set("Filled in from your SnapEDA login - click Save to keep it.")

        def log_out():
            self._ul_login = ("", "")
            self.creds.set(UL_EMAIL, "")
            self.creds.set(UL_PASSWORD, "")
            email_var.set("")
            pw_var.set("")
            self._cse_update_login_label()
            status.set("Logged out - the saved login was deleted.")

        def open_sign_in():
            if email_var.get().strip():
                self._copy(email_var.get().strip())
                status.set("Sign-in page opened; your email is on the clipboard.")
            webbrowser.open(UL_LOGIN_URL)

        lb = ttk.Frame(frm)
        lb.grid(row=5, column=1, sticky="w", pady=(6, 2))
        ttk.Button(lb, text="Save", style="Accent.TButton", command=save).pack(side="left")
        ttk.Button(lb, text="Use my SnapEDA login", command=use_snap).pack(side="left", padx=4)
        ttk.Button(lb, text="Log out", command=log_out).pack(side="left")
        cb = ttk.Frame(frm)
        cb.grid(row=6, column=1, sticky="w", pady=2)
        ttk.Button(cb, text="Open sign-in page", command=open_sign_in).pack(side="left")
        ttk.Button(cb, text="Copy email", command=lambda: (
            self._copy(email_var.get().strip()), status.set("Email copied."))).pack(
            side="left", padx=4)
        ttk.Button(cb, text="Copy password", command=lambda: (
            self._copy(pw_var.get(), secret=True),
            status.set("Password copied - removed from the clipboard in 30 s."))).pack(side="left")
        ttk.Button(frm, text="Close", command=dlg.destroy).grid(row=7, column=1, sticky="e",
                                                               pady=(8, 0))

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
        status = tk.StringVar(value="Signed in" if all(self._cse_login) else "Not signed in")
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
                + [("  Try: Get from CSE (direct), From Ultra Librarian, or EasyEDA by LCSC #.\n",
                    "")])
            self.status_var.set(f"No exact SnapEDA model for {mpn}.")
            return
        want_mfr = self.param_vars["Manufacturer"].get().strip()
        same_mfr = [p for p in exact if lcsc_lookup.same_maker(p.manufacturer, want_mfr)]
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
            self._cse_update_login_label()
            self._snap_save_session()
            status.set(f"Signed in as {user}" + (" (remembered)" if remember.get() else
                                                 " (this session only)"))

        def log_out():
            self._snap_login, self._snap = ("", ""), None
            self.creds.set(SNAP_USER, "")
            self.creds.set(SNAP_PASSWORD, "")
            self.creds.set(SNAP_SESSION, "")
            pw_var.set("")
            self._cse_update_login_label()
            status.set("Logged out - the saved login was deleted.")

        lb = ttk.Frame(frm)
        lb.grid(row=5, column=1, sticky="w", pady=4)
        ttk.Button(lb, text="Sign in & test", command=sign_in).pack(side="left")
        ttk.Button(lb, text="Log out", command=log_out).pack(side="left", padx=4)
        ttk.Button(lb, text="Open snapeda.com",
                   command=lambda: webbrowser.open(SNAPEDA_HOME)).pack(side="left", padx=4)
        ttk.Button(frm, text="Close", command=dlg.destroy).grid(row=6, column=1, sticky="e")

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
        self.info.configure(state="disabled")
        self._append_info(parts)

    def _append_info(self, parts: list[tuple[str, str]]) -> None:
        self.info.configure(state="normal")
        for text, tag in parts:
            self.info.insert("end", text, tag)
        self.info.configure(state="disabled")


def run_gui(settings: Settings, creds: CredentialStore, db: Database, providers: list) -> None:
    root = tk.Tk()
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
