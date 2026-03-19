"""
Auto-Discovery Module - Printer Network Discovery

Provides multi-subnet network scanning, SNMP probing,
port scanning fallback, and automatic config integration.
"""

import re
import os
import logging
import socket
import subprocess
import platform
import threading
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger("PrinterManager.AutoDiscovery")

# Printer detection ports
PRINTER_PORTS = [9100, 515, 631]

# SNMP community string for probing
DEFAULT_COMMUNITY = "public"

# Default thread pool sizes
DEFAULT_PING_WORKERS = 30
DEFAULT_SNMP_WORKERS = 20

# Timeout values (seconds)
PING_TIMEOUT = 1
PORT_SCAN_TIMEOUT = 1
SNMP_TIMEOUT = 2


# ==============================================================================
# DATA CLASSES
# ==============================================================================

@dataclass
class DiscoveredPrinter:
    """Information about a discovered printer"""
    ip: str
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    sys_descr: str = ""
    snmp_available: bool = False
    port_available: bool = False
    open_ports: List[int] = field(default_factory=list)
    has_printer_mib: bool = False
    detection_confidence: float = 0.0
    status: str = "UNKNOWN"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "name": self.name or f"Printer_{self.ip.split('.')[-1]}",
            "manufacturer": self.manufacturer,
            "model": self.model,
            "serial": self.serial,
            "sys_descr": self.sys_descr,
            "snmp_available": self.snmp_available,
            "port_available": self.port_available,
            "open_ports": self.open_ports,
            "has_printer_mib": self.has_printer_mib,
            "detection_confidence": self.detection_confidence,
            "status": self.status,
        }


# ==============================================================================
# SUBNET DETECTOR
# ==============================================================================

class SubnetDetector:
    """Detects local subnets from network interfaces"""

    @staticmethod
    def get_local_subnets() -> List[str]:
        """
        Detect local subnets from network interfaces.

        Returns a list of subnet strings (e.g. ['192.168.1.0/24']).
        Filters out loopback and link-local addresses.
        """
        subnets: List[str] = []
        system = platform.system().lower()

        try:
            if system == "windows":
                subnets = SubnetDetector._detect_windows()
            else:
                subnets = SubnetDetector._detect_unix()
        except Exception as exc:
            logger.warning("Subnet auto-detection failed: %s", exc)

        # Fallback: try socket
        if not subnets:
            subnets = SubnetDetector._detect_via_socket()

        logger.info("Detected subnets: %s", subnets)
        return subnets

    @staticmethod
    def _detect_windows() -> List[str]:
        """Parse ipconfig output on Windows"""
        subnets: List[str] = []
        try:
            output = subprocess.check_output(
                ["ipconfig"], encoding="utf-8", errors="replace", timeout=10
            )
            ip = None
            mask = None
            for line in output.splitlines():
                line = line.strip()
                m = re.search(r"IPv4 Address[^:]*:\s*([\d.]+)", line)
                if m:
                    ip = m.group(1)
                    mask = None
                m2 = re.search(r"Subnet Mask[^:]*:\s*([\d.]+)", line)
                if m2 and ip:
                    mask = m2.group(1)
                    try:
                        net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                        if not net.is_loopback and not net.is_link_local:
                            subnets.append(str(net))
                    except ValueError:
                        pass
                    ip = None
                    mask = None
        except Exception as exc:
            logger.debug("ipconfig parsing failed: %s", exc)
        return subnets

    @staticmethod
    def _detect_unix() -> List[str]:
        """Parse ip addr / ifconfig output on Linux/macOS"""
        subnets: List[str] = []

        # Try 'ip addr' first (Linux)
        try:
            output = subprocess.check_output(
                ["ip", "addr"], encoding="utf-8", errors="replace", timeout=10
            )
            for line in output.splitlines():
                m = re.search(r"inet\s+([\d.]+/\d+)", line)
                if m:
                    try:
                        net = ipaddress.IPv4Network(m.group(1), strict=False)
                        if not net.is_loopback and not net.is_link_local:
                            subnets.append(str(net))
                    except ValueError:
                        pass
            if subnets:
                return subnets
        except Exception:
            pass

        # Fallback: ifconfig (macOS / older Linux)
        try:
            output = subprocess.check_output(
                ["ifconfig"], encoding="utf-8", errors="replace", timeout=10
            )
            ip = None
            for line in output.splitlines():
                m = re.search(r"inet\s+([\d.]+)\s+netmask\s+([\dxa-fA-F.]+)", line)
                if m:
                    ip_str = m.group(1)
                    mask_str = m.group(2)
                    # Convert hex netmask (macOS) to dotted-decimal
                    if mask_str.startswith("0x"):
                        try:
                            mask_int = int(mask_str, 16)
                            mask_bytes = mask_int.to_bytes(4, "big")
                            mask_str = ".".join(str(b) for b in mask_bytes)
                        except ValueError:
                            continue
                    try:
                        net = ipaddress.IPv4Network(f"{ip_str}/{mask_str}", strict=False)
                        if not net.is_loopback and not net.is_link_local:
                            subnets.append(str(net))
                    except ValueError:
                        pass
        except Exception as exc:
            logger.debug("ifconfig parsing failed: %s", exc)

        return subnets

    @staticmethod
    def _detect_via_socket() -> List[str]:
        """Last-resort: use socket to find local IP"""
        subnets: List[str] = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            # Assume /24
            net = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            subnets.append(str(net))
        except Exception:
            pass
        return subnets

    @staticmethod
    def parse_subnet_input(subnet_str: str) -> Optional[str]:
        """
        Parse and normalise a user-supplied subnet string.

        Accepts CIDR notation (192.168.1.0/24), a bare network prefix
        (192.168.1 → 192.168.1.0/24), or a host address (192.168.1.5 → 192.168.1.0/24).

        Returns a CIDR string or None if unparseable.
        """
        subnet_str = subnet_str.strip()

        # Strict allowlist: only digits, dots, slashes allowed
        if not re.match(r'^[0-9./]+$', subnet_str):
            logger.warning("Rejecting invalid subnet input (illegal chars): '%s'", subnet_str)
            return None

        # Reject if too many dots, slashes, or other anomalies
        if subnet_str.count('.') > 3 or subnet_str.count('/') > 1:
            logger.warning("Rejecting malformed subnet: '%s'", subnet_str)
            return None

        try:
            # CIDR notation
            return str(ipaddress.IPv4Network(subnet_str, strict=False))
        except ValueError:
            pass

        # Three-octet prefix (e.g. "192.168.1")
        m = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,3})$", subnet_str)
        if m:
            try:
                return str(ipaddress.IPv4Network(f"{m.group(1)}.0/24", strict=False))
            except ValueError:
                pass

        logger.warning("Cannot parse subnet: '%s'", subnet_str)
        return None


# ==============================================================================
# SNMP PROBE HELPERS
# ==============================================================================

def _snmp_get(ip: str, oid: str, community: str = DEFAULT_COMMUNITY,
              timeout: int = SNMP_TIMEOUT) -> Optional[str]:
    """
    Run a single snmpget command and return the value string, or None.
    Uses the system snmpget/snmpwalk binary if available.
    """
    try:
        cmd = [
            "snmpget",
            "-v", "2c",
            "-c", community,
            "-t", str(timeout),
            "-r", "0",
            "-Oqv",
            ip,
            oid,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
        if result.returncode == 0 and result.stdout.strip():
            value = result.stdout.strip().strip('"')
            if "No Such" not in value and "error" not in value.lower():
                return value
    except FileNotFoundError:
        logger.debug("snmpget not found in PATH")
    except Exception as exc:
        logger.debug("SNMP get failed for %s OID %s: %s", ip, oid, exc)
    return None


def _port_open(ip: str, port: int, timeout: float = PORT_SCAN_TIMEOUT) -> bool:
    """Check whether a TCP port is open on the given host"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _ping(ip: str, timeout: int = PING_TIMEOUT) -> bool:
    """Return True if the host responds to ping"""
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout), ip]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout + 2
        )
        return result.returncode == 0
    except Exception:
        return False


# ==============================================================================
# NETWORK SCANNER
# ==============================================================================

class NetworkScanner:
    """
    Multi-subnet network scanner with SNMP probe and port scan fallback.

    Implements a three-stage discovery pipeline:
      1. Ping sweep — find responsive hosts quickly.
      2. SNMP probe — identify printers via sysDescr / printer MIB.
      3. Port scan fallback — detect printers without SNMP.
    """

    def __init__(
        self,
        community: str = DEFAULT_COMMUNITY,
        ping_workers: int = DEFAULT_PING_WORKERS,
        snmp_workers: int = DEFAULT_SNMP_WORKERS,
        snmp_probe: bool = True,
        port_scan_fallback: bool = True,
    ):
        self.community = community
        self.ping_workers = ping_workers
        self.snmp_workers = snmp_workers
        self.snmp_probe = snmp_probe
        self.port_scan_fallback = port_scan_fallback
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Signal the scanner to stop after the current batch"""
        self._cancel_event.set()

    def stop(self) -> None:
        """Alias for cancel(); also shuts down any persistent executor"""
        self._cancel_event.set()
        if hasattr(self, '_executor') and self._executor:
            self._executor.shutdown(wait=False)
            logger.info("NetworkScanner executor shutdown")

    def reset(self) -> None:
        """Reset the cancel flag so the scanner can be reused"""
        self._cancel_event.clear()

    def scan_subnets(
        self,
        subnets: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[DiscoveredPrinter]:
        """
        Scan a list of subnets and return discovered printers.

        Args:
            subnets: List of CIDR strings (e.g. ['192.168.1.0/24'])
            progress_callback: Optional callback(completed, total, current_ip)

        Returns:
            List of DiscoveredPrinter objects
        """
        self.reset()

        # Resolve all IP addresses across all subnets
        all_ips: List[str] = []
        for subnet_str in subnets:
            normalised = SubnetDetector.parse_subnet_input(subnet_str)
            if normalised is None:
                logger.warning("Skipping invalid subnet: %s", subnet_str)
                continue
            try:
                network = ipaddress.IPv4Network(normalised, strict=False)
                # Skip the network and broadcast addresses
                all_ips.extend(str(ip) for ip in network.hosts())
                logger.info("Added %d hosts from %s", network.num_addresses - 2, normalised)
            except ValueError as exc:
                logger.warning("Invalid network %s: %s", normalised, exc)

        if not all_ips:
            logger.warning("No hosts to scan")
            return []

        logger.info("Starting scan of %d hosts across %d subnet(s)", len(all_ips), len(subnets))

        # Stage 1: Ping sweep
        alive_ips = self._ping_sweep(all_ips, progress_callback)

        if self._cancel_event.is_set():
            logger.info("Scan cancelled after ping sweep")
            return []

        logger.info("Ping sweep complete: %d/%d hosts alive", len(alive_ips), len(all_ips))

        # Stage 2: SNMP + port scan on alive hosts
        discovered = self._probe_hosts(alive_ips, progress_callback)

        logger.info(
            "Scan complete: %d printer(s) found from %d alive host(s)",
            len(discovered), len(alive_ips),
        )
        return discovered

    # ------------------------------------------------------------------
    # Stage 1: Ping sweep
    # ------------------------------------------------------------------

    def _ping_sweep(
        self,
        ips: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> List[str]:
        """Return list of IPs that responded to ping"""
        alive: List[str] = []
        total = len(ips)
        completed = 0

        with ThreadPoolExecutor(max_workers=self.ping_workers) as executor:
            future_to_ip = {executor.submit(_ping, ip): ip for ip in ips}

            for future in as_completed(future_to_ip):
                if self._cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                ip = future_to_ip[future]
                completed += 1

                try:
                    if future.result():
                        alive.append(ip)
                except Exception:
                    pass

                if progress_callback:
                    try:
                        progress_callback(completed, total, ip)
                    except Exception:
                        pass

        return alive

    # ------------------------------------------------------------------
    # Stage 2: SNMP probe + optional port scan
    # ------------------------------------------------------------------

    def _probe_hosts(
        self,
        ips: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> List[DiscoveredPrinter]:
        """Probe each alive host for printer-specific services"""
        discovered: List[DiscoveredPrinter] = []
        total = len(ips)
        completed = 0

        with ThreadPoolExecutor(max_workers=self.snmp_workers) as executor:
            future_to_ip = {executor.submit(self._probe_single, ip): ip for ip in ips}

            for future in as_completed(future_to_ip):
                if self._cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                ip = future_to_ip[future]
                completed += 1

                try:
                    result = future.result()
                    if result is not None:
                        discovered.append(result)
                        logger.info(
                            "Discovered: %s  mfr=%s  snmp=%s  ports=%s",
                            result.ip, result.manufacturer,
                            result.snmp_available, result.open_ports,
                        )
                except Exception as exc:
                    logger.debug("Probe failed for %s: %s", ip, exc)

                if progress_callback:
                    try:
                        progress_callback(completed, total, ip)
                    except Exception:
                        pass

        return discovered

    def _probe_single(self, ip: str) -> Optional[DiscoveredPrinter]:
        """
        Probe a single IP.  Returns a DiscoveredPrinter if a printer is found,
        otherwise None.
        """
        printer = DiscoveredPrinter(ip=ip, status="ONLINE")
        is_printer = False

        # --- SNMP probe ---
        if self.snmp_probe:
            sys_descr = _snmp_get(ip, "1.3.6.1.2.1.1.1.0", self.community)
            if sys_descr:
                printer.snmp_available = True
                printer.sys_descr = sys_descr

                # Vendor detection
                try:
                    from oid_registry import VendorDetector, Manufacturer
                    detection = VendorDetector.detect(sys_descr)
                    printer.manufacturer = detection.manufacturer.value
                    printer.model = detection.model_hint or ""
                    printer.detection_confidence = detection.confidence

                    if detection.manufacturer != Manufacturer.GENERIC:
                        is_printer = True
                except ImportError:
                    logger.warning(
                        "oid_registry not available — manufacturer detection skipped for %s", ip
                    )

                # Check printer MIB
                page_count = _snmp_get(
                    ip, "1.3.6.1.2.1.43.10.2.1.4.1.1", self.community
                )
                if page_count is not None:
                    printer.has_printer_mib = True
                    is_printer = True

                # Try to get serial
                serial = _snmp_get(
                    ip, "1.3.6.1.2.1.43.5.1.1.17.1", self.community
                )
                if serial:
                    printer.serial = serial

        # --- Port scan fallback ---
        if self.port_scan_fallback and not is_printer:
            open_ports = [p for p in PRINTER_PORTS if _port_open(ip, p)]
            if open_ports:
                printer.open_ports = open_ports
                printer.port_available = True
                is_printer = True

        if not is_printer:
            return None

        # Build a friendly name
        if not printer.name:
            suffix = ip.split(".")[-1]
            mfr = printer.manufacturer or "Printer"
            printer.name = f"{mfr}_{suffix}"

        return printer


# ==============================================================================
# AUTO-DISCOVERY SERVICE
# ==============================================================================

class AutoDiscoveryService:
    """
    High-level auto-discovery service integrating with the existing app framework.

    Coordinates NetworkScanner with DatabaseManager, ConfigManager (config.yaml),
    and the EventBus.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        db=None,
        auto_add: bool = False,
    ):
        """
        Args:
            config: Application config dict (loaded by ConfigManager)
            db: DatabaseManager instance (optional, for persistence)
            auto_add: If True, automatically add discovered printers to config
        """
        self.config = config
        self.db = db
        self.auto_add = auto_add
        self._scanner: Optional[NetworkScanner] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_discovery(
        self,
        subnets: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        completion_callback: Optional[Callable[[List[DiscoveredPrinter]], None]] = None,
        auto_add: Optional[bool] = None,
    ) -> threading.Thread:
        """
        Start an asynchronous discovery scan.

        Args:
            subnets: Subnets to scan; falls back to config['discovery']['subnets']
                     then auto-detected local subnets.
            progress_callback: Called with (completed, total, current_ip) during scan.
            completion_callback: Called with the list of DiscoveredPrinter when done.
            auto_add: Override the instance-level auto_add setting.

        Returns:
            The background Thread that performs the scan.
        """
        effective_auto_add = auto_add if auto_add is not None else self.auto_add
        effective_subnets = subnets or self._resolve_subnets()

        snmp_cfg = self.config.get("snmp", {})
        community = snmp_cfg.get("community", DEFAULT_COMMUNITY)
        disc_cfg = self.config.get("discovery", {})
        ping_workers = disc_cfg.get("ping_workers", DEFAULT_PING_WORKERS)
        snmp_workers = disc_cfg.get("snmp_workers", DEFAULT_SNMP_WORKERS)
        snmp_probe = disc_cfg.get("snmp_probe", True)
        port_scan = disc_cfg.get("port_scan_fallback", True)

        with self._lock:
            self._scanner = NetworkScanner(
                community=community,
                ping_workers=ping_workers,
                snmp_workers=snmp_workers,
                snmp_probe=snmp_probe,
                port_scan_fallback=port_scan,
            )

        def _run():
            self._emit_started(effective_subnets)
            try:
                found = self._scanner.scan_subnets(
                    effective_subnets, self._make_progress_cb(progress_callback)
                )
                if effective_auto_add:
                    self._add_to_config(found)
                self._save_to_db(found)
                self._emit_completed(found)
                if completion_callback:
                    completion_callback(found)
            except Exception as exc:
                logger.error("Discovery failed: %s", exc, exc_info=True)
                self._emit_completed([])

        thread = threading.Thread(target=_run, daemon=True, name="AutoDiscovery")
        thread.start()
        return thread

    def stop_discovery(self) -> None:
        """Cancel a running discovery scan"""
        with self._lock:
            if self._scanner:
                self._scanner.cancel()
                logger.info("Discovery cancellation requested")

    def get_subnets(self) -> List[str]:
        """Return the subnets that would be scanned"""
        return self._resolve_subnets()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_subnets(self) -> List[str]:
        """Determine subnets from config or auto-detection"""
        disc_cfg = self.config.get("discovery", {})

        # Accept both 'subnets' (list) and legacy 'subnet' (single string)
        subnets: List[str] = []
        configured = disc_cfg.get("subnets") or []
        if isinstance(configured, str):
            configured = [configured]
        subnets.extend(configured)

        single = disc_cfg.get("subnet", "")
        if single and single not in subnets:
            subnets.append(single)

        if not subnets:
            logger.info("No subnets in config — auto-detecting local subnets")
            subnets = SubnetDetector.get_local_subnets()

        return [s for s in subnets if s]

    def _add_to_config(self, found: List[DiscoveredPrinter]) -> None:
        """Add newly discovered printers to config.yaml (deduplicated)"""
        existing_ips = {p.get("ip") for p in self.config.get("printers", [])}
        added = 0
        for p in found:
            if p.ip not in existing_ips:
                entry: Dict[str, Any] = {
                    "ip": p.ip,
                    "name": p.name or f"Printer_{p.ip.split('.')[-1]}",
                }
                if p.manufacturer:
                    entry["manufacturer"] = p.manufacturer
                self.config.setdefault("printers", []).append(entry)
                existing_ips.add(p.ip)
                added += 1
        if added:
            try:
                # Import here to avoid circular dependency
                from main import ConfigManager  # type: ignore
                ConfigManager.save_config(self.config)
                logger.info("Auto-added %d printer(s) to config.yaml", added)
            except Exception as exc:
                logger.warning("Could not save config: %s", exc)

    def _save_to_db(self, found: List[DiscoveredPrinter]) -> None:
        """Persist discovered printers to the database"""
        if not self.db:
            return
        for p in found:
            try:
                self.db.save_discovered_printer(p.to_dict())
            except Exception as exc:
                logger.debug("Could not save printer %s to DB: %s", p.ip, exc)

    def _make_progress_cb(
        self, user_cb: Optional[Callable[[int, int, str], None]]
    ) -> Callable[[int, int, str], None]:
        """Wrap the user progress callback with event bus emission"""

        def _cb(completed: int, total: int, ip: str) -> None:
            self._emit_progress(completed, total, ip)
            if user_cb:
                try:
                    user_cb(completed, total, ip)
                except Exception:
                    pass

        return _cb

    # ------------------------------------------------------------------
    # EventBus helpers (optional — silently skip if not available)
    # ------------------------------------------------------------------

    def _emit_started(self, subnets: List[str]) -> None:
        try:
            from main import event_bus, Event, EventType  # type: ignore
            event_bus.emit(Event(EventType.DISCOVERY_STARTED, {"subnets": subnets}))
        except Exception:
            pass

    def _emit_progress(self, completed: int, total: int, ip: str) -> None:
        try:
            from main import event_bus, Event, EventType  # type: ignore
            event_bus.emit(Event(
                EventType.DISCOVERY_PROGRESS,
                {"completed": completed, "total": total, "ip": ip},
            ))
        except Exception:
            pass

    def _emit_completed(self, found: List[DiscoveredPrinter]) -> None:
        try:
            from main import event_bus, Event, EventType  # type: ignore
            event_bus.emit(Event(
                EventType.DISCOVERY_COMPLETED,
                {"found": [p.to_dict() for p in found]},
            ))
        except Exception:
            pass
