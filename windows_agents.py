"""
Windows Local Printer Agents Module v2.0
With enhanced logging, fallback strategies, and elevation detection

Agents for monitoring and managing local Windows printers via:
- WMI (Windows Management Instrumentation)
- PowerShell
- Windows Spooler API (Win32)
- Registry
"""

import subprocess
import logging
import json
import re
import os
import ctypes
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
import threading

logger = logging.getLogger("PrinterManager.WindowsAgents")

# Check platform
IS_WINDOWS = platform.system() == "Windows"

# Try WMI import
WMI_AVAILABLE = False
WMI_ERROR = None
try:
    if IS_WINDOWS:
        import wmi
        import pythoncom
        WMI_AVAILABLE = True
        logger.info("WMI module loaded successfully")
except ImportError as e:
    WMI_ERROR = str(e)
    logger.warning(f"WMI not available: {e}. Install: pip install wmi pywin32")

# Try win32print
WIN32_AVAILABLE = False
WIN32_ERROR = None
try:
    if IS_WINDOWS:
        import win32print
        import win32api
        import win32con
        WIN32_AVAILABLE = True
        logger.info("Win32 module loaded successfully")
except ImportError as e:
    WIN32_ERROR = str(e)
    logger.warning(f"Win32 not available: {e}. Install: pip install pywin32")


# ==============================================================================
# ELEVATION / PERMISSIONS CHECKER
# ==============================================================================

class PermissionChecker:
    """Check and report permission status for various operations"""
    
    @staticmethod
    def is_admin() -> bool:
        """Check if running with admin privileges"""
        if not IS_WINDOWS:
            return os.geteuid() == 0 if hasattr(os, 'geteuid') else False
        
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            return False
    
    @staticmethod
    def get_elevation_status() -> Dict[str, Any]:
        """Get detailed elevation status"""
        is_admin = PermissionChecker.is_admin()
        
        return {
            'is_admin': is_admin,
            'can_manage_spooler': is_admin,
            'can_add_printer': is_admin,
            'can_install_driver': is_admin,
            'can_modify_ports': is_admin,
            'can_view_printers': True,  # Always allowed
            'can_view_jobs': True,
            'can_cancel_own_jobs': True,
            'can_cancel_all_jobs': is_admin,
            'message': "Running as Administrator" if is_admin else "Running as standard user (some features limited)"
        }
    
    @staticmethod
    def require_admin(operation: str) -> Tuple[bool, str]:
        """
        Check if admin required for operation.
        Returns (allowed, message)
        """
        if PermissionChecker.is_admin():
            return True, "OK"
        
        admin_operations = [
            'add_printer', 'remove_printer', 'install_driver', 'remove_driver',
            'stop_spooler', 'start_spooler', 'restart_spooler', 'clear_spool',
            'add_port', 'remove_port', 'cancel_all_jobs'
        ]
        
        if operation in admin_operations:
            return False, f"Operation '{operation}' requires administrator privileges. Run as Administrator."
        
        return True, "OK"


# ==============================================================================
# DIAGNOSTICS
# ==============================================================================

@dataclass
class DiagnosticsResult:
    """System diagnostics result"""
    platform: str
    is_windows: bool
    is_admin: bool
    python_version: str
    
    # Module availability
    wmi_available: bool
    wmi_error: Optional[str]
    win32_available: bool
    win32_error: Optional[str]
    
    # PowerShell
    powershell_path: Optional[str]
    powershell_version: Optional[str]
    powershell_available: bool
    
    # SNMP tools
    snmpget_path: Optional[str]
    snmpwalk_path: Optional[str]
    snmp_available: bool
    
    # Spooler
    spooler_running: bool
    spooler_path: Optional[str]
    
    # Summary
    local_printer_support: str  # "full", "partial", "none"
    network_printer_support: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'platform': self.platform,
            'is_windows': self.is_windows,
            'is_admin': self.is_admin,
            'python_version': self.python_version,
            'wmi': {'available': self.wmi_available, 'error': self.wmi_error},
            'win32': {'available': self.win32_available, 'error': self.win32_error},
            'powershell': {
                'available': self.powershell_available,
                'path': self.powershell_path,
                'version': self.powershell_version
            },
            'snmp': {
                'available': self.snmp_available,
                'snmpget': self.snmpget_path,
                'snmpwalk': self.snmpwalk_path
            },
            'spooler': {
                'running': self.spooler_running,
                'path': self.spooler_path
            },
            'support': {
                'local_printers': self.local_printer_support,
                'network_printers': self.network_printer_support
            }
        }
    
    def to_text(self) -> str:
        """Generate human-readable diagnostics report"""
        lines = [
            "=" * 60,
            "PRINTER MANAGEMENT SUITE - DIAGNOSTICS",
            "=" * 60,
            "",
            "SYSTEM:",
            f"  Platform: {self.platform}",
            f"  Python: {self.python_version}",
            f"  Administrator: {'Yes ✓' if self.is_admin else 'No ✗ (some features limited)'}",
            "",
            "WINDOWS MODULES:",
            f"  WMI: {'Available ✓' if self.wmi_available else f'Not available ✗ ({self.wmi_error})'}",
            f"  Win32 (pywin32): {'Available ✓' if self.win32_available else f'Not available ✗ ({self.win32_error})'}",
            "",
            "POWERSHELL:",
            f"  Available: {'Yes ✓' if self.powershell_available else 'No ✗'}",
            f"  Path: {self.powershell_path or 'Not found'}",
            f"  Version: {self.powershell_version or 'Unknown'}",
            "",
            "SNMP TOOLS:",
            f"  Available: {'Yes ✓' if self.snmp_available else 'No ✗'}",
            f"  snmpget: {self.snmpget_path or 'Not found'}",
            f"  snmpwalk: {self.snmpwalk_path or 'Not found'}",
            "",
            "PRINT SPOOLER:",
            f"  Running: {'Yes ✓' if self.spooler_running else 'No ✗'}",
            f"  Path: {self.spooler_path or 'Unknown'}",
            "",
            "SUPPORT LEVEL:",
            f"  Local Printers: {self.local_printer_support.upper()}",
            f"  Network Printers: {self.network_printer_support.upper()}",
            "",
            "=" * 60,
        ]
        
        # Add recommendations
        recommendations = []
        if not self.wmi_available:
            recommendations.append("• Install WMI support: pip install wmi pywin32")
        if not self.win32_available:
            recommendations.append("• Install Win32 support: pip install pywin32")
        if not self.snmp_available:
            recommendations.append("• Install SNMP tools for network printers: choco install net-snmp")
        if not self.is_admin:
            recommendations.append("• Run as Administrator for full functionality")
        
        if recommendations:
            lines.extend(["RECOMMENDATIONS:", *recommendations, ""])
        
        return "\n".join(lines)


class SystemDiagnostics:
    """Run system diagnostics"""
    
    @staticmethod
    def run() -> DiagnosticsResult:
        """Run full diagnostics"""
        import shutil
        import sys
        
        # PowerShell
        ps_path = None
        ps_version = None
        ps_available = False
        
        if IS_WINDOWS:
            ps_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                   r"System32\WindowsPowerShell\v1.0\powershell.exe")
            if not os.path.exists(ps_path):
                ps_path = shutil.which("pwsh") or shutil.which("powershell")
            
            if ps_path and os.path.exists(ps_path):
                ps_available = True
                try:
                    result = subprocess.run(
                        [ps_path, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        ps_version = result.stdout.strip()
                except:
                    pass
        
        # SNMP tools
        snmpget_path = shutil.which("snmpget")
        snmpwalk_path = shutil.which("snmpwalk")
        snmp_available = snmpget_path is not None or snmpwalk_path is not None
        
        # Spooler status
        spooler_running = False
        spooler_path = None
        
        if IS_WINDOWS:
            spooler_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                        r"System32\spoolsv.exe")
            try:
                result = subprocess.run(
                    ["sc", "query", "spooler"],
                    capture_output=True, text=True, timeout=5
                )
                spooler_running = "RUNNING" in result.stdout
            except:
                pass
        
        # Determine support levels
        if IS_WINDOWS and (WMI_AVAILABLE or WIN32_AVAILABLE or ps_available):
            if WMI_AVAILABLE and WIN32_AVAILABLE:
                local_support = "full"
            elif WMI_AVAILABLE or WIN32_AVAILABLE:
                local_support = "partial"
            else:
                local_support = "basic"
        else:
            local_support = "none" if not IS_WINDOWS else "basic"
        
        network_support = "full" if snmp_available else "basic"
        
        return DiagnosticsResult(
            platform=platform.system(),
            is_windows=IS_WINDOWS,
            is_admin=PermissionChecker.is_admin(),
            python_version=sys.version.split()[0],
            wmi_available=WMI_AVAILABLE,
            wmi_error=WMI_ERROR,
            win32_available=WIN32_AVAILABLE,
            win32_error=WIN32_ERROR,
            powershell_path=ps_path,
            powershell_version=ps_version,
            powershell_available=ps_available,
            snmpget_path=snmpget_path,
            snmpwalk_path=snmpwalk_path,
            snmp_available=snmp_available,
            spooler_running=spooler_running,
            spooler_path=spooler_path,
            local_printer_support=local_support,
            network_printer_support=network_support
        )


# ==============================================================================
# DATA MODELS
# ==============================================================================

class LocalPrinterStatus(Enum):
    """Windows printer status codes"""
    READY = "Ready"
    PAUSED = "Paused"
    ERROR = "Error"
    PENDING_DELETION = "Pending Deletion"
    PAPER_JAM = "Paper Jam"
    PAPER_OUT = "Paper Out"
    MANUAL_FEED = "Manual Feed"
    PAPER_PROBLEM = "Paper Problem"
    OFFLINE = "Offline"
    IO_ACTIVE = "I/O Active"
    BUSY = "Busy"
    PRINTING = "Printing"
    OUTPUT_BIN_FULL = "Output Bin Full"
    NOT_AVAILABLE = "Not Available"
    WAITING = "Waiting"
    PROCESSING = "Processing"
    INITIALIZING = "Initializing"
    WARMING_UP = "Warming Up"
    TONER_LOW = "Toner Low"
    NO_TONER = "No Toner"
    PAGE_PUNT = "Page Punt"
    USER_INTERVENTION = "User Intervention"
    OUT_OF_MEMORY = "Out of Memory"
    DOOR_OPEN = "Door Open"
    SERVER_UNKNOWN = "Server Unknown"
    POWER_SAVE = "Power Save"
    UNKNOWN = "Unknown"


class PrinterType(Enum):
    """Printer connection type"""
    LOCAL = "Local"
    NETWORK = "Network"
    SHARED = "Shared"
    USB = "USB"
    VIRTUAL = "Virtual"
    FAX = "Fax"
    UNKNOWN = "Unknown"


class JobStatus(Enum):
    """Print job status"""
    PAUSED = "Paused"
    ERROR = "Error"
    DELETING = "Deleting"
    SPOOLING = "Spooling"
    PRINTING = "Printing"
    OFFLINE = "Offline"
    PAPEROUT = "Paper Out"
    PRINTED = "Printed"
    DELETED = "Deleted"
    BLOCKED = "Blocked"
    USER_INTERVENTION = "User Intervention"
    RESTART = "Restart"
    COMPLETE = "Complete"
    RETAINED = "Retained"
    UNKNOWN = "Unknown"


@dataclass
class LocalPrinterInfo:
    """Information about a local Windows printer"""
    name: str
    port_name: str
    driver_name: str
    printer_type: PrinterType
    status: LocalPrinterStatus
    status_code: int = 0
    is_default: bool = False
    is_shared: bool = False
    share_name: Optional[str] = None
    location: Optional[str] = None
    comment: Optional[str] = None
    
    # Hardware info
    device_id: Optional[str] = None
    hardware_id: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    
    # Capabilities
    is_color: bool = False
    is_duplex: bool = False
    max_resolution_dpi: int = 0
    supported_paper_sizes: List[str] = field(default_factory=list)
    
    # Statistics
    jobs_count: int = 0
    total_pages_printed: int = 0
    total_jobs_printed: int = 0
    
    # Timestamps
    last_used: Optional[datetime] = None
    driver_date: Optional[datetime] = None
    
    # Data source
    data_source: str = "unknown"  # "wmi", "win32", "powershell"
    
    # Raw data
    raw_wmi_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'port_name': self.port_name,
            'driver_name': self.driver_name,
            'printer_type': self.printer_type.value,
            'status': self.status.value,
            'status_code': self.status_code,
            'is_default': self.is_default,
            'is_shared': self.is_shared,
            'share_name': self.share_name,
            'location': self.location,
            'manufacturer': self.manufacturer,
            'model': self.model,
            'is_color': self.is_color,
            'is_duplex': self.is_duplex,
            'jobs_count': self.jobs_count,
            'total_pages_printed': self.total_pages_printed,
            'data_source': self.data_source
        }


@dataclass
class PrintJob:
    """Information about a print job"""
    job_id: int
    printer_name: str
    document_name: str
    user_name: str
    status: JobStatus
    status_code: int = 0
    priority: int = 1
    position: int = 0
    
    # Size info
    total_pages: int = 0
    pages_printed: int = 0
    size_bytes: int = 0
    
    # Timestamps
    submitted: Optional[datetime] = None
    start_time: Optional[datetime] = None
    
    # Additional
    host_name: Optional[str] = None
    notify_name: Optional[str] = None
    data_type: str = "RAW"
    
    # Data source
    data_source: str = "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'job_id': self.job_id,
            'printer_name': self.printer_name,
            'document_name': self.document_name,
            'user_name': self.user_name,
            'status': self.status.value,
            'total_pages': self.total_pages,
            'pages_printed': self.pages_printed,
            'size_bytes': self.size_bytes,
            'submitted': self.submitted.isoformat() if self.submitted else None,
            'data_source': self.data_source
        }


@dataclass
class PrinterDriver:
    """Information about a printer driver"""
    name: str
    version: str
    manufacturer: str
    hardware_id: Optional[str] = None
    inf_path: Optional[str] = None
    driver_path: Optional[str] = None
    config_file: Optional[str] = None
    data_file: Optional[str] = None
    help_file: Optional[str] = None
    supported_platforms: List[str] = field(default_factory=list)
    is_package_aware: bool = False
    driver_date: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'version': self.version,
            'manufacturer': self.manufacturer,
            'hardware_id': self.hardware_id,
            'inf_path': self.inf_path,
            'driver_date': self.driver_date.isoformat() if self.driver_date else None
        }


@dataclass
class SpoolerStatus:
    """Windows Print Spooler status"""
    is_running: bool
    status: str
    start_type: str  # Automatic, Manual, Disabled
    pid: Optional[int] = None
    memory_usage_mb: float = 0.0
    cpu_percent: float = 0.0
    uptime_seconds: int = 0
    
    # Spool folder info
    spool_folder: str = r"C:\Windows\System32\spool\PRINTERS"
    spool_files_count: int = 0
    spool_size_mb: float = 0.0
    spool_files: List[Dict[str, Any]] = field(default_factory=list)
    
    # Health
    health_status: str = "unknown"  # "healthy", "warning", "error"
    health_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'is_running': self.is_running,
            'status': self.status,
            'start_type': self.start_type,
            'pid': self.pid,
            'memory_usage_mb': self.memory_usage_mb,
            'spool_files_count': self.spool_files_count,
            'spool_size_mb': self.spool_size_mb,
            'health_status': self.health_status,
            'health_message': self.health_message
        }


@dataclass
class PrinterPort:
    """Printer port information"""
    name: str
    port_type: str  # Local, TCP/IP, USB, LPT, FILE
    description: str = ""
    host_address: Optional[str] = None
    port_number: int = 9100
    protocol: str = "RAW"  # RAW, LPR
    snmp_enabled: bool = False
    snmp_community: str = "public"
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'port_type': self.port_type,
            'host_address': self.host_address,
            'port_number': self.port_number,
            'protocol': self.protocol
        }


@dataclass
class OperationResult:
    """Result of a printer operation"""
    success: bool
    operation: str
    target: str
    message: str
    details: Optional[str] = None
    requires_admin: bool = False
    timestamp: datetime = field(default_factory=datetime.now)


# ==============================================================================
# STATUS CODE PARSER
# ==============================================================================

class WindowsStatusParser:
    """Parse Windows printer status codes"""
    
    # Status bit flags (from Windows SDK)
    STATUS_FLAGS = {
        0x00000001: LocalPrinterStatus.PAUSED,
        0x00000002: LocalPrinterStatus.ERROR,
        0x00000004: LocalPrinterStatus.PENDING_DELETION,
        0x00000008: LocalPrinterStatus.PAPER_JAM,
        0x00000010: LocalPrinterStatus.PAPER_OUT,
        0x00000020: LocalPrinterStatus.MANUAL_FEED,
        0x00000040: LocalPrinterStatus.PAPER_PROBLEM,
        0x00000080: LocalPrinterStatus.OFFLINE,
        0x00000100: LocalPrinterStatus.IO_ACTIVE,
        0x00000200: LocalPrinterStatus.BUSY,
        0x00000400: LocalPrinterStatus.PRINTING,
        0x00000800: LocalPrinterStatus.OUTPUT_BIN_FULL,
        0x00001000: LocalPrinterStatus.NOT_AVAILABLE,
        0x00002000: LocalPrinterStatus.WAITING,
        0x00004000: LocalPrinterStatus.PROCESSING,
        0x00008000: LocalPrinterStatus.INITIALIZING,
        0x00010000: LocalPrinterStatus.WARMING_UP,
        0x00020000: LocalPrinterStatus.TONER_LOW,
        0x00040000: LocalPrinterStatus.NO_TONER,
        0x00080000: LocalPrinterStatus.PAGE_PUNT,
        0x00100000: LocalPrinterStatus.USER_INTERVENTION,
        0x00200000: LocalPrinterStatus.OUT_OF_MEMORY,
        0x00400000: LocalPrinterStatus.DOOR_OPEN,
        0x00800000: LocalPrinterStatus.SERVER_UNKNOWN,
        0x01000000: LocalPrinterStatus.POWER_SAVE,
    }
    
    # Job status flags
    JOB_STATUS_FLAGS = {
        0x00000001: JobStatus.PAUSED,
        0x00000002: JobStatus.ERROR,
        0x00000004: JobStatus.DELETING,
        0x00000008: JobStatus.SPOOLING,
        0x00000010: JobStatus.PRINTING,
        0x00000020: JobStatus.OFFLINE,
        0x00000040: JobStatus.PAPEROUT,
        0x00000080: JobStatus.PRINTED,
        0x00000100: JobStatus.DELETED,
        0x00000200: JobStatus.BLOCKED,
        0x00000400: JobStatus.USER_INTERVENTION,
        0x00000800: JobStatus.RESTART,
        0x00001000: JobStatus.COMPLETE,
        0x00002000: JobStatus.RETAINED,
    }
    
    @classmethod
    def parse_printer_status(cls, status_code: int) -> Tuple[LocalPrinterStatus, List[LocalPrinterStatus]]:
        """Parse printer status code into primary status and list of all active statuses."""
        if status_code == 0:
            return LocalPrinterStatus.READY, [LocalPrinterStatus.READY]
        
        active_statuses = []
        for flag, status in cls.STATUS_FLAGS.items():
            if status_code & flag:
                active_statuses.append(status)
        
        if not active_statuses:
            return LocalPrinterStatus.UNKNOWN, [LocalPrinterStatus.UNKNOWN]
        
        # Priority order for primary status
        priority_order = [
            LocalPrinterStatus.ERROR,
            LocalPrinterStatus.OFFLINE,
            LocalPrinterStatus.PAPER_JAM,
            LocalPrinterStatus.PAPER_OUT,
            LocalPrinterStatus.NO_TONER,
            LocalPrinterStatus.TONER_LOW,
            LocalPrinterStatus.DOOR_OPEN,
            LocalPrinterStatus.USER_INTERVENTION,
            LocalPrinterStatus.PAUSED,
            LocalPrinterStatus.PRINTING,
            LocalPrinterStatus.BUSY,
            LocalPrinterStatus.PROCESSING,
        ]
        
        for status in priority_order:
            if status in active_statuses:
                return status, active_statuses
        
        return active_statuses[0], active_statuses
    
    @classmethod
    def parse_job_status(cls, status_code: int) -> Tuple[JobStatus, List[JobStatus]]:
        """Parse job status code"""
        if status_code == 0:
            return JobStatus.UNKNOWN, []
        
        active_statuses = []
        for flag, status in cls.JOB_STATUS_FLAGS.items():
            if status_code & flag:
                active_statuses.append(status)
        
        if not active_statuses:
            return JobStatus.UNKNOWN, []
        
        return active_statuses[0], active_statuses
    
    @classmethod
    def get_status_description(cls, status: LocalPrinterStatus) -> str:
        """Get human-readable description for status"""
        descriptions = {
            LocalPrinterStatus.READY: "Printer is ready",
            LocalPrinterStatus.PAUSED: "Printing is paused",
            LocalPrinterStatus.ERROR: "Printer has an error",
            LocalPrinterStatus.OFFLINE: "Printer is offline",
            LocalPrinterStatus.PAPER_JAM: "Paper jam detected",
            LocalPrinterStatus.PAPER_OUT: "Out of paper",
            LocalPrinterStatus.TONER_LOW: "Toner is low",
            LocalPrinterStatus.NO_TONER: "No toner",
            LocalPrinterStatus.DOOR_OPEN: "Printer door is open",
            LocalPrinterStatus.PRINTING: "Currently printing",
            LocalPrinterStatus.BUSY: "Printer is busy",
            LocalPrinterStatus.POWER_SAVE: "In power save mode",
            LocalPrinterStatus.USER_INTERVENTION: "User intervention required",
        }
        return descriptions.get(status, status.value)


# ==============================================================================
# POWERSHELL AGENT (Enhanced with logging)
# ==============================================================================

class PowerShellAgent:
    """Agent for PowerShell-based printer operations with detailed logging"""
    
    def __init__(self):
        self.ps_exe = self._find_powershell()
        self.available = self.ps_exe is not None and os.path.exists(self.ps_exe)
        
        if self.available:
            logger.info(f"PowerShellAgent initialized: {self.ps_exe}")
        else:
            logger.warning("PowerShellAgent: PowerShell not found")
    
    def _find_powershell(self) -> Optional[str]:
        """Find PowerShell executable"""
        import shutil
        
        if IS_WINDOWS:
            # Try Windows PowerShell 5.1 first
            ps51 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                               r"System32\WindowsPowerShell\v1.0\powershell.exe")
            if os.path.exists(ps51):
                return ps51
            
            # Try PowerShell Core
            pwsh = shutil.which("pwsh")
            if pwsh:
                return pwsh
        
        return shutil.which("powershell")
    
    def run(self, command: str, timeout: int = 30, log_command: bool = True) -> Tuple[bool, str, str]:
        """
        Run PowerShell command.
        Returns: (success, stdout, stderr)
        """
        if not self.available:
            return False, "", "PowerShell not available"
        
        # Log command (sanitize sensitive data)
        if log_command:
            safe_cmd = self._sanitize_command(command)
            logger.debug(f"PS Execute: {safe_cmd[:200]}{'...' if len(safe_cmd) > 200 else ''}")
        
        try:
            result = subprocess.run(
                [self.ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            success = result.returncode == 0
            
            if not success and result.stderr:
                logger.debug(f"PS Error: {result.stderr[:200]}")
            
            return success, result.stdout.strip(), result.stderr.strip()
            
        except subprocess.TimeoutExpired:
            logger.warning(f"PS Timeout after {timeout}s")
            return False, "", f"Timeout after {timeout}s"
        except Exception as e:
            logger.error(f"PS Exception: {e}")
            return False, "", str(e)
    
    def _sanitize_command(self, command: str) -> str:
        """Remove sensitive data from command for logging"""
        # Remove passwords and keys
        sanitized = re.sub(r'-Password\s+["\']?[^"\'>\s]+["\']?', '-Password ***', command)
        sanitized = re.sub(r'-Credential\s+[^\s]+', '-Credential ***', sanitized)
        return sanitized
    
    def run_json(self, command: str, timeout: int = 30) -> Tuple[bool, Any]:
        """Run PowerShell command and parse JSON output"""
        json_command = f"{command} | ConvertTo-Json -Depth 5 -Compress"
        success, stdout, stderr = self.run(json_command, timeout, log_command=True)
        
        if success and stdout:
            try:
                # Handle empty arrays
                if stdout == "" or stdout == "null":
                    return True, []
                data = json.loads(stdout)
                return True, data
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse error: {e}, output: {stdout[:100]}")
                return False, None
        
        return False, None
    
    # ==========================================================================
    # PRINTER QUERIES
    # ==========================================================================
    
    def get_printers(self) -> List[Dict[str, Any]]:
        """Get all printers via Get-Printer"""
        logger.debug("PS: Getting printers list")
        success, data = self.run_json("Get-Printer | Select-Object Name, PortName, DriverName, PrinterStatus, Shared, ShareName, Location, Comment")
        
        if success:
            if isinstance(data, dict):
                return [data]
            return data or []
        return []
    
    def get_printer_details(self, name: str) -> Optional[Dict[str, Any]]:
        """Get detailed printer info"""
        logger.debug(f"PS: Getting details for '{name}'")
        success, data = self.run_json(f'Get-Printer -Name "{name}" | Select-Object *')
        return data if success else None
    
    def get_printer_ports(self) -> List[Dict[str, Any]]:
        """Get all printer ports"""
        logger.debug("PS: Getting printer ports")
        success, data = self.run_json("Get-PrinterPort | Select-Object Name, Description, PrinterHostAddress, PortNumber, Protocol, SNMPEnabled, SNMPCommunity")
        
        if success:
            if isinstance(data, dict):
                return [data]
            return data or []
        return []
    
    def get_printer_drivers(self) -> List[Dict[str, Any]]:
        """Get all printer drivers"""
        logger.debug("PS: Getting printer drivers")
        success, data = self.run_json("Get-PrinterDriver | Select-Object Name, Manufacturer, DriverVersion, PrinterEnvironment")
        
        if success:
            if isinstance(data, dict):
                return [data]
            return data or []
        return []
    
    def get_print_jobs(self, printer_name: str = None) -> List[Dict[str, Any]]:
        """Get print jobs"""
        if printer_name:
            logger.debug(f"PS: Getting jobs for '{printer_name}'")
            cmd = f'Get-PrintJob -PrinterName "{printer_name}" -ErrorAction SilentlyContinue | Select-Object Id, PrinterName, DocumentName, UserName, JobStatus, TotalPages, PagesPrinted, Size, SubmittedTime'
        else:
            logger.debug("PS: Getting all print jobs")
            cmd = "Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue } | Select-Object Id, PrinterName, DocumentName, UserName, JobStatus, TotalPages, PagesPrinted, Size, SubmittedTime"
        
        success, data = self.run_json(cmd)
        
        if success:
            if isinstance(data, dict):
                return [data]
            return data or []
        return []
    
    def get_spooler_status(self) -> Dict[str, Any]:
        """Get spooler service status"""
        logger.debug("PS: Getting spooler status")
        success, data = self.run_json(
            "Get-Service -Name Spooler | Select-Object Name, Status, StartType"
        )
        return data if success else {}
    
    # ==========================================================================
    # PRINTER OPERATIONS
    # ==========================================================================
    
    def add_printer_port(self, name: str, host_address: str, 
                         port_number: int = 9100, protocol: str = "Raw") -> OperationResult:
        """Add TCP/IP printer port"""
        allowed, msg = PermissionChecker.require_admin('add_port')
        if not allowed:
            return OperationResult(False, "add_port", name, msg, requires_admin=True)
        
        logger.info(f"PS: Adding port '{name}' -> {host_address}:{port_number}")
        
        cmd = f'Add-PrinterPort -Name "{name}" -PrinterHostAddress "{host_address}" -PortNumber {port_number} -ErrorAction Stop'
        success, stdout, stderr = self.run(cmd)
        
        if success:
            logger.info(f"PS: Port '{name}' added successfully")
            return OperationResult(True, "add_port", name, "Port created")
        else:
            logger.error(f"PS: Failed to add port: {stderr}")
            return OperationResult(False, "add_port", name, f"Failed: {stderr[:100]}")
    
    def add_printer(self, name: str, driver_name: str, port_name: str,
                    shared: bool = False, share_name: str = None) -> OperationResult:
        """Add a printer"""
        allowed, msg = PermissionChecker.require_admin('add_printer')
        if not allowed:
            return OperationResult(False, "add_printer", name, msg, requires_admin=True)
        
        logger.info(f"PS: Adding printer '{name}' with driver '{driver_name}'")
        
        cmd = f'Add-Printer -Name "{name}" -DriverName "{driver_name}" -PortName "{port_name}"'
        
        if shared:
            share = share_name or name
            cmd += f' -Shared -ShareName "{share}"'
        
        success, stdout, stderr = self.run(cmd)
        
        if success:
            logger.info(f"PS: Printer '{name}' added successfully")
            return OperationResult(True, "add_printer", name, "Printer added")
        else:
            logger.error(f"PS: Failed to add printer: {stderr}")
            return OperationResult(False, "add_printer", name, f"Failed: {stderr[:100]}")
    
    def remove_printer(self, name: str) -> OperationResult:
        """Remove a printer"""
        allowed, msg = PermissionChecker.require_admin('remove_printer')
        if not allowed:
            return OperationResult(False, "remove_printer", name, msg, requires_admin=True)
        
        logger.info(f"PS: Removing printer '{name}'")
        success, _, stderr = self.run(f'Remove-Printer -Name "{name}" -ErrorAction Stop')
        
        if success:
            return OperationResult(True, "remove_printer", name, "Printer removed")
        else:
            return OperationResult(False, "remove_printer", name, f"Failed: {stderr[:100]}")
    
    def remove_printer_port(self, name: str) -> OperationResult:
        """Remove a printer port"""
        allowed, msg = PermissionChecker.require_admin('remove_port')
        if not allowed:
            return OperationResult(False, "remove_port", name, msg, requires_admin=True)
        
        logger.info(f"PS: Removing port '{name}'")
        success, _, _ = self.run(f'Remove-PrinterPort -Name "{name}" -ErrorAction SilentlyContinue')
        return OperationResult(success, "remove_port", name, "Port removed" if success else "Failed")
    
    def set_default_printer(self, name: str) -> OperationResult:
        """Set default printer"""
        logger.info(f"PS: Setting default printer '{name}'")
        
        # Use WMI via PowerShell
        cmd = f'''
$printer = Get-CimInstance -ClassName Win32_Printer | Where-Object {{ $_.Name -eq "{name}" }}
if ($printer) {{
    Invoke-CimMethod -InputObject $printer -MethodName SetDefaultPrinter | Out-Null
    "OK"
}} else {{
    "NotFound"
}}
        '''
        success, stdout, stderr = self.run(cmd)
        
        if success and "OK" in stdout:
            return OperationResult(True, "set_default", name, "Set as default")
        else:
            return OperationResult(False, "set_default", name, f"Failed: {stderr[:100] if stderr else 'Printer not found'}")
    
    def pause_printer(self, name: str) -> OperationResult:
        """Pause a printer"""
        logger.info(f"PS: Pausing printer '{name}'")
        
        cmd = f'''
$printer = Get-CimInstance -ClassName Win32_Printer | Where-Object {{ $_.Name -eq "{name}" }}
if ($printer) {{ Invoke-CimMethod -InputObject $printer -MethodName Pause | Out-Null; "OK" }}
        '''
        success, stdout, _ = self.run(cmd)
        return OperationResult("OK" in stdout, "pause", name, "Paused" if "OK" in stdout else "Failed")
    
    def resume_printer(self, name: str) -> OperationResult:
        """Resume a printer"""
        logger.info(f"PS: Resuming printer '{name}'")
        
        cmd = f'''
$printer = Get-CimInstance -ClassName Win32_Printer | Where-Object {{ $_.Name -eq "{name}" }}
if ($printer) {{ Invoke-CimMethod -InputObject $printer -MethodName Resume | Out-Null; "OK" }}
        '''
        success, stdout, _ = self.run(cmd)
        return OperationResult("OK" in stdout, "resume", name, "Resumed" if "OK" in stdout else "Failed")
    
    def cancel_all_jobs(self, printer_name: str) -> OperationResult:
        """Cancel all print jobs. Returns count cancelled."""
        logger.info(f"PS: Cancelling all jobs for '{printer_name}'")
        
        cmd = f'''
$jobs = Get-PrintJob -PrinterName "{printer_name}" -ErrorAction SilentlyContinue
$count = 0
foreach ($job in $jobs) {{
    Remove-PrintJob -InputObject $job -ErrorAction SilentlyContinue
    $count++
}}
$count
        '''
        success, stdout, _ = self.run(cmd)
        
        try:
            count = int(stdout) if success and stdout else 0
            return OperationResult(True, "cancel_all_jobs", printer_name, f"Cancelled {count} jobs", str(count))
        except:
            return OperationResult(success, "cancel_all_jobs", printer_name, "Jobs cancelled")
    
    def cancel_job(self, printer_name: str, job_id: int) -> OperationResult:
        """Cancel specific print job"""
        logger.info(f"PS: Cancelling job {job_id} on '{printer_name}'")
        
        cmd = f'Remove-PrintJob -PrinterName "{printer_name}" -ID {job_id} -ErrorAction Stop'
        success, _, stderr = self.run(cmd)
        
        return OperationResult(success, "cancel_job", f"{printer_name}:{job_id}", 
                              "Cancelled" if success else f"Failed: {stderr[:50]}")
    
    def restart_job(self, printer_name: str, job_id: int) -> OperationResult:
        """Restart a print job"""
        logger.info(f"PS: Restarting job {job_id} on '{printer_name}'")
        
        cmd = f'Restart-PrintJob -PrinterName "{printer_name}" -ID {job_id} -ErrorAction Stop'
        success, _, stderr = self.run(cmd)
        
        return OperationResult(success, "restart_job", f"{printer_name}:{job_id}",
                              "Restarted" if success else f"Failed: {stderr[:50]}")
    
    # ==========================================================================
    # SPOOLER OPERATIONS
    # ==========================================================================
    
    def stop_spooler(self) -> OperationResult:
        """Stop print spooler"""
        allowed, msg = PermissionChecker.require_admin('stop_spooler')
        if not allowed:
            return OperationResult(False, "stop_spooler", "spooler", msg, requires_admin=True)
        
        logger.info("PS: Stopping spooler service")
        success, _, stderr = self.run("Stop-Service -Name Spooler -Force -ErrorAction Stop")
        
        return OperationResult(success, "stop_spooler", "spooler", 
                              "Stopped" if success else f"Failed: {stderr[:50]}")
    
    def start_spooler(self) -> OperationResult:
        """Start print spooler"""
        allowed, msg = PermissionChecker.require_admin('start_spooler')
        if not allowed:
            return OperationResult(False, "start_spooler", "spooler", msg, requires_admin=True)
        
        logger.info("PS: Starting spooler service")
        success, _, stderr = self.run("Start-Service -Name Spooler -ErrorAction Stop")
        
        return OperationResult(success, "start_spooler", "spooler",
                              "Started" if success else f"Failed: {stderr[:50]}")
    
    def restart_spooler(self) -> OperationResult:
        """Restart print spooler"""
        allowed, msg = PermissionChecker.require_admin('restart_spooler')
        if not allowed:
            return OperationResult(False, "restart_spooler", "spooler", msg, requires_admin=True)
        
        logger.info("PS: Restarting spooler service")
        success, _, stderr = self.run("Restart-Service -Name Spooler -Force -ErrorAction Stop")
        
        return OperationResult(success, "restart_spooler", "spooler",
                              "Restarted" if success else f"Failed: {stderr[:50]}")
    
    def clear_spool_folder(self) -> OperationResult:
        """Clear spool folder. Returns files deleted."""
        allowed, msg = PermissionChecker.require_admin('clear_spool')
        if not allowed:
            return OperationResult(False, "clear_spool", "spool", msg, requires_admin=True)
        
        logger.info("PS: Clearing spool folder")
        
        cmd = '''
Stop-Service -Name Spooler -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$path = "$env:WINDIR\\System32\\spool\\PRINTERS"
$count = 0
if (Test-Path $path) {
    $files = Get-ChildItem -Path $path -File -ErrorAction SilentlyContinue
    foreach ($file in $files) {
        Remove-Item -Path $file.FullName -Force -ErrorAction SilentlyContinue
        $count++
    }
}
Start-Service -Name Spooler -ErrorAction SilentlyContinue
$count
        '''
        success, stdout, stderr = self.run(cmd, timeout=30)
        
        try:
            count = int(stdout) if success and stdout.strip().isdigit() else 0
            return OperationResult(True, "clear_spool", "spool", f"Cleared {count} files", str(count))
        except:
            return OperationResult(success, "clear_spool", "spool", 
                                  "Cleared" if success else f"Failed: {stderr[:50]}")
    
    # ==========================================================================
    # DRIVER OPERATIONS
    # ==========================================================================
    
    def install_driver(self, inf_path: str) -> OperationResult:
        """Install printer driver from INF file"""
        allowed, msg = PermissionChecker.require_admin('install_driver')
        if not allowed:
            return OperationResult(False, "install_driver", inf_path, msg, requires_admin=True)
        
        logger.info(f"PS: Installing driver from '{inf_path}'")
        
        cmd = f'pnputil /add-driver "{inf_path}" /install'
        success, stdout, stderr = self.run(cmd, timeout=120)
        
        return OperationResult(success, "install_driver", inf_path,
                              "Installed" if success else f"Failed: {stderr[:100]}")
    
    def remove_driver(self, driver_name: str) -> OperationResult:
        """Remove printer driver"""
        allowed, msg = PermissionChecker.require_admin('remove_driver')
        if not allowed:
            return OperationResult(False, "remove_driver", driver_name, msg, requires_admin=True)
        
        logger.info(f"PS: Removing driver '{driver_name}'")
        
        cmd = f'Remove-PrinterDriver -Name "{driver_name}" -ErrorAction Stop'
        success, _, stderr = self.run(cmd)
        
        return OperationResult(success, "remove_driver", driver_name,
                              "Removed" if success else f"Failed: {stderr[:100]}")
    
    def get_available_drivers(self) -> List[str]:
        """Get list of available (installed) drivers"""
        success, data = self.run_json("Get-PrinterDriver | Select-Object -ExpandProperty Name")
        
        if success:
            if isinstance(data, str):
                return [data]
            return data or []
        return []


# ==============================================================================
# WMI AGENT (Enhanced with fallback)
# ==============================================================================

class WMIAgent:
    """Agent for WMI-based printer queries with thread safety and fallback"""
    
    def __init__(self):
        self._local = threading.local()
        self.available = WMI_AVAILABLE
        
        if self.available:
            logger.info("WMIAgent initialized")
        else:
            logger.warning(f"WMIAgent: WMI not available ({WMI_ERROR})")
    
    def _get_wmi(self):
        """Get thread-local WMI connection"""
        if not self.available:
            return None
        
        if not hasattr(self._local, 'wmi'):
            try:
                pythoncom.CoInitialize()
                self._local.wmi = wmi.WMI()
                logger.debug("WMI connection established for thread")
            except Exception as e:
                logger.error(f"WMI connection failed: {e}")
                return None
        
        return self._local.wmi
    
    def get_printers(self) -> List[LocalPrinterInfo]:
        """Get all printers via WMI"""
        if not self.available:
            logger.debug("WMI: Not available, returning empty list")
            return []
        
        try:
            c = self._get_wmi()
            if c is None:
                return []
            
            printers = []
            logger.debug("WMI: Querying Win32_Printer")
            
            for p in c.Win32_Printer():
                # Parse status
                status, _ = WindowsStatusParser.parse_printer_status(p.PrinterStatus or 0)
                
                # Determine printer type
                port = p.PortName or ""
                if "USB" in port.upper():
                    ptype = PrinterType.USB
                elif "LPT" in port.upper():
                    ptype = PrinterType.LOCAL
                elif p.Network:
                    ptype = PrinterType.NETWORK
                elif p.Local:
                    if any(x in (p.DriverName or "").upper() for x in ["XPS", "PDF", "ONENOTE", "FAX"]):
                        ptype = PrinterType.VIRTUAL
                    else:
                        ptype = PrinterType.LOCAL
                else:
                    ptype = PrinterType.UNKNOWN
                
                # Parse manufacturer from driver
                manufacturer = None
                driver = p.DriverName or ""
                for mfr in ["HP", "Canon", "Epson", "Brother", "Xerox", "Lexmark", 
                           "Samsung", "Ricoh", "Kyocera", "Konica", "Sharp", "OKI"]:
                    if mfr.lower() in driver.lower():
                        manufacturer = mfr
                        break
                
                printer = LocalPrinterInfo(
                    name=p.Name,
                    port_name=p.PortName or "",
                    driver_name=p.DriverName or "",
                    printer_type=ptype,
                    status=status,
                    status_code=p.PrinterStatus or 0,
                    is_default=bool(p.Default),
                    is_shared=bool(p.Shared),
                    share_name=p.ShareName,
                    location=p.Location,
                    comment=p.Comment,
                    device_id=p.DeviceID,
                    manufacturer=manufacturer,
                    is_color=bool(p.CapabilityDescriptions and 
                                 "Color" in str(p.CapabilityDescriptions)),
                    jobs_count=p.Jobs or 0,
                    data_source="wmi",
                    raw_wmi_data={
                        'Attributes': p.Attributes,
                        'Capabilities': p.Capabilities,
                        'DetectedErrorState': p.DetectedErrorState,
                        'ExtendedPrinterStatus': p.ExtendedPrinterStatus,
                        'HorizontalResolution': p.HorizontalResolution,
                        'VerticalResolution': p.VerticalResolution,
                    }
                )
                
                printers.append(printer)
            
            logger.debug(f"WMI: Found {len(printers)} printers")
            return printers
            
        except Exception as e:
            logger.error(f"WMI query failed: {e}")
            return []
    
    def get_print_jobs(self, printer_name: str = None) -> List[PrintJob]:
        """Get print jobs via WMI"""
        if not self.available:
            return []
        
        try:
            c = self._get_wmi()
            if c is None:
                return []
            
            jobs = []
            logger.debug(f"WMI: Querying Win32_PrintJob{' for ' + printer_name if printer_name else ''}")
            
            query = "SELECT * FROM Win32_PrintJob"
            if printer_name:
                query += f" WHERE Name LIKE '{printer_name},%'"
            
            for j in c.query(query):
                status, _ = WindowsStatusParser.parse_job_status(j.StatusMask or 0)
                
                # Parse submitted time
                submitted = None
                if j.TimeSubmitted:
                    try:
                        submitted = datetime.strptime(
                            j.TimeSubmitted.split('.')[0], 
                            "%Y%m%d%H%M%S"
                        )
                    except:
                        pass
                
                job = PrintJob(
                    job_id=j.JobId,
                    printer_name=printer_name or j.Name.split(',')[0] if j.Name else "",
                    document_name=j.Document or "",
                    user_name=j.Owner or "",
                    status=status,
                    status_code=j.StatusMask or 0,
                    priority=j.Priority or 1,
                    total_pages=j.TotalPages or 0,
                    pages_printed=j.PagesPrinted or 0,
                    size_bytes=j.Size or 0,
                    submitted=submitted,
                    host_name=j.HostPrintQueue,
                    data_type=j.DataType or "RAW",
                    data_source="wmi"
                )
                jobs.append(job)
            
            logger.debug(f"WMI: Found {len(jobs)} jobs")
            return jobs
            
        except Exception as e:
            logger.error(f"WMI job query failed: {e}")
            return []
    
    def get_printer_config(self, name: str) -> Dict[str, Any]:
        """Get detailed printer configuration"""
        if not self.available:
            return {}
        
        try:
            c = self._get_wmi()
            if c is None:
                return {}
            
            for p in c.Win32_PrinterConfiguration(Name=name):
                return {
                    'name': p.Name,
                    'paper_size': p.PaperSize,
                    'paper_length': p.PaperLength,
                    'paper_width': p.PaperWidth,
                    'scale': p.Scale,
                    'copies': p.Copies,
                    'color': p.Color,
                    'duplex': p.Duplex,
                    'orientation': p.Orientation,
                    'print_quality': p.PrintQuality,
                    'x_resolution': p.XResolution,
                    'y_resolution': p.YResolution,
                    'collate': p.Collate,
                }
            
            return {}
            
        except Exception as e:
            logger.error(f"WMI config query failed: {e}")
            return {}
    
    def get_spooler_process(self) -> Dict[str, Any]:
        """Get spooler process info"""
        if not self.available:
            return {}
        
        try:
            c = self._get_wmi()
            if c is None:
                return {}
            
            for p in c.Win32_Process(Name="spoolsv.exe"):
                return {
                    'pid': p.ProcessId,
                    'name': p.Name,
                    'handle_count': p.HandleCount,
                    'thread_count': p.ThreadCount,
                    'working_set_mb': (p.WorkingSetSize or 0) / 1024 / 1024,
                    'virtual_size_mb': (p.VirtualSize or 0) / 1024 / 1024,
                    'command_line': p.CommandLine,
                    'creation_date': p.CreationDate,
                }
            
            return {}
            
        except Exception as e:
            logger.error(f"WMI process query failed: {e}")
            return {}


# ==============================================================================
# WIN32 AGENT (Enhanced)
# ==============================================================================

class Win32Agent:
    """Agent using win32print API for direct Windows calls"""
    
    def __init__(self):
        self.available = WIN32_AVAILABLE
        
        if self.available:
            logger.info("Win32Agent initialized")
        else:
            logger.warning(f"Win32Agent: Not available ({WIN32_ERROR})")
    
    def get_printers(self) -> List[Dict[str, Any]]:
        """Get printers using win32print"""
        if not self.available:
            return []
        
        try:
            printers = []
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            
            logger.debug("Win32: Enumerating printers")
            
            for p in win32print.EnumPrinters(flags, None, 2):
                printers.append({
                    'name': p['pPrinterName'],
                    'server': p.get('pServerName'),
                    'share_name': p.get('pShareName'),
                    'port_name': p.get('pPortName'),
                    'driver_name': p.get('pDriverName'),
                    'comment': p.get('pComment'),
                    'location': p.get('pLocation'),
                    'status': p.get('Status', 0),
                    'jobs': p.get('cJobs', 0),
                    'attributes': p.get('Attributes', 0),
                    'priority': p.get('Priority', 0),
                    'default_priority': p.get('DefaultPriority', 0),
                })
            
            logger.debug(f"Win32: Found {len(printers)} printers")
            return printers
            
        except Exception as e:
            logger.error(f"Win32 EnumPrinters failed: {e}")
            return []
    
    def get_default_printer(self) -> Optional[str]:
        """Get default printer name"""
        if not self.available:
            return None
        
        try:
            return win32print.GetDefaultPrinter()
        except:
            return None
    
    def set_default_printer(self, name: str) -> OperationResult:
        """Set default printer"""
        if not self.available:
            return OperationResult(False, "set_default", name, "Win32 not available")
        
        logger.info(f"Win32: Setting default printer '{name}'")
        
        try:
            win32print.SetDefaultPrinter(name)
            return OperationResult(True, "set_default", name, "Set as default")
        except Exception as e:
            logger.error(f"SetDefaultPrinter failed: {e}")
            return OperationResult(False, "set_default", name, str(e))
    
    def get_print_jobs(self, printer_name: str) -> List[Dict[str, Any]]:
        """Get print jobs for a printer"""
        if not self.available:
            return []
        
        try:
            handle = win32print.OpenPrinter(printer_name)
            try:
                jobs = win32print.EnumJobs(handle, 0, -1, 2)
                return [
                    {
                        'job_id': j['JobId'],
                        'printer_name': j.get('pPrinterName', printer_name),
                        'document': j.get('pDocument', ''),
                        'user': j.get('pUserName', ''),
                        'status': j.get('Status', 0),
                        'priority': j.get('Priority', 0),
                        'position': j.get('Position', 0),
                        'total_pages': j.get('TotalPages', 0),
                        'pages_printed': j.get('PagesPrinted', 0),
                        'size': j.get('Size', 0),
                        'submitted': j.get('Submitted'),
                    }
                    for j in jobs
                ]
            finally:
                win32print.ClosePrinter(handle)
                
        except Exception as e:
            logger.error(f"EnumJobs failed: {e}")
            return []
    
    def cancel_job(self, printer_name: str, job_id: int) -> OperationResult:
        """Cancel a print job"""
        if not self.available:
            return OperationResult(False, "cancel_job", f"{printer_name}:{job_id}", "Win32 not available")
        
        logger.info(f"Win32: Cancelling job {job_id} on '{printer_name}'")
        
        try:
            handle = win32print.OpenPrinter(printer_name)
            try:
                win32print.SetJob(handle, job_id, 0, None, win32print.JOB_CONTROL_DELETE)
                return OperationResult(True, "cancel_job", f"{printer_name}:{job_id}", "Cancelled")
            finally:
                win32print.ClosePrinter(handle)
        except Exception as e:
            logger.error(f"CancelJob failed: {e}")
            return OperationResult(False, "cancel_job", f"{printer_name}:{job_id}", str(e))
    
    def pause_printer(self, printer_name: str) -> OperationResult:
        """Pause printer"""
        if not self.available:
            return OperationResult(False, "pause", printer_name, "Win32 not available")
        
        logger.info(f"Win32: Pausing printer '{printer_name}'")
        
        try:
            handle = win32print.OpenPrinter(printer_name)
            try:
                win32print.SetPrinter(handle, 0, None, win32print.PRINTER_CONTROL_PAUSE)
                return OperationResult(True, "pause", printer_name, "Paused")
            finally:
                win32print.ClosePrinter(handle)
        except Exception as e:
            logger.error(f"PausePrinter failed: {e}")
            return OperationResult(False, "pause", printer_name, str(e))
    
    def resume_printer(self, printer_name: str) -> OperationResult:
        """Resume printer"""
        if not self.available:
            return OperationResult(False, "resume", printer_name, "Win32 not available")
        
        logger.info(f"Win32: Resuming printer '{printer_name}'")
        
        try:
            handle = win32print.OpenPrinter(printer_name)
            try:
                win32print.SetPrinter(handle, 0, None, win32print.PRINTER_CONTROL_RESUME)
                return OperationResult(True, "resume", printer_name, "Resumed")
            finally:
                win32print.ClosePrinter(handle)
        except Exception as e:
            logger.error(f"ResumePrinter failed: {e}")
            return OperationResult(False, "resume", printer_name, str(e))
    
    def purge_printer(self, printer_name: str) -> OperationResult:
        """Purge all jobs from printer"""
        if not self.available:
            return OperationResult(False, "purge", printer_name, "Win32 not available")
        
        logger.info(f"Win32: Purging printer '{printer_name}'")
        
        try:
            handle = win32print.OpenPrinter(printer_name)
            try:
                win32print.SetPrinter(handle, 0, None, win32print.PRINTER_CONTROL_PURGE)
                return OperationResult(True, "purge", printer_name, "Purged")
            finally:
                win32print.ClosePrinter(handle)
        except Exception as e:
            logger.error(f"PurgePrinter failed: {e}")
            return OperationResult(False, "purge", printer_name, str(e))
    
    def open_printer_queue(self, printer_name: str) -> OperationResult:
        """Open printer queue in Windows"""
        if not self.available or not IS_WINDOWS:
            return OperationResult(False, "open_queue", printer_name, "Not available")
        
        logger.info(f"Win32: Opening queue for '{printer_name}'")
        
        try:
            import subprocess
            subprocess.Popen(['rundll32', 'printui.dll,PrintUIEntry', '/o', '/n', printer_name])
            return OperationResult(True, "open_queue", printer_name, "Queue opened")
        except Exception as e:
            return OperationResult(False, "open_queue", printer_name, str(e))


# ==============================================================================
# LOCAL PRINTER SERVICE (Aggregates all agents with fallback)
# ==============================================================================

class LocalPrinterService:
    """
    Service that aggregates all Windows printer agents with automatic fallback.
    Provides unified API for local printer management.
    """
    
    def __init__(self):
        self.ps_agent = PowerShellAgent()
        self.wmi_agent = WMIAgent()
        self.win32_agent = Win32Agent()
        self._executor = ThreadPoolExecutor(max_workers=5)
        
        # Determine available methods
        self.wmi_available = self.wmi_agent.available
        self.win32_available = self.win32_agent.available
        self.ps_available = self.ps_agent.available
        
        # Log available methods
        logger.info("LocalPrinterService initialized")
        logger.info(f"  WMI: {'Available' if self.wmi_available else 'Not available'}")
        logger.info(f"  Win32: {'Available' if self.win32_available else 'Not available'}")
        logger.info(f"  PowerShell: {'Available' if self.ps_available else 'Not available'}")
        
        if not any([self.wmi_available, self.win32_available, self.ps_available]):
            logger.error("No printer agents available!")
    
    def shutdown(self):
        """Cleanup"""
        self._executor.shutdown(wait=False)
        logger.info("LocalPrinterService shutdown")

    def _restart_spooler_safe(self):
        """Attempt to restart spooler without throwing"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Restart-Service -Name spooler -Force"],
                timeout=10, capture_output=True
            )
        except Exception as e:
            logger.error(f"Spooler restart failed: {e}")
    
    def get_diagnostics(self) -> DiagnosticsResult:
        """Get system diagnostics"""
        return SystemDiagnostics.run()
    
    def get_permissions(self) -> Dict[str, Any]:
        """Get current permission status"""
        return PermissionChecker.get_elevation_status()
    
    # ==========================================================================
    # QUERIES (with fallback)
    # ==========================================================================
    
    def get_all_printers(self) -> List[LocalPrinterInfo]:
        """
        Get all local printers using best available method.
        Falls back automatically: WMI -> Win32 -> PowerShell
        """
        # Preflight: check if Spooler is running
        try:
            spooler = self.get_spooler_status()
            if not spooler.is_running:
                logger.warning("Spooler is not running; attempting auto-restart...")
                try:
                    self._restart_spooler_safe()
                    time.sleep(1.5)
                except Exception as e:
                    logger.error(f"Spooler auto-restart failed: {e}")
                    return []  # Return empty - UI should show banner
        except Exception:
            pass  # Spooler check itself failed, continue with best effort

        printers = {}
        data_source = "unknown"
        
        # Try WMI first (most detailed)
        if self.wmi_available:
            try:
                wmi_printers = self.wmi_agent.get_printers()
                if wmi_printers:
                    for p in wmi_printers:
                        printers[p.name] = p
                    data_source = "wmi"
                    logger.debug(f"Got {len(wmi_printers)} printers from WMI")
            except Exception as e:
                logger.warning(f"WMI fallback triggered: {e}")
        
        # Try Win32 if WMI failed or empty
        if not printers and self.win32_available:
            try:
                win32_printers = self.win32_agent.get_printers()
                if win32_printers:
                    for p in win32_printers:
                        name = p['name']
                        status, _ = WindowsStatusParser.parse_printer_status(p.get('status', 0))
                        
                        port = p.get('port_name', '')
                        if "USB" in port.upper():
                            ptype = PrinterType.USB
                        elif "LPT" in port.upper():
                            ptype = PrinterType.LOCAL
                        elif "\\\\" in name or p.get('server'):
                            ptype = PrinterType.NETWORK
                        else:
                            ptype = PrinterType.LOCAL
                        
                        printers[name] = LocalPrinterInfo(
                            name=name,
                            port_name=port,
                            driver_name=p.get('driver_name', ''),
                            printer_type=ptype,
                            status=status,
                            status_code=p.get('status', 0),
                            is_shared=bool(p.get('share_name')),
                            share_name=p.get('share_name'),
                            location=p.get('location'),
                            comment=p.get('comment'),
                            jobs_count=p.get('jobs', 0),
                            data_source="win32"
                        )
                    data_source = "win32"
                    logger.debug(f"Got {len(win32_printers)} printers from Win32")
            except Exception as e:
                logger.warning(f"Win32 fallback triggered: {e}")
        
        # Try PowerShell as last resort
        if not printers and self.ps_available:
            try:
                ps_printers = self.ps_agent.get_printers()
                if ps_printers:
                    for ps_p in ps_printers:
                        name = ps_p.get('Name')
                        if not name:
                            continue
                        
                        status, _ = WindowsStatusParser.parse_printer_status(
                            ps_p.get('PrinterStatus', 0)
                        )
                        
                        printers[name] = LocalPrinterInfo(
                            name=name,
                            port_name=ps_p.get('PortName', ''),
                            driver_name=ps_p.get('DriverName', ''),
                            printer_type=PrinterType.LOCAL,
                            status=status,
                            status_code=ps_p.get('PrinterStatus', 0),
                            is_shared=ps_p.get('Shared', False),
                            share_name=ps_p.get('ShareName'),
                            location=ps_p.get('Location'),
                            comment=ps_p.get('Comment'),
                            data_source="powershell"
                        )
                    data_source = "powershell"
                    logger.debug(f"Got {len(ps_printers)} printers from PowerShell")
            except Exception as e:
                logger.error(f"PowerShell query failed: {e}")
        
        # Get default printer
        default = None
        if self.win32_available:
            default = self.win32_agent.get_default_printer()
        
        if default and default in printers:
            printers[default].is_default = True
        
        logger.info(f"Total printers: {len(printers)} (source: {data_source})")
        return list(printers.values())
    
    def get_printer(self, name: str) -> Optional[LocalPrinterInfo]:
        """Get single printer by name"""
        for p in self.get_all_printers():
            if p.name == name:
                return p
        return None
    
    def get_all_jobs(self) -> List[PrintJob]:
        """Get all print jobs from all printers"""
        # Try WMI first
        if self.wmi_available:
            jobs = self.wmi_agent.get_print_jobs()
            if jobs:
                return jobs
        
        # Fallback to PowerShell
        if self.ps_available:
            jobs = []
            ps_jobs = self.ps_agent.get_print_jobs()
            
            for j in ps_jobs:
                if not j:
                    continue
                
                status, _ = WindowsStatusParser.parse_job_status(j.get('JobStatus', 0))
                
                jobs.append(PrintJob(
                    job_id=j.get('Id', 0),
                    printer_name=j.get('PrinterName', ''),
                    document_name=j.get('DocumentName', ''),
                    user_name=j.get('UserName', ''),
                    status=status,
                    status_code=j.get('JobStatus', 0),
                    total_pages=j.get('TotalPages', 0),
                    pages_printed=j.get('PagesPrinted', 0),
                    size_bytes=j.get('Size', 0),
                    data_source="powershell"
                ))
            
            return jobs
        
        # Fallback to Win32
        if self.win32_available:
            jobs = []
            for printer in self.get_all_printers():
                try:
                    printer_jobs = self.win32_agent.get_print_jobs(printer.name)
                    for j in printer_jobs:
                        status, _ = WindowsStatusParser.parse_job_status(j.get('status', 0))
                        jobs.append(PrintJob(
                            job_id=j['job_id'],
                            printer_name=printer.name,
                            document_name=j.get('document', ''),
                            user_name=j.get('user', ''),
                            status=status,
                            total_pages=j.get('total_pages', 0),
                            pages_printed=j.get('pages_printed', 0),
                            size_bytes=j.get('size', 0),
                            data_source="win32"
                        ))
                except:
                    continue
            return jobs
        
        return []
    
    def get_printer_jobs(self, printer_name: str) -> List[PrintJob]:
        """Get jobs for specific printer"""
        if self.wmi_available:
            return self.wmi_agent.get_print_jobs(printer_name)
        
        if self.ps_available:
            jobs = []
            ps_jobs = self.ps_agent.get_print_jobs(printer_name)
            
            for j in ps_jobs:
                if not j:
                    continue
                status, _ = WindowsStatusParser.parse_job_status(j.get('JobStatus', 0))
                
                jobs.append(PrintJob(
                    job_id=j.get('Id', 0),
                    printer_name=printer_name,
                    document_name=j.get('DocumentName', ''),
                    user_name=j.get('UserName', ''),
                    status=status,
                    total_pages=j.get('TotalPages', 0),
                    pages_printed=j.get('PagesPrinted', 0),
                    size_bytes=j.get('Size', 0),
                    data_source="powershell"
                ))
            return jobs
        
        return []
    
    def get_all_ports(self) -> List[PrinterPort]:
        """Get all printer ports"""
        ports = []
        
        if self.ps_available:
            ps_ports = self.ps_agent.get_printer_ports()
            
            for p in ps_ports:
                if not p:
                    continue
                
                port_type = "Unknown"
                name = p.get('Name', '')
                
                if 'USB' in name.upper():
                    port_type = "USB"
                elif 'LPT' in name.upper():
                    port_type = "LPT"
                elif name.startswith('IP_') or p.get('PrinterHostAddress'):
                    port_type = "TCP/IP"
                elif 'FILE' in name.upper() or 'PORTPROMPT' in name.upper():
                    port_type = "FILE"
                elif 'WSD' in name.upper():
                    port_type = "WSD"
                elif 'NUL' in name.upper():
                    port_type = "NULL"
                
                ports.append(PrinterPort(
                    name=name,
                    port_type=port_type,
                    description=p.get('Description', ''),
                    host_address=p.get('PrinterHostAddress'),
                    port_number=p.get('PortNumber', 9100),
                    protocol=p.get('Protocol', 'RAW'),
                    snmp_enabled=p.get('SNMPEnabled', False),
                    snmp_community=p.get('SNMPCommunity', 'public'),
                ))
        
        return ports
    
    def get_all_drivers(self) -> List[PrinterDriver]:
        """Get all installed printer drivers"""
        drivers = []
        
        if self.ps_available:
            ps_drivers = self.ps_agent.get_printer_drivers()
            
            for d in ps_drivers:
                if not d:
                    continue
                
                drivers.append(PrinterDriver(
                    name=d.get('Name', ''),
                    version=str(d.get('DriverVersion', '')),
                    manufacturer=d.get('Manufacturer', ''),
                    hardware_id=d.get('HardwareID'),
                    inf_path=d.get('InfPath'),
                    config_file=d.get('ConfigFile'),
                    data_file=d.get('DataFile'),
                ))
        
        return drivers
    
    def get_spooler_status(self) -> SpoolerStatus:
        """Get comprehensive spooler status"""
        # Get service status
        svc = {}
        if self.ps_available:
            svc = self.ps_agent.get_spooler_status()
        
        is_running = False
        if isinstance(svc.get('Status'), int):
            is_running = svc.get('Status') == 4
        elif isinstance(svc.get('Status'), str):
            is_running = svc.get('Status', '').lower() == 'running'
        
        start_type_map = {1: 'Disabled', 2: 'Manual', 3: 'Automatic', 4: 'Automatic (Delayed)'}
        start_type = start_type_map.get(svc.get('StartType'), str(svc.get('StartType', 'Unknown')))
        
        # Get process info
        proc = {}
        if self.wmi_available:
            proc = self.wmi_agent.get_spooler_process()
        
        # Get spool folder info
        spool_folder = os.path.join(os.environ.get('WINDIR', r'C:\Windows'),
                                    r'System32\spool\PRINTERS')
        spool_files = 0
        spool_size = 0.0
        spool_file_list = []
        
        if os.path.exists(spool_folder):
            try:
                for f in os.listdir(spool_folder):
                    fp = os.path.join(spool_folder, f)
                    if os.path.isfile(fp):
                        spool_files += 1
                        file_size = os.path.getsize(fp)
                        spool_size += file_size
                        spool_file_list.append({
                            'name': f,
                            'size': file_size,
                            'modified': datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()
                        })
            except:
                pass
        
        # Determine health
        health_status = "healthy"
        health_message = "Spooler is running normally"
        
        if not is_running:
            health_status = "error"
            health_message = "Spooler is not running"
        elif spool_files > 50:
            health_status = "warning"
            health_message = f"Large spool queue ({spool_files} files)"
        elif spool_size > 100 * 1024 * 1024:  # 100MB
            health_status = "warning"
            health_message = f"Large spool size ({spool_size/1024/1024:.1f} MB)"
        
        return SpoolerStatus(
            is_running=is_running,
            status="Running" if is_running else "Stopped",
            start_type=start_type,
            pid=proc.get('pid'),
            memory_usage_mb=proc.get('working_set_mb', 0),
            spool_folder=spool_folder,
            spool_files_count=spool_files,
            spool_size_mb=spool_size / 1024 / 1024,
            spool_files=spool_file_list[:20],  # Limit to 20
            health_status=health_status,
            health_message=health_message
        )
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all local printers"""
        printers = self.get_all_printers()
        jobs = self.get_all_jobs()
        spooler = self.get_spooler_status()
        
        # Count by type
        type_counts = {}
        for p in printers:
            t = p.printer_type.value
            type_counts[t] = type_counts.get(t, 0) + 1
        
        # Count by status
        status_counts = {}
        for p in printers:
            s = p.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        
        # Jobs by status
        job_status_counts = {}
        for j in jobs:
            s = j.status.value
            job_status_counts[s] = job_status_counts.get(s, 0) + 1
        
        return {
            'total_printers': len(printers),
            'total_jobs': len(jobs),
            'printers_by_type': type_counts,
            'printers_by_status': status_counts,
            'jobs_by_status': job_status_counts,
            'spooler': spooler.to_dict(),
            'default_printer': next((p.name for p in printers if p.is_default), None),
            'agents': {
                'wmi': self.wmi_available,
                'win32': self.win32_available,
                'powershell': self.ps_available
            }
        }
    
    # ==========================================================================
    # OPERATIONS (with automatic fallback)
    # ==========================================================================
    
    def add_network_printer(self, name: str, host_address: str,
                            driver_name: str, port_number: int = 9100) -> OperationResult:
        """Add a network printer"""
        port_name = f"IP_{host_address}"
        
        # Create port
        port_result = self.ps_agent.add_printer_port(port_name, host_address, port_number)
        if not port_result.success:
            if port_result.requires_admin:
                return port_result
            logger.warning(f"Port creation may have failed: {port_result.message}")
        
        # Add printer
        return self.ps_agent.add_printer(name, driver_name, port_name)
    
    def remove_printer(self, name: str, remove_port: bool = False) -> OperationResult:
        """Remove a printer"""
        printer = self.get_printer(name)
        if not printer:
            return OperationResult(False, "remove_printer", name, "Printer not found")
        
        result = self.ps_agent.remove_printer(name)
        
        if result.success and remove_port and printer.port_name.startswith('IP_'):
            self.ps_agent.remove_printer_port(printer.port_name)
        
        return result
    
    def set_default_printer(self, name: str) -> OperationResult:
        """Set default printer - try Win32 first, then PowerShell"""
        if self.win32_available:
            result = self.win32_agent.set_default_printer(name)
            if result.success:
                return result
        
        return self.ps_agent.set_default_printer(name)
    
    def pause_printer(self, name: str) -> OperationResult:
        """Pause printer - try Win32 first"""
        if self.win32_available:
            result = self.win32_agent.pause_printer(name)
            if result.success:
                return result
        
        return self.ps_agent.pause_printer(name)
    
    def resume_printer(self, name: str) -> OperationResult:
        """Resume printer - try Win32 first"""
        if self.win32_available:
            result = self.win32_agent.resume_printer(name)
            if result.success:
                return result
        
        return self.ps_agent.resume_printer(name)
    
    def cancel_job(self, printer_name: str, job_id: int) -> OperationResult:
        """Cancel a print job"""
        if self.win32_available:
            result = self.win32_agent.cancel_job(printer_name, job_id)
            if result.success:
                return result
        
        return self.ps_agent.cancel_job(printer_name, job_id)
    
    def cancel_all_jobs(self, printer_name: str) -> OperationResult:
        """Cancel all jobs for a printer"""
        if self.win32_available:
            result = self.win32_agent.purge_printer(printer_name)
            if result.success:
                return result
        
        return self.ps_agent.cancel_all_jobs(printer_name)
    
    def open_printer_queue(self, printer_name: str) -> OperationResult:
        """Open printer queue UI"""
        if self.win32_available:
            return self.win32_agent.open_printer_queue(printer_name)
        
        # Fallback to rundll32
        try:
            import subprocess
            subprocess.Popen(['rundll32', 'printui.dll,PrintUIEntry', '/o', '/n', printer_name])
            return OperationResult(True, "open_queue", printer_name, "Queue opened")
        except Exception as e:
            return OperationResult(False, "open_queue", printer_name, str(e))
    
    def restart_spooler(self) -> OperationResult:
        """Restart print spooler"""
        return self.ps_agent.restart_spooler()
    
    def clear_and_restart_spooler(self) -> Tuple[OperationResult, int]:
        """Clear spool folder and restart spooler"""
        result = self.ps_agent.clear_spool_folder()
        
        try:
            deleted = int(result.details) if result.details else 0
        except:
            deleted = 0
        
        if result.success:
            start_result = self.ps_agent.start_spooler()
            return start_result, deleted
        
        return result, deleted


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # Service
    'LocalPrinterService',
    
    # Data models
    'LocalPrinterInfo',
    'LocalPrinterStatus',
    'PrinterType',
    'PrintJob',
    'JobStatus',
    'PrinterDriver',
    'PrinterPort',
    'SpoolerStatus',
    'OperationResult',
    
    # Agents
    'PowerShellAgent',
    'WMIAgent',
    'Win32Agent',
    
    # Utilities
    'WindowsStatusParser',
    'PermissionChecker',
    'SystemDiagnostics',
    'DiagnosticsResult',
    
    # Availability flags
    'WMI_AVAILABLE',
    'WIN32_AVAILABLE',
    'IS_WINDOWS',
    'WMI_ERROR',
    'WIN32_ERROR',
]