"""
Auto-Discovery GUI Panel

Provides a ttk.Frame-based discovery panel that can be embedded
as a tab in the main application Notebook.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import csv
import logging
from typing import List, Optional, Dict, Any, Callable

logger = logging.getLogger("PrinterManager.DiscoveryGUI")


class DiscoveryPanel(ttk.Frame):
    """
    GUI panel for network printer auto-discovery.

    Designed to be added as a tab in the main application Notebook::

        panel = DiscoveryPanel(notebook, config, db)
        notebook.add(panel, text="Auto-Discovery")
    """

    def __init__(
        self,
        parent,
        config: Dict[str, Any],
        db=None,
        on_printers_added: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ):
        super().__init__(parent)
        self.config = config
        self.db = db
        self.on_printers_added = on_printers_added

        self._discovery_service = None
        self._scan_thread: Optional[threading.Thread] = None
        self._results: List[Dict[str, Any]] = []

        self._setup_ui()
        self._subscribe_events()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the full panel layout"""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)  # results table grows

        # ── Settings ──────────────────────────────────────────────────
        settings_lf = ttk.LabelFrame(self, text="Discovery Settings", padding=8)
        settings_lf.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        settings_lf.columnconfigure(1, weight=1)

        # Subnets
        ttk.Label(settings_lf, text="Subnets to scan:").grid(
            row=0, column=0, sticky="nw", padx=(0, 6)
        )
        subnet_frame = ttk.Frame(settings_lf)
        subnet_frame.grid(row=0, column=1, columnspan=3, sticky="ew")
        subnet_frame.columnconfigure(0, weight=1)

        self._subnet_text = tk.Text(subnet_frame, height=3, width=40, wrap="none")
        self._subnet_text.grid(row=0, column=0, sticky="ew")
        subnet_sb = ttk.Scrollbar(subnet_frame, orient="vertical",
                                   command=self._subnet_text.yview)
        subnet_sb.grid(row=0, column=1, sticky="ns")
        self._subnet_text.configure(yscrollcommand=subnet_sb.set)

        ttk.Button(
            subnet_frame, text="Auto-detect", command=self._auto_detect_subnets, width=12
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Populate from config
        disc_cfg = self.config.get("discovery", {})
        subnets = disc_cfg.get("subnets", [])
        if isinstance(subnets, str):
            subnets = [subnets]
        single = disc_cfg.get("subnet", "")
        if single and single not in subnets:
            subnets.append(single)
        if subnets:
            self._subnet_text.insert("end", "\n".join(subnets))

        # Thread count
        ttk.Label(settings_lf, text="Threads:").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self._threads_var = tk.IntVar(value=disc_cfg.get("ping_workers", 30))
        threads_spin = ttk.Spinbox(
            settings_lf, from_=1, to=100, textvariable=self._threads_var, width=6
        )
        threads_spin.grid(row=1, column=1, sticky="w", pady=(6, 0))

        # Checkboxes
        self._auto_add_var = tk.BooleanVar(value=disc_cfg.get("auto_add", False))
        ttk.Checkbutton(
            settings_lf, text="Auto-add discovered printers to config",
            variable=self._auto_add_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self._snmp_probe_var = tk.BooleanVar(value=disc_cfg.get("snmp_probe", True))
        ttk.Checkbutton(
            settings_lf, text="SNMP probe",
            variable=self._snmp_probe_var
        ).grid(row=3, column=0, sticky="w")

        self._port_scan_var = tk.BooleanVar(
            value=disc_cfg.get("port_scan_fallback", True)
        )
        ttk.Checkbutton(
            settings_lf, text="Port scan fallback (9100, 515, 631)",
            variable=self._port_scan_var
        ).grid(row=3, column=1, sticky="w")

        # ── Progress ──────────────────────────────────────────────────
        progress_frame = ttk.Frame(self)
        progress_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        progress_frame.columnconfigure(0, weight=1)

        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            progress_frame, variable=self._progress_var, maximum=100
        )
        self._progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 2))

        self._status_label = ttk.Label(progress_frame, text="Ready")
        self._status_label.grid(row=1, column=0, sticky="w")

        # Scan / Stop buttons
        btn_top = ttk.Frame(progress_frame)
        btn_top.grid(row=0, column=1, padx=(8, 0))
        self._scan_btn = ttk.Button(btn_top, text="▶ Start Scan", command=self._start_scan, width=14)
        self._scan_btn.pack(side="left", padx=2)
        self._stop_btn = ttk.Button(
            btn_top, text="■ Stop", command=self._stop_scan, state="disabled", width=8
        )
        self._stop_btn.pack(side="left", padx=2)

        # ── Results table ─────────────────────────────────────────────
        table_lf = ttk.LabelFrame(self, text="Discovered Printers", padding=4)
        table_lf.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)
        table_lf.columnconfigure(0, weight=1)
        table_lf.rowconfigure(0, weight=1)

        columns = ("IP", "Name", "Manufacturer", "Model", "SNMP", "Ports", "Status")
        self._tree = ttk.Treeview(
            table_lf, columns=columns, show="headings", height=14, selectmode="extended"
        )
        col_widths = {"IP": 120, "Name": 160, "Manufacturer": 120,
                      "Model": 160, "SNMP": 55, "Ports": 80, "Status": 80}
        for col in columns:
            self._tree.heading(
                col, text=col,
                command=lambda c=col: self._sort_column(c)
            )
            self._tree.column(col, width=col_widths.get(col, 100), anchor="w")

        vsb = ttk.Scrollbar(table_lf, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(table_lf, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Row colour tags
        self._tree.tag_configure("online", background="#d4f4d4")   # green
        self._tree.tag_configure("offline", background="#e8e8e8")  # grey

        # ── Action buttons ────────────────────────────────────────────
        action_frame = ttk.Frame(self)
        action_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(4, 10))

        ttk.Button(
            action_frame, text="Add Selected", command=self._add_selected, width=14
        ).pack(side="left", padx=4)
        ttk.Button(
            action_frame, text="Add All", command=self._add_all, width=10
        ).pack(side="left", padx=4)
        ttk.Button(
            action_frame, text="Export CSV", command=self._export_csv, width=12
        ).pack(side="left", padx=4)
        ttk.Button(
            action_frame, text="Refresh / Rescan", command=self._start_scan, width=16
        ).pack(side="left", padx=4)
        ttk.Button(
            action_frame, text="Load Saved", command=self._load_saved, width=12
        ).pack(side="right", padx=4)

    # ------------------------------------------------------------------
    # Event subscriptions
    # ------------------------------------------------------------------

    def _subscribe_events(self) -> None:
        """Subscribe to EventBus events if available"""
        try:
            from main import event_bus, EventType  # type: ignore
            event_bus.subscribe(EventType.DISCOVERY_STARTED, self._on_discovery_started)
            event_bus.subscribe(EventType.DISCOVERY_PROGRESS, self._on_discovery_progress)
            event_bus.subscribe(EventType.DISCOVERY_COMPLETED, self._on_discovery_completed)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Scan control
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        """Start a discovery scan in the background"""
        subnets = self._get_subnets_from_ui()
        if not subnets:
            messagebox.showerror(
                "No subnets",
                "Enter at least one subnet to scan, or click Auto-detect.",
                parent=self,
            )
            return

        try:
            from autodiscovery import AutoDiscoveryService  # type: ignore
        except ImportError:
            messagebox.showerror(
                "Module missing",
                "autodiscovery.py is not available.",
                parent=self,
            )
            return

        self._results.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)

        self._progress_var.set(0)
        self._status_label.config(text="Starting scan…")
        self._scan_btn.config(state="disabled")
        self._stop_btn.config(state="normal")

        auto_add = self._auto_add_var.get()
        threads = self._threads_var.get()

        # Build runtime config override
        cfg = dict(self.config)
        cfg.setdefault("discovery", {})
        cfg["discovery"]["ping_workers"] = threads
        cfg["discovery"]["snmp_workers"] = max(1, threads // 2)
        cfg["discovery"]["snmp_probe"] = self._snmp_probe_var.get()
        cfg["discovery"]["port_scan_fallback"] = self._port_scan_var.get()

        self._discovery_service = AutoDiscoveryService(cfg, db=self.db, auto_add=auto_add)
        self._scan_thread = self._discovery_service.start_discovery(
            subnets=subnets,
            completion_callback=self._on_scan_done,
        )

    def _stop_scan(self) -> None:
        """Stop the running scan"""
        if self._discovery_service:
            self._discovery_service.stop_discovery()
        self._status_label.config(text="Stopping…")
        self._stop_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # EventBus handlers (run on UI thread via root.after)
    # ------------------------------------------------------------------

    def _on_discovery_started(self, event) -> None:
        self._schedule_ui(lambda: self._status_label.config(text="Scan started…"))

    def _on_discovery_progress(self, event) -> None:
        data = event.data or {}
        completed = data.get("completed", 0)
        total = data.get("total", 1)
        ip = data.get("ip", "")
        pct = (completed / total * 100) if total else 0

        def _update():
            self._progress_var.set(pct)
            self._status_label.config(
                text=f"Scanning {ip}… ({completed}/{total})"
            )

        self._schedule_ui(_update)

    def _on_discovery_completed(self, event) -> None:
        data = event.data or {}
        found = data.get("found", [])

        def _update():
            self._progress_var.set(100)
            self._status_label.config(text=f"Completed — {len(found)} printer(s) found")
            self._scan_btn.config(state="normal")
            self._stop_btn.config(state="disabled")
            self._populate_results(found)

        self._schedule_ui(_update)

    def _on_scan_done(self, found) -> None:
        """Called from background thread when scan finishes"""
        result_dicts = [p.to_dict() if hasattr(p, "to_dict") else p for p in found]
        self._results = result_dicts

    # ------------------------------------------------------------------
    # Results table
    # ------------------------------------------------------------------

    def _populate_results(self, printers: list) -> None:
        """Fill the Treeview with scan results"""
        for item in self._tree.get_children():
            self._tree.delete(item)

        for p in printers:
            if hasattr(p, "to_dict"):
                p = p.to_dict()
            tag = "online" if p.get("status") == "ONLINE" else "offline"
            self._tree.insert(
                "", "end",
                values=(
                    p.get("ip", ""),
                    p.get("name", ""),
                    p.get("manufacturer", ""),
                    p.get("model", ""),
                    "✓" if p.get("snmp_available") else "✗",
                    ", ".join(str(port) for port in p.get("open_ports", [])),
                    p.get("status", ""),
                ),
                tags=(tag,),
            )

    def _sort_column(self, col: str) -> None:
        """Sort the Treeview by the given column"""
        items = [(self._tree.set(k, col), k) for k in self._tree.get_children("")]
        items.sort(key=lambda t: t[0].lower() if isinstance(t[0], str) else t[0])
        for idx, (_, k) in enumerate(items):
            self._tree.move(k, "", idx)

    # ------------------------------------------------------------------
    # Action buttons
    # ------------------------------------------------------------------

    def _add_to_config_from_items(self, items) -> None:
        """Add selected tree items to config.yaml"""
        existing_ips = {p.get("ip") for p in self.config.get("printers", [])}
        added = 0
        added_list: List[Dict[str, Any]] = []

        for item in items:
            values = self._tree.item(item, "values")
            if not values:
                continue
            ip = values[0]
            if ip not in existing_ips:
                entry: Dict[str, Any] = {
                    "ip": ip,
                    "name": values[1] or f"Printer_{ip.split('.')[-1]}",
                }
                mfr = values[2]
                if mfr:
                    entry["manufacturer"] = mfr
                self.config.setdefault("printers", []).append(entry)
                existing_ips.add(ip)
                added += 1
                added_list.append(entry)

        if added:
            try:
                from main import ConfigManager  # type: ignore
                ConfigManager.save_config(self.config)
            except Exception as exc:
                logger.warning("Could not save config: %s", exc)
            messagebox.showinfo("Added", f"Added {added} printer(s) to config.yaml", parent=self)
            if self.on_printers_added:
                self.on_printers_added(added_list)
        else:
            messagebox.showinfo(
                "Info", "All selected printers are already in config.", parent=self
            )

    def _add_selected(self) -> None:
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Select printers in the table first.", parent=self)
            return
        self._add_to_config_from_items(selected)

    def _add_all(self) -> None:
        all_items = self._tree.get_children()
        if not all_items:
            messagebox.showinfo("Info", "No printers to add.", parent=self)
            return
        self._add_to_config_from_items(all_items)

    def _export_csv(self) -> None:
        """Export scan results to CSV"""
        items = self._tree.get_children()
        if not items:
            messagebox.showinfo("Info", "No results to export.", parent=self)
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Discovery Results",
            parent=self,
        )
        if not path:
            return

        columns = ("IP", "Name", "Manufacturer", "Model", "SNMP", "Ports", "Status")
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(columns)
                for item in items:
                    writer.writerow(self._tree.item(item, "values"))
            messagebox.showinfo("Exported", f"Results saved to:\n{path}", parent=self)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)

    def _load_saved(self) -> None:
        """Load previously discovered printers from the database"""
        if not self.db:
            messagebox.showinfo("Info", "No database available.", parent=self)
            return
        try:
            saved = self.db.get_discovered_printers()
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)
            return

        for item in self._tree.get_children():
            self._tree.delete(item)
        for p in saved:
            self._tree.insert(
                "", "end",
                values=(
                    p.get("ip", ""),
                    p.get("name", ""),
                    p.get("manufacturer", ""),
                    p.get("model", ""),
                    "✓" if p.get("snmp_available") else "—",
                    "",
                    p.get("status", ""),
                ),
            )
        self._status_label.config(text=f"Loaded {len(saved)} saved printer(s)")

    # ------------------------------------------------------------------
    # Subnet helpers
    # ------------------------------------------------------------------

    def _get_subnets_from_ui(self) -> List[str]:
        """Extract non-empty subnet lines from the text widget"""
        raw = self._subnet_text.get("1.0", "end").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return lines

    def _auto_detect_subnets(self) -> None:
        """Populate subnet field with auto-detected local subnets"""
        try:
            from autodiscovery import SubnetDetector  # type: ignore
            subnets = SubnetDetector.get_local_subnets()
        except ImportError:
            messagebox.showerror("Error", "autodiscovery module not available.", parent=self)
            return

        if not subnets:
            messagebox.showinfo("Info", "No local subnets detected.", parent=self)
            return

        self._subnet_text.delete("1.0", "end")
        self._subnet_text.insert("end", "\n".join(subnets))
        self._status_label.config(text=f"Auto-detected {len(subnets)} subnet(s)")

    # ------------------------------------------------------------------
    # Thread-safe UI scheduling
    # ------------------------------------------------------------------

    def _schedule_ui(self, fn: Callable) -> None:
        """Schedule a function on the Tkinter main thread"""
        try:
            self.after(0, fn)
        except Exception:
            pass
