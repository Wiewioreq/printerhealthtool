"""
Printer Management Suite - Pro Edition v6.1
With Windows Local Printer Agents

Modules:
- oid_registry.py - Vendor OID profiles and auto-detection
- snmp_engine.py - Async SNMP engine with ThreadPoolExecutor
- windows_agents.py - Windows local printer management (WMI, PowerShell, Win32)
- dashboard_template.html - Chart.js dashboard template
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import os
import sys
import yaml
import subprocess
import threading
from queue import Queue
from datetime import datetime, timedelta, time
import platform
import sqlite3
import json
import smtplib
import webbrowser
import tempfile
import shutil
import logging
from logging.handlers import RotatingFileHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Callable, Tuple
from enum import Enum

# ==============================================================================
# OPTIONAL IMPORTS
# ==============================================================================

# OID Registry module
try:
    from oid_registry import (
        OIDRegistry, VendorDetector, VendorOIDProfiles,
        Manufacturer, VendorProfile, StandardMIB, DetectionResult
    )
    OID_REGISTRY_AVAILABLE = True
except ImportError:
    OID_REGISTRY_AVAILABLE = False

# SNMP Engine module
try:
    from snmp_engine import (
        AsyncSNMPEngine, PrinterCheckResult, TonerLevel,
        PrinterStatus, SNMPDiscovery, SNMPHelper, create_snmp_engine
    )
    SNMP_ENGINE_AVAILABLE = True
except ImportError:
    SNMP_ENGINE_AVAILABLE = False

# Windows Agents module
try:
    from windows_agents import (
        LocalPrinterService, LocalPrinterInfo, LocalPrinterStatus,
        PrinterType, PrintJob, JobStatus, PrinterDriver, PrinterPort,
        SpoolerStatus, WMI_AVAILABLE, WIN32_AVAILABLE, IS_WINDOWS
    )
    WINDOWS_AGENTS_AVAILABLE = True
except ImportError:
    WINDOWS_AGENTS_AVAILABLE = False
    WMI_AVAILABLE = False
    WIN32_AVAILABLE = False
    IS_WINDOWS = platform.system() == "Windows"

# Jinja2
try:
    from jinja2 import Environment, FileSystemLoader, BaseLoader
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False

# APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

# Requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ==============================================================================
# LOGGER SETUP
# ==============================================================================

class LoggerSetup:
    """Central logging configuration"""
    _initialized = False
    
    @classmethod
    def setup(cls, log_file: str = "printer_manager.log", level: int = logging.DEBUG):
        if cls._initialized:
            return logging.getLogger("PrinterManager")
        
        logger = logging.getLogger("PrinterManager")
        logger.setLevel(level)
        
        if logger.handlers:
            return logger
        
        # File handler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s.%(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        ))
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        cls._initialized = True
        logger.info("=" * 70)
        logger.info("PRINTER MANAGEMENT SUITE v6.1")
        logger.info("=" * 70)
        
        return logger


logger = LoggerSetup.setup()


# ==============================================================================
# FALLBACK DATA MODELS (when modules not available)
# ==============================================================================

if not SNMP_ENGINE_AVAILABLE:
    class PrinterStatus:
        ONLINE = "ONLINE"
        OFFLINE = "OFFLINE"
        ERROR = "ERROR"
        UNKNOWN = "UNKNOWN"
    
    @dataclass
    class TonerLevel:
        name: str
        level: int
        max_capacity: int = 100
        is_low: bool = False
        is_critical: bool = False
        
        def __post_init__(self):
            self.is_low = self.level < 20
            self.is_critical = self.level < 10
    
    @dataclass
    class PrinterCheckResult:
        ip: str
        name: str
        status: str
        timestamp: datetime = field(default_factory=datetime.now)
        device_description: Optional[str] = None
        model: Optional[str] = None
        manufacturer: Optional[str] = None
        serial: Optional[str] = None
        toner_levels: List[TonerLevel] = field(default_factory=list)
        page_count: Optional[int] = None
        error_message: Optional[str] = None
        check_method: str = "unknown"
        check_duration_ms: int = 0
        detection_confidence: float = 0.0
        raw_data: Dict[str, Any] = field(default_factory=dict)
        
        @property
        def primary_toner(self) -> Optional[int]:
            return self.toner_levels[0].level if self.toner_levels else None
        
        @property
        def has_low_toner(self) -> bool:
            return any(t.is_low for t in self.toner_levels)
        
        @property
        def has_critical_toner(self) -> bool:
            return any(t.is_critical for t in self.toner_levels)
        
        @property
        def low_toner_list(self) -> List:
            return [t for t in self.toner_levels if t.is_low]
        
        @property
        def critical_toner_list(self) -> List:
            return [t for t in self.toner_levels if t.is_critical]

if not OID_REGISTRY_AVAILABLE:
    class Manufacturer(Enum):
        HP = "HP"
        CANON = "Canon"
        XEROX = "Xerox"
        KONICA_MINOLTA = "Konica Minolta"
        BROTHER = "Brother"
        EPSON = "Epson"
        LEXMARK = "Lexmark"
        RICOH = "Ricoh"
        KYOCERA = "Kyocera"
        SAMSUNG = "Samsung"
        GENERIC = "Generic"


# ==============================================================================
# ALERT DATA MODELS
# ==============================================================================

class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertChannel(Enum):
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    WEBHOOK = "webhook"
    LOG = "log"


@dataclass
class Alert:
    alert_type: str
    printer_ip: str
    message: str
    severity: AlertSeverity = AlertSeverity.WARNING
    timestamp: datetime = field(default_factory=datetime.now)
    sent: bool = False
    channels_sent: List[str] = field(default_factory=list)
    suppressed: bool = False
    suppression_reason: Optional[str] = None
    id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationResult:
    success: bool
    operation: str
    target: str
    message: str
    details: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


# ==============================================================================
# EVENT BUS (UI <-> Backend Communication)
# ==============================================================================

class EventType(Enum):
    PRINTER_CHECK_STARTED = "printer_check_started"
    PRINTER_CHECK_COMPLETED = "printer_check_completed"
    PRINTER_CHECK_FAILED = "printer_check_failed"
    BATCH_CHECK_STARTED = "batch_check_started"
    BATCH_CHECK_PROGRESS = "batch_check_progress"
    BATCH_CHECK_COMPLETED = "batch_check_completed"
    OPERATION_STARTED = "operation_started"
    OPERATION_COMPLETED = "operation_completed"
    ALERT_TRIGGERED = "alert_triggered"
    LOG_MESSAGE = "log_message"
    DISCOVERY_STARTED = "discovery_started"
    DISCOVERY_PROGRESS = "discovery_progress"
    DISCOVERY_COMPLETED = "discovery_completed"


@dataclass
class Event:
    type: EventType
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.now)


class EventBus:
    """Event bus for decoupled UI/Backend communication"""
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._queue: Queue = Queue()
        self._running = False
    
    def subscribe(self, event_type: EventType, callback: Callable):
        self._subscribers[event_type].append(callback)
    
    def unsubscribe(self, event_type: EventType, callback: Callable):
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
    
    def emit(self, event: Event):
        self._queue.put(event)
    
    def emit_now(self, event_type: EventType, data: Any = None):
        event = Event(type=event_type, data=data)
        for callback in self._subscribers[event_type]:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Event handler error: {e}")
    
    def process_events(self, root: tk.Tk):
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                for callback in self._subscribers[event.type]:
                    try:
                        callback(event)
                    except Exception as e:
                        logger.error(f"Event handler error: {e}")
            except:
                break
        
        if self._running:
            root.after(100, lambda: self.process_events(root))
    
    def start(self, root: tk.Tk):
        self._running = True
        self.process_events(root)
    
    def stop(self):
        self._running = False


# Global event bus
event_bus = EventBus()


# ==============================================================================
# SYSTEM HELPERS
# ==============================================================================

def get_windir() -> str:
    return os.environ.get("WINDIR", r"C:\Windows")


def get_system32() -> str:
    return os.path.join(get_windir(), "System32")


def get_ping_exe() -> str:
    if platform.system() == "Windows":
        ping = os.path.join(get_system32(), "ping.exe")
        if os.path.exists(ping):
            return ping
    return shutil.which("ping") or "ping"


def get_powershell_exe() -> str:
    if platform.system() == "Windows":
        ps51 = os.path.join(get_windir(), r"System32\WindowsPowerShell\v1.0\powershell.exe")
        if os.path.exists(ps51):
            return ps51
    return shutil.which("pwsh") or shutil.which("pwsh.exe") or "powershell"


def run_powershell(ps_command: str, timeout: int = 20) -> subprocess.CompletedProcess:
    ps_exe = get_powershell_exe()
    logger.debug(f"PowerShell: {ps_command[:80]}...")
    return subprocess.run(
        [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
        check=False, capture_output=True, timeout=timeout, text=True
    )


def run_ping(ip: str, count: int = 1, timeout: int = 3) -> subprocess.CompletedProcess:
    if platform.system() == "Windows":
        cmd = [get_ping_exe(), "-n", str(count), "-w", str(timeout * 1000), ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), ip]
    return subprocess.run(cmd, capture_output=True, timeout=timeout + 2, text=True)


# ==============================================================================
# DATABASE MANAGER
# ==============================================================================

class DatabaseManager:
    """SQLite database manager with auto-migration"""
    
    def __init__(self, db_file: str = "printer_history.db"):
        self.db_file = db_file
        self.init_db()
        self.migrate_db()
        logger.info(f"Database initialized: {db_file}")
    
    def init_db(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS printer_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                printer_ip TEXT NOT NULL,
                printer_name TEXT,
                status TEXT,
                manufacturer TEXT,
                model TEXT,
                serial TEXT,
                toner_level INTEGER,
                page_count INTEGER,
                error TEXT,
                notes TEXT,
                check_method TEXT,
                check_duration_ms INTEGER,
                detection_confidence REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                operation TEXT NOT NULL,
                target TEXT,
                status TEXT,
                details TEXT,
                user TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                alert_type TEXT,
                printer_ip TEXT,
                message TEXT,
                severity TEXT,
                sent BOOLEAN DEFAULT 0,
                channels_sent TEXT,
                suppressed BOOLEAN DEFAULT 0,
                suppression_reason TEXT,
                metadata TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS discovered_printers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                ip TEXT UNIQUE,
                name TEXT,
                manufacturer TEXT,
                model TEXT,
                sys_descr TEXT,
                auto_added BOOLEAN DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS local_printer_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                printer_name TEXT NOT NULL,
                status TEXT,
                printer_type TEXT,
                driver_name TEXT,
                port_name TEXT,
                jobs_count INTEGER,
                is_default BOOLEAN,
                raw_data TEXT
            )
        ''')
        
        # Indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ph_ip ON printer_history(printer_ip)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ph_ts ON printer_history(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_lph_name ON local_printer_history(printer_name)')
        
        conn.commit()
        conn.close()
    
    def migrate_db(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Get existing columns
        cursor.execute("PRAGMA table_info(printer_history)")
        ph_columns = {row[1] for row in cursor.fetchall()}
        
        cursor.execute("PRAGMA table_info(alerts)")
        alert_columns = {row[1] for row in cursor.fetchall()}
        
        # Migrations
        ph_migrations = [
            ("manufacturer", "TEXT"),
            ("model", "TEXT"),
            ("serial", "TEXT"),
            ("check_method", "TEXT"),
            ("check_duration_ms", "INTEGER"),
            ("detection_confidence", "REAL"),
        ]
        
        for column, col_type in ph_migrations:
            if column not in ph_columns:
                try:
                    cursor.execute(f"ALTER TABLE printer_history ADD COLUMN {column} {col_type}")
                    logger.info(f"Migration: Added {column} to printer_history")
                except:
                    pass
        
        alert_migrations = [
            ("channels_sent", "TEXT"),
            ("suppressed", "BOOLEAN DEFAULT 0"),
            ("suppression_reason", "TEXT"),
            ("metadata", "TEXT"),
        ]
        
        for column, col_type in alert_migrations:
            if column not in alert_columns:
                try:
                    cursor.execute(f"ALTER TABLE alerts ADD COLUMN {column} {col_type}")
                    logger.info(f"Migration: Added {column} to alerts")
                except:
                    pass
        
        conn.commit()
        conn.close()
    
    def log_printer_result(self, result: PrinterCheckResult):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        toner_data = json.dumps([
            {'name': t.name, 'level': t.level, 'is_low': t.is_low, 'is_critical': t.is_critical}
            for t in result.toner_levels
        ]) if result.toner_levels else None
        
        cursor.execute('''
            INSERT INTO printer_history 
            (printer_ip, printer_name, status, manufacturer, model, serial,
             toner_level, page_count, error, notes, check_method, 
             check_duration_ms, detection_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result.ip, result.name, result.status, result.manufacturer,
            result.model, result.serial, result.primary_toner, result.page_count,
            result.error_message, toner_data, result.check_method,
            result.check_duration_ms, getattr(result, 'detection_confidence', 0)
        ))
        conn.commit()
        conn.close()
    
    def log_local_printer(self, printer):
        """Log local printer status"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO local_printer_history 
            (printer_name, status, printer_type, driver_name, port_name, jobs_count, is_default, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            printer.name,
            printer.status.value if hasattr(printer.status, 'value') else str(printer.status),
            printer.printer_type.value if hasattr(printer.printer_type, 'value') else str(printer.printer_type),
            printer.driver_name,
            printer.port_name,
            printer.jobs_count,
            printer.is_default,
            json.dumps(printer.raw_wmi_data) if hasattr(printer, 'raw_wmi_data') else None
        ))
        conn.commit()
        conn.close()
    
    def log_operation(self, result: OperationResult, user: str = "system"):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO operation_logs (operation, target, status, details, user)
            VALUES (?, ?, ?, ?, ?)
        ''', (result.operation, result.target,
              'success' if result.success else 'failed',
              result.details, user))
        conn.commit()
        conn.close()
    
    def log_alert(self, alert: Alert):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO alerts (alert_type, printer_ip, message, severity, sent,
                              channels_sent, suppressed, suppression_reason, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            alert.alert_type, alert.printer_ip, alert.message, alert.severity.value,
            alert.sent, json.dumps(alert.channels_sent), alert.suppressed,
            alert.suppression_reason, json.dumps(alert.metadata)
        ))
        conn.commit()
        conn.close()
    
    def save_discovered_printer(self, printer: Dict[str, Any]):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO discovered_printers 
            (ip, name, manufacturer, model, sys_descr, auto_added, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            printer.get('ip'),
            printer.get('name'),
            printer.get('manufacturer'),
            printer.get('model_hint'),
            printer.get('sys_descr'),
            False
        ))
        conn.commit()
        conn.close()
    
    def get_discovered_printers(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT ip, name, manufacturer, model, sys_descr, timestamp FROM discovered_printers ORDER BY timestamp DESC')
        columns = ['ip', 'name', 'manufacturer', 'model', 'sys_descr', 'timestamp']
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def get_printer_history(self, ip: str, hours: int = 24) -> List[Dict]:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        since = datetime.now() - timedelta(hours=hours)
        cursor.execute('''
            SELECT * FROM printer_history 
            WHERE printer_ip = ? AND timestamp > ?
            ORDER BY timestamp DESC
        ''', (ip, since))
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def get_latest_printer_statuses(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT printer_ip, printer_name, status, manufacturer, model,
                   toner_level, page_count, timestamp, notes, check_method,
                   check_duration_ms, detection_confidence
            FROM printer_history
            WHERE (printer_ip, timestamp) IN (
                SELECT printer_ip, MAX(timestamp) 
                FROM printer_history 
                GROUP BY printer_ip
            )
            ORDER BY timestamp DESC
        ''')
        columns = ['ip', 'name', 'status', 'manufacturer', 'model',
                   'toner_level', 'page_count', 'timestamp', 'notes',
                   'check_method', 'check_duration_ms', 'detection_confidence']
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def get_alerts(self, hours: int = 24, unsent_only: bool = False) -> List[Alert]:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        since = datetime.now() - timedelta(hours=hours)
        
        query = '''
            SELECT id, timestamp, alert_type, printer_ip, message, severity,
                   sent, channels_sent, suppressed, suppression_reason, metadata
            FROM alerts WHERE timestamp > ?
        '''
        if unsent_only:
            query += ' AND sent = 0'
        query += ' ORDER BY timestamp DESC'
        
        cursor.execute(query, (since,))
        
        alerts = []
        for row in cursor.fetchall():
            try:
                alerts.append(Alert(
                    id=row[0],
                    timestamp=datetime.fromisoformat(row[1]) if isinstance(row[1], str) else row[1],
                    alert_type=row[2],
                    printer_ip=row[3],
                    message=row[4],
                    severity=AlertSeverity(row[5]) if row[5] else AlertSeverity.WARNING,
                    sent=bool(row[6]),
                    channels_sent=json.loads(row[7]) if row[7] else [],
                    suppressed=bool(row[8]) if row[8] is not None else False,
                    suppression_reason=row[9],
                    metadata=json.loads(row[10]) if row[10] else {}
                ))
            except Exception as e:
                logger.debug(f"Error parsing alert: {e}")
        
        conn.close()
        return alerts
    
    def get_operations(self, limit: int = 50) -> List[Dict]:
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, operation, target, status, details, user
            FROM operation_logs ORDER BY timestamp DESC LIMIT ?
        ''', (limit,))
        columns = ['timestamp', 'operation', 'target', 'status', 'details', 'user']
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get overall statistics for dashboard"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Vendor distribution
        cursor.execute('''
            SELECT manufacturer, COUNT(*) FROM (
                SELECT DISTINCT printer_ip, manufacturer FROM printer_history
                WHERE manufacturer IS NOT NULL
            ) GROUP BY manufacturer
        ''')
        vendor_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Average toner levels
        cursor.execute('''
            SELECT AVG(toner_level) FROM printer_history
            WHERE toner_level IS NOT NULL AND timestamp > datetime('now', '-24 hours')
        ''')
        avg_toner = cursor.fetchone()[0] or 0
        
        # Alert counts by severity
        cursor.execute('''
            SELECT severity, COUNT(*) FROM alerts
            WHERE timestamp > datetime('now', '-24 hours')
            GROUP BY severity
        ''')
        alert_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        conn.close()
        
        return {
            'vendor_distribution': vendor_stats,
            'average_toner': round(avg_toner, 1),
            'alert_counts': alert_stats
        }
    
    def clear_all(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM printer_history')
        cursor.execute('DELETE FROM operation_logs')
        cursor.execute('DELETE FROM alerts')
        cursor.execute('DELETE FROM discovered_printers')
        cursor.execute('DELETE FROM local_printer_history')
        conn.commit()
        conn.close()
        logger.warning("Database cleared")


# ==============================================================================
# CONFIG MANAGER
# ==============================================================================

class ConfigManager:
    
    @staticmethod
    def load_config() -> Dict[str, Any]:
        config_file = "config.yaml"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                    # Merge with defaults
                    defaults = ConfigManager.get_default_config()
                    for key, value in defaults.items():
                        if key not in config:
                            config[key] = value
                        elif isinstance(value, dict):
                            for k, v in value.items():
                                if k not in config.get(key, {}):
                                    config.setdefault(key, {})[k] = v
                    logger.info(f"Configuration loaded from {config_file}")
                    return config
            except Exception as e:
                logger.error(f"Error loading config: {e}")
        return ConfigManager.get_default_config()
    
    @staticmethod
    def save_config(config: Dict[str, Any]) -> bool:
        try:
            with open("config.yaml", 'w', encoding='utf-8') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            logger.info("Configuration saved")
            return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False
    
    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            'snmp': {
                'version': '2c',
                'community': 'public',
                'timeout': 2,
                'retries': 1,
                'max_workers': 10,
            },
            'email': {
                'enabled': False,
                'smtp_server': 'smtp.gmail.com',
                'smtp_port': 587,
                'sender': '',
                'password': '',
                'recipients': []
            },
            'slack': {'enabled': False, 'webhook_url': ''},
            'teams': {'enabled': False, 'webhook_url': ''},
            'scheduler': {
                'enabled': False,
                'interval_minutes': 30,
                'debounce_count': 2
            },
            'suppression': {
                'night_window': {
                    'enabled': False,
                    'start_hour': 22,
                    'end_hour': 6,
                    'severity_threshold': 'error'
                },
                'weekend': {
                    'enabled': False,
                    'severity_threshold': 'critical'
                }
            },
            'printers': [],
            'discovery': {
                'enabled': True,
                'subnet': ''
            },
            'local_printers': {
                'enabled': True,
                'monitor_interval_minutes': 5
            }
        }


# ==============================================================================
# ALERT SERVICE
# ==============================================================================

@dataclass
class AlertRule:
    name: str
    condition: Callable[[PrinterCheckResult], bool]
    severity: AlertSeverity
    channels: List[AlertChannel]
    message_template: str
    enabled: bool = True
    
    def matches(self, result: PrinterCheckResult) -> bool:
        if not self.enabled:
            return False
        try:
            return self.condition(result)
        except:
            return False
    
    def format_message(self, result: PrinterCheckResult) -> str:
        try:
            return self.message_template.format(
                name=result.name,
                ip=result.ip,
                status=result.status,
                manufacturer=result.manufacturer or "Unknown",
                model=result.model or "Unknown",
                toner_levels=", ".join(f"{t.name}: {t.level}%" for t in result.toner_levels),
                low_toners=", ".join(t.name for t in result.low_toner_list),
                critical_toners=", ".join(t.name for t in result.critical_toner_list),
                page_count=result.page_count or "N/A"
            )
        except:
            return f"Alert for {result.name} ({result.ip})"


@dataclass
class SuppressionWindow:
    name: str
    start_time: time
    end_time: time
    days: List[int] = field(default_factory=lambda: list(range(7)))
    severity_threshold: Optional[AlertSeverity] = None
    enabled: bool = True
    
    def is_active(self, dt: datetime = None) -> bool:
        if not self.enabled:
            return False
        dt = dt or datetime.now()
        if dt.weekday() not in self.days:
            return False
        current = dt.time()
        if self.start_time <= self.end_time:
            return self.start_time <= current <= self.end_time
        return current >= self.start_time or current <= self.end_time
    
    def should_suppress(self, alert: Alert) -> bool:
        if not self.is_active():
            return False
        if self.severity_threshold:
            order = [AlertSeverity.INFO, AlertSeverity.WARNING,
                     AlertSeverity.ERROR, AlertSeverity.CRITICAL]
            if order.index(alert.severity) < order.index(self.severity_threshold):
                return True
        return True


class AlertDispatcher:
    """Multi-channel alert dispatcher"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    def send(self, alert: Alert, channels: List[AlertChannel]) -> List[str]:
        successful = []
        for channel in channels:
            try:
                if channel == AlertChannel.EMAIL and self._send_email(alert):
                    successful.append("email")
                elif channel == AlertChannel.SLACK and self._send_slack(alert):
                    successful.append("slack")
                elif channel == AlertChannel.TEAMS and self._send_teams(alert):
                    successful.append("teams")
                elif channel == AlertChannel.LOG:
                    self._send_log(alert)
                    successful.append("log")
            except Exception as e:
                logger.error(f"Alert dispatch failed ({channel.value}): {e}")
        return successful
    
    def _send_email(self, alert: Alert) -> bool:
        cfg = self.config.get('email', {})
        if not cfg.get('enabled'):
            return False
        
        try:
            server = smtplib.SMTP(cfg['smtp_server'], cfg.get('smtp_port', 587))
            server.starttls()
            server.login(cfg['sender'], cfg['password'])
            
            msg = MIMEMultipart()
            msg['From'] = cfg['sender']
            msg['To'] = ', '.join(cfg['recipients'])
            msg['Subject'] = f"[{alert.severity.value.upper()}] Printer: {alert.alert_type}"
            
            body = f"""
Printer Alert
{'='*50}
Type: {alert.alert_type}
Printer: {alert.printer_ip}
Severity: {alert.severity.value}
Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}

Message:
{alert.message}

{'='*50}
Printer Management Suite v6.1
            """
            msg.attach(MIMEText(body, 'plain'))
            
            server.send_message(msg)
            server.quit()
            logger.info(f"Email sent: {alert.alert_type}")
            return True
        except Exception as e:
            logger.error(f"Email failed: {e}")
            return False
    
    def _send_slack(self, alert: Alert) -> bool:
        if not REQUESTS_AVAILABLE:
            return False
        
        cfg = self.config.get('slack', {})
        if not cfg.get('enabled') or not cfg.get('webhook_url'):
            return False
        
        try:
            colors = {
                AlertSeverity.INFO: "#36a64f",
                AlertSeverity.WARNING: "#ffcc00",
                AlertSeverity.ERROR: "#ff6600",
                AlertSeverity.CRITICAL: "#ff0000"
            }
            
            payload = {
                "attachments": [{
                    "color": colors.get(alert.severity, "#808080"),
                    "title": f"🖨️ {alert.alert_type}",
                    "text": alert.message,
                    "fields": [
                        {"title": "Printer", "value": alert.printer_ip, "short": True},
                        {"title": "Severity", "value": alert.severity.value, "short": True}
                    ],
                    "footer": "Printer Management Suite",
                    "ts": int(alert.timestamp.timestamp())
                }]
            }
            response = requests.post(cfg['webhook_url'], json=payload, timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def _send_teams(self, alert: Alert) -> bool:
        if not REQUESTS_AVAILABLE:
            return False
        
        cfg = self.config.get('teams', {})
        if not cfg.get('enabled') or not cfg.get('webhook_url'):
            return False
        
        try:
            colors = {
                AlertSeverity.INFO: "00ff00",
                AlertSeverity.WARNING: "ffcc00",
                AlertSeverity.ERROR: "ff6600",
                AlertSeverity.CRITICAL: "ff0000"
            }
            
            payload = {
                "@type": "MessageCard",
                "themeColor": colors.get(alert.severity, "808080"),
                "summary": alert.alert_type,
                "sections": [{
                    "activityTitle": f"🖨️ {alert.alert_type}",
                    "facts": [
                        {"name": "Printer", "value": alert.printer_ip},
                        {"name": "Severity", "value": alert.severity.value}
                    ],
                    "text": alert.message
                }]
            }
            response = requests.post(cfg['webhook_url'], json=payload, timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def _send_log(self, alert: Alert):
        levels = {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.ERROR: logging.ERROR,
            AlertSeverity.CRITICAL: logging.CRITICAL
        }
        logger.log(levels.get(alert.severity, logging.INFO),
                  f"ALERT: {alert.alert_type} - {alert.printer_ip} - {alert.message}")


class AlertService:
    """Central alert service"""
    
    def __init__(self, config: Dict[str, Any], db: DatabaseManager):
        self.config = config
        self.db = db
        self.dispatcher = AlertDispatcher(config)
        self.rules: List[AlertRule] = []
        self.suppression_windows: List[SuppressionWindow] = []
        self._load_default_rules()
        self._load_suppression_windows()
        logger.info("AlertService initialized")
    
    def _load_default_rules(self):
        # Critical toner
        self.rules.append(AlertRule(
            name="critical_toner",
            condition=lambda r: r.has_critical_toner,
            severity=AlertSeverity.CRITICAL,
            channels=[AlertChannel.EMAIL, AlertChannel.SLACK, AlertChannel.LOG],
            message_template="CRITICAL: {name} ({ip}) toner critical: {critical_toners}"
        ))
        
        # Low toner
        self.rules.append(AlertRule(
            name="low_toner",
            condition=lambda r: r.has_low_toner and not r.has_critical_toner,
            severity=AlertSeverity.WARNING,
            channels=[AlertChannel.EMAIL, AlertChannel.LOG],
            message_template="Warning: {name} ({ip}) low toner: {low_toners}"
        ))
        
        # Offline
        self.rules.append(AlertRule(
            name="offline",
            condition=lambda r: r.status == PrinterStatus.OFFLINE or r.status == "OFFLINE",
            severity=AlertSeverity.ERROR,
            channels=[AlertChannel.EMAIL, AlertChannel.SLACK, AlertChannel.LOG],
            message_template="ERROR: {name} ({ip}) is OFFLINE"
        ))
        
        # Error status
        self.rules.append(AlertRule(
            name="error",
            condition=lambda r: r.status == PrinterStatus.ERROR or r.status == "ERROR",
            severity=AlertSeverity.ERROR,
            channels=[AlertChannel.EMAIL, AlertChannel.LOG],
            message_template="ERROR: {name} ({ip}) has error"
        ))
    
    def _load_suppression_windows(self):
        night = self.config.get('suppression', {}).get('night_window', {})
        if night.get('enabled'):
            self.suppression_windows.append(SuppressionWindow(
                name="Night",
                start_time=time(night.get('start_hour', 22), 0),
                end_time=time(night.get('end_hour', 6), 0),
                severity_threshold=AlertSeverity(night.get('severity_threshold', 'error'))
            ))
        
        weekend = self.config.get('suppression', {}).get('weekend', {})
        if weekend.get('enabled'):
            self.suppression_windows.append(SuppressionWindow(
                name="Weekend",
                start_time=time(0, 0),
                end_time=time(23, 59),
                days=[5, 6],
                severity_threshold=AlertSeverity(weekend.get('severity_threshold', 'critical'))
            ))
    
    def process_result(self, result: PrinterCheckResult) -> List[Alert]:
        alerts = []
        
        for rule in self.rules:
            if rule.matches(result):
                alert = Alert(
                    alert_type=rule.name,
                    printer_ip=result.ip,
                    message=rule.format_message(result),
                    severity=rule.severity,
                    metadata={
                        'manufacturer': result.manufacturer,
                        'model': result.model,
                        'check_method': result.check_method
                    }
                )
                
                # Check suppression
                for window in self.suppression_windows:
                    if window.should_suppress(alert):
                        alert.suppressed = True
                        alert.suppression_reason = f"Window: {window.name}"
                        break
                
                if not alert.suppressed:
                    alert.channels_sent = self.dispatcher.send(alert, rule.channels)
                    alert.sent = len(alert.channels_sent) > 0
                
                self.db.log_alert(alert)
                alerts.append(alert)
                
                event_bus.emit(Event(EventType.ALERT_TRIGGERED, alert))
        
        return alerts
    
    def get_active_suppressions(self) -> List[SuppressionWindow]:
        return [w for w in self.suppression_windows if w.is_active()]


# ==============================================================================
# BACKEND SERVICES
# ==============================================================================

class PrinterMonitorService:
    """Backend service for network printer monitoring (SNMP)"""
    
    def __init__(self, config: Dict[str, Any], db: DatabaseManager,
                 alert_service: AlertService, snmp_engine):
        self.config = config
        self.db = db
        self.alert_service = alert_service
        self.snmp = snmp_engine
        logger.info("PrinterMonitorService initialized")
    
    def check_printer(self, ip: str, name: str = None,
                      manufacturer: str = None) -> PrinterCheckResult:
        """Check a single network printer"""
        name = name or ip
        
        event_bus.emit(Event(EventType.PRINTER_CHECK_STARTED, {'ip': ip, 'name': name}))
        
        # Parse manufacturer if provided
        mfr = None
        if manufacturer and OID_REGISTRY_AVAILABLE:
            try:
                mfr = Manufacturer(manufacturer)
            except ValueError:
                pass
        
        # Use SNMP engine if available
        if self.snmp:
            result = self.snmp.check_single(ip, name, mfr)
        else:
            # Fallback to basic ping
            result = self._basic_check(ip, name)
        
        # Log and process
        self.db.log_printer_result(result)
        self.alert_service.process_result(result)
        
        event_bus.emit(Event(EventType.PRINTER_CHECK_COMPLETED, result))
        
        return result
    
    def _basic_check(self, ip: str, name: str) -> PrinterCheckResult:
        """Basic ping check when SNMP engine not available"""
        start_time = datetime.now()
        try:
            result = run_ping(ip, count=1, timeout=2)
            status = "ONLINE" if result.returncode == 0 else "OFFLINE"
        except:
            status = "OFFLINE"
        
        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        
        return PrinterCheckResult(
            ip=ip,
            name=name,
            status=status,
            check_method="ping",
            check_duration_ms=duration_ms
        )
    
    def check_all_printers(self, progress_callback: Callable = None) -> List[PrinterCheckResult]:
        """Check all configured network printers"""
        printers = self.config.get('printers', [])
        
        if not printers:
            logger.warning("No printers configured")
            return []
        
        event_bus.emit(Event(EventType.BATCH_CHECK_STARTED, {'total': len(printers)}))
        
        def internal_progress(completed, total, result):
            self.db.log_printer_result(result)
            self.alert_service.process_result(result)
            
            event_bus.emit(Event(EventType.BATCH_CHECK_PROGRESS, {
                'completed': completed,
                'total': total,
                'result': result
            }))
            
            if progress_callback:
                progress_callback(completed, total, result)
        
        if self.snmp:
            results = self.snmp.check_batch(printers, internal_progress)
        else:
            # Sequential fallback
            results = []
            for i, p in enumerate(printers):
                result = self._basic_check(p.get('ip'), p.get('name', p.get('ip')))
                results.append(result)
                internal_progress(i + 1, len(printers), result)
        
        event_bus.emit(Event(EventType.BATCH_CHECK_COMPLETED, {'results': results}))
        
        return results
    
    def discover_printers(self, subnet: str, progress_callback: Callable = None) -> List[Dict]:
        """Discover printers on subnet"""
        if not self.snmp or not SNMP_ENGINE_AVAILABLE:
            logger.warning("SNMP engine not available for discovery")
            return []
        
        event_bus.emit(Event(EventType.DISCOVERY_STARTED, {'subnet': subnet}))
        
        from snmp_engine import SNMPDiscovery
        discovery = SNMPDiscovery(self.snmp)
        
        def internal_progress(completed, total, ip):
            event_bus.emit(Event(EventType.DISCOVERY_PROGRESS, {
                'completed': completed,
                'total': total,
                'ip': ip
            }))
            if progress_callback:
                progress_callback(completed, total, ip)
        
        found = discovery.scan_subnet(subnet, internal_progress)
        
        # Save to database
        for printer in found:
            self.db.save_discovered_printer(printer)
        
        event_bus.emit(Event(EventType.DISCOVERY_COMPLETED, {'found': found}))
        
        return found
    
    def get_printer_history(self, ip: str, hours: int = 24) -> List[Dict]:
        return self.db.get_printer_history(ip, hours)
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        printers = self.db.get_latest_printer_statuses()
        alerts = self.db.get_alerts(hours=24)
        stats = self.db.get_statistics()
        
        # Calculate toner averages per color
        toner_stats = {'black': [], 'cyan': [], 'magenta': [], 'yellow': []}
        for p in printers:
            if p.get('notes'):
                try:
                    toners = json.loads(p['notes'])
                    for t in toners:
                        if t['name'] in toner_stats:
                            toner_stats[t['name']].append(t['level'])
                except:
                    pass
        
        toner_averages = {
            color: int(sum(levels) / len(levels)) if levels else 0
            for color, levels in toner_stats.items()
        }
        
        return {
            'printers': printers,
            'alerts': alerts,
            'active_suppressions': self.alert_service.get_active_suppressions(),
            'stats': {
                'total': len(printers),
                'online': sum(1 for p in printers if p.get('status') == 'ONLINE'),
                'offline': sum(1 for p in printers if p.get('status') == 'OFFLINE'),
                'error': sum(1 for p in printers if p.get('status') == 'ERROR'),
                'alerts': len(alerts)
            },
            'vendor_stats': stats.get('vendor_distribution', {}),
            'toner_stats': toner_averages
        }


class PrinterDeploymentService:
    """Backend service for printer deployment"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
        logger.info("PrinterDeploymentService initialized")
    
    def map_printer(self, ip: str, name: str, driver: str = None) -> OperationResult:
        if platform.system() != "Windows":
            return OperationResult(False, "map_printer", name, "Windows only")
        
        event_bus.emit(Event(EventType.OPERATION_STARTED,
                            {'operation': 'map_printer', 'target': name}))
        
        try:
            # Create port
            ps_port = f"""
if (-not (Get-PrinterPort -Name 'IP_{ip}' -ErrorAction SilentlyContinue)) {{
    Add-PrinterPort -Name 'IP_{ip}' -PrinterHostAddress '{ip}'
    'Port created'
}} else {{ 'Port exists' }}
"""
            run_powershell(ps_port, timeout=20)
            
            if driver:
                ps_check = f"if (Get-PrinterDriver -Name '{driver}' -ErrorAction SilentlyContinue) {{ 'found' }} else {{ 'not found' }}"
                check_result = run_powershell(ps_check, timeout=10)
                
                if 'found' in check_result.stdout.lower() and 'not found' not in check_result.stdout.lower():
                    ps_add = f"Add-Printer -Name '{name}' -DriverName '{driver}' -PortName 'IP_{ip}'"
                    add_result = run_powershell(ps_add, timeout=20)
                    
                    if add_result.returncode == 0:
                        result = OperationResult(True, "map_printer", name,
                                                f"Mapped with driver '{driver}'", f"IP: {ip}")
                    else:
                        result = OperationResult(False, "map_printer", name,
                                                f"Add failed: {add_result.stderr[:100]}")
                else:
                    result = OperationResult(False, "map_printer", name,
                                            f"Driver not found: {driver}", "Port created")
            else:
                result = OperationResult(True, "map_printer", name, "Port created")
            
            self.db.log_operation(result)
            event_bus.emit(Event(EventType.OPERATION_COMPLETED, result))
            return result
            
        except Exception as e:
            result = OperationResult(False, "map_printer", name, str(e))
            self.db.log_operation(result)
            event_bus.emit(Event(EventType.OPERATION_COMPLETED, result))
            return result
    
    def remove_printer(self, name: str) -> OperationResult:
        if platform.system() != "Windows":
            return OperationResult(False, "remove_printer", name, "Windows only")
        
        try:
            run_powershell(f'Remove-Printer -Name "{name}" -ErrorAction SilentlyContinue', timeout=10)
            result = OperationResult(True, "remove_printer", name, "Removed")
            self.db.log_operation(result)
            return result
        except Exception as e:
            return OperationResult(False, "remove_printer", name, str(e))
    
    def reset_queue(self, name: str) -> OperationResult:
        if platform.system() != "Windows":
            return OperationResult(False, "reset_queue", name, "Windows only")
        
        try:
            run_powershell(f'Get-PrintJob -PrinterName "{name}" -ErrorAction SilentlyContinue | Remove-PrintJob -ErrorAction SilentlyContinue', timeout=10)
            result = OperationResult(True, "reset_queue", name, "Queue cleared")
            self.db.log_operation(result)
            return result
        except Exception as e:
            return OperationResult(False, "reset_queue", name, str(e))


class SpoolerService:
    """Backend service for spooler management"""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
        logger.info("SpoolerService initialized")
    
    def stop(self) -> OperationResult:
        if platform.system() != "Windows":
            return OperationResult(False, "stop_spooler", "spooler", "Windows only")
        
        try:
            run_powershell('Stop-Service -Name spooler -Force', timeout=10)
            result = OperationResult(True, "stop_spooler", "spooler", "Stopped")
            self.db.log_operation(result)
            return result
        except Exception as e:
            return OperationResult(False, "stop_spooler", "spooler", str(e))
    
    def start(self) -> OperationResult:
        if platform.system() != "Windows":
            return OperationResult(False, "start_spooler", "spooler", "Windows only")
        
        try:
            run_powershell('Start-Service -Name spooler', timeout=10)
            result = OperationResult(True, "start_spooler", "spooler", "Started")
            self.db.log_operation(result)
            return result
        except Exception as e:
            return OperationResult(False, "start_spooler", "spooler", str(e))
    
    def clean(self) -> OperationResult:
        if platform.system() != "Windows":
            return OperationResult(False, "clean_spool", "spool", "Windows only")
        
        try:
            run_powershell('Stop-Service -Name spooler -Force', timeout=5)
            
            spool_path = r"C:\Windows\System32\spool\PRINTERS"
            deleted = 0
            if os.path.exists(spool_path):
                for f in os.listdir(spool_path):
                    try:
                        os.remove(os.path.join(spool_path, f))
                        deleted += 1
                    except:
                        pass
            
            result = OperationResult(True, "clean_spool", "spool", f"Cleaned ({deleted} files)")
            self.db.log_operation(result)
            return result
        except Exception as e:
            return OperationResult(False, "clean_spool", "spool", str(e))
    
    def full_reset(self) -> OperationResult:
        stop = self.stop()
        clean = self.clean()
        start = self.start()
        
        success = stop.success and clean.success and start.success
        details = f"Stop: {'OK' if stop.success else 'FAIL'}, Clean: {'OK' if clean.success else 'FAIL'}, Start: {'OK' if start.success else 'FAIL'}"
        
        result = OperationResult(success, "full_reset", "spooler",
                                "Complete" if success else "Partial", details)
        self.db.log_operation(result)
        return result


# ==============================================================================
# SCHEDULER
# ==============================================================================

class PrinterScheduler:
    """Background scheduler for automatic monitoring"""
    
    def __init__(self, monitor_service: PrinterMonitorService, config: Dict[str, Any]):
        self.monitor = monitor_service
        self.config = config
        self.scheduler_config = config.get('scheduler', {})
        self.scheduler = None
        self.offline_counter = defaultdict(int)
        self.debounce_count = self.scheduler_config.get('debounce_count', 2)
        
        if APSCHEDULER_AVAILABLE:
            self.scheduler = BackgroundScheduler()
    
    def start(self):
        if not self.scheduler or not self.scheduler_config.get('enabled'):
            return
        
        interval = self.scheduler_config.get('interval_minutes', 30)
        self.scheduler.add_job(
            self._run_checks,
            IntervalTrigger(minutes=interval),
            id='printer_check',
            replace_existing=True
        )
        
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info(f"Scheduler started: {interval}min interval")
    
    def stop(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
    
    def _run_checks(self):
        logger.info("Scheduled check started")
        
        def progress(completed, total, result):
            status = result.status if hasattr(result, 'status') else str(result.status)
            if status == "OFFLINE" or status == PrinterStatus.OFFLINE:
                self.offline_counter[result.ip] += 1
            else:
                self.offline_counter[result.ip] = 0
        
        self.monitor.check_all_printers(progress)
        logger.info("Scheduled check completed")
    
    def run_now(self):
        threading.Thread(target=self._run_checks, daemon=True).start()


# ==============================================================================
# DASHBOARD GENERATOR
# ==============================================================================

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>Printer Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        :root { --primary: #667eea; --success: #48bb78; --warning: #ed8936; --danger: #f56565; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea, #764ba2); min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: white; padding: 20px 30px; border-radius: 12px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .header h1 { font-size: 1.5em; }
        .stats { display: flex; gap: 30px; }
        .stat-value { font-size: 2em; font-weight: bold; }
        .stat-value.online { color: var(--success); }
        .stat-value.offline { color: var(--danger); }
        .stat-label { font-size: 0.75em; color: #666; text-transform: uppercase; }
        .charts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 20px; }
        .chart-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .chart-card h3 { font-size: 0.9em; color: #666; margin-bottom: 15px; text-align: center; }
        .chart-container { height: 200px; }
        .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
        .card { background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); overflow: hidden; }
        .card-header { padding: 15px 20px; border-bottom: 1px solid #eee; }
        .card-header h2 { font-size: 1em; }
        .card-body { padding: 20px; max-height: 500px; overflow-y: auto; }
        .printer { background: #f8f9fa; border-radius: 8px; padding: 15px; margin-bottom: 12px; border-left: 4px solid var(--primary); }
        .printer.online { border-left-color: var(--success); }
        .printer.offline { border-left-color: var(--danger); }
        .printer-header { display: flex; justify-content: space-between; margin-bottom: 10px; }
        .printer-name { font-weight: 600; }
        .badge { padding: 3px 10px; border-radius: 20px; font-size: 0.7em; font-weight: 600; }
        .badge.online { background: #c6f6d5; color: #22543d; }
        .badge.offline { background: #fed7d7; color: #742a2a; }
        .vendor-badge { font-size: 0.7em; padding: 2px 8px; border-radius: 4px; margin-left: 8px; }
        .vendor-hp { background: #0096d6; color: white; }
        .vendor-canon { background: #cc0000; color: white; }
        .vendor-xerox { background: #c8102e; color: white; }
        .vendor-brother { background: #004c97; color: white; }
        .toner-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
        .toner-label { width: 60px; font-size: 0.8em; color: #666; }
        .toner-bar { flex: 1; height: 10px; background: #e2e8f0; border-radius: 5px; overflow: hidden; }
        .toner-fill { height: 100%; border-radius: 5px; }
        .toner-fill.black { background: #333; }
        .toner-fill.cyan { background: #0bc5ea; }
        .toner-fill.magenta { background: #ed64a6; }
        .toner-fill.yellow { background: #ecc94b; }
        .toner-fill.low { animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .toner-pct { width: 35px; text-align: right; font-size: 0.8em; font-weight: 600; }
        .toner-pct.low { color: var(--warning); }
        .toner-pct.critical { color: var(--danger); }
        .alert { padding: 12px; margin-bottom: 8px; border-radius: 8px; border-left: 4px solid; background: #f8f9fa; }
        .alert.warning { border-color: var(--warning); background: #fffaf0; }
        .alert.error { border-color: var(--danger); background: #fff5f5; }
        .alert.critical { border-color: #c53030; background: #fed7d7; }
        .alert-header { display: flex; justify-content: space-between; font-size: 0.85em; }
        .alert-type { font-weight: 600; }
        .alert-time { color: #999; font-size: 0.75em; }
        .alert-msg { font-size: 0.8em; color: #555; margin-top: 4px; }
        .footer { text-align: center; color: rgba(255,255,255,0.8); margin-top: 20px; font-size: 0.85em; }
        .no-data { text-align: center; padding: 40px; color: #999; }
        @media (max-width: 1200px) { .charts { grid-template-columns: 1fr 1fr; } .grid { grid-template-columns: 1fr; } }
        @media (max-width: 768px) { .charts { grid-template-columns: 1fr; } .header { flex-direction: column; gap: 15px; } }
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <h1>🖨️ Printer Dashboard</h1>
            <div class="stats">
                <div><div class="stat-value" style="color: var(--primary);">{{ stats.total }}</div><div class="stat-label">Total</div></div>
                <div><div class="stat-value online">{{ stats.online }}</div><div class="stat-label">Online</div></div>
                <div><div class="stat-value offline">{{ stats.offline }}</div><div class="stat-label">Offline</div></div>
                <div><div class="stat-value" style="color: var(--warning);">{{ stats.alerts }}</div><div class="stat-label">Alerts</div></div>
            </div>
        </header>
        
        <div class="charts">
            <div class="chart-card">
                <h3>📊 Status Distribution</h3>
                <div class="chart-container"><canvas id="statusChart"></canvas></div>
            </div>
            <div class="chart-card">
                <h3>🎨 Average Toner Levels</h3>
                <div class="chart-container"><canvas id="tonerChart"></canvas></div>
            </div>
            <div class="chart-card">
                <h3>🏭 Vendor Distribution</h3>
                <div class="chart-container"><canvas id="vendorChart"></canvas></div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card">
                <div class="card-header"><h2>📋 Printers ({{ printers|length }})</h2></div>
                <div class="card-body">
                    {% if printers %}
                        {% for p in printers %}
                        <div class="printer {{ p.status|lower }}">
                            <div class="printer-header">
                                <div>
                                    <span class="printer-name">{{ p.name or p.ip }}</span>
                                    {% if p.manufacturer %}<span class="vendor-badge vendor-{{ p.manufacturer|lower|replace(' ', '-') }}">{{ p.manufacturer }}</span>{% endif %}
                                </div>
                                <span class="badge {{ p.status|lower }}">{{ p.status }}</span>
                            </div>
                            <div style="font-size: 0.85em; color: #666;">IP: {{ p.ip }}{% if p.model %} • {{ p.model[:25] }}{% endif %}{% if p.page_count %} • {{ "{:,}".format(p.page_count|int) }} pages{% endif %}</div>
                            {% if p.toners %}
                                {% for t in p.toners %}
                                <div class="toner-row">
                                    <span class="toner-label">{{ t.name }}:</span>
                                    <div class="toner-bar"><div class="toner-fill {{ t.name }} {% if t.level < 20 %}low{% endif %}" style="width: {{ t.level }}%"></div></div>
                                    <span class="toner-pct {% if t.level < 10 %}critical{% elif t.level < 20 %}low{% endif %}">{{ t.level }}%</span>
                                </div>
                                {% endfor %}
                            {% endif %}
                        </div>
                        {% endfor %}
                    {% else %}
                        <div class="no-data">No printers monitored</div>
                    {% endif %}
                </div>
            </div>
            <div class="card">
                <div class="card-header"><h2>⚠️ Alerts (24h)</h2></div>
                <div class="card-body">
                    {% if alerts %}
                        {% for a in alerts[:20] %}
                        <div class="alert {{ a.severity.value }}">
                            <div class="alert-header">
                                <span class="alert-type">{{ a.alert_type|upper }}</span>
                                <span class="alert-time">{{ a.timestamp.strftime('%m/%d %H:%M') if a.timestamp.strftime else a.timestamp }}</span>
                            </div>
                            <div class="alert-msg"><strong>{{ a.printer_ip }}</strong>: {{ a.message[:60] }}{% if a.message|length > 60 %}...{% endif %}</div>
                        </div>
                        {% endfor %}
                    {% else %}
                        <div class="no-data">✅ No alerts</div>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <footer class="footer">
            Printer Management Suite v6.1 • {{ timestamp }}<br>
            <span style="font-size: 0.8em;">HP • Canon • Xerox • Konica Minolta • Brother • Epson • Ricoh • Kyocera • Lexmark • Samsung</span>
        </footer>
    </div>
    
    <script>
        const stats = { online: {{ stats.online }}, offline: {{ stats.offline }}, error: {{ stats.error|default(0) }} };
        const vendorData = {{ vendor_stats|tojson|safe }};
        const tonerData = {{ toner_stats|tojson|safe }};
        
        Chart.defaults.font.family = "'Segoe UI', sans-serif";
        
        new Chart(document.getElementById('statusChart'), {
            type: 'doughnut',
            data: {
                labels: ['Online', 'Offline', 'Error'],
                datasets: [{ data: [stats.online, stats.offline, stats.error], backgroundColor: ['#48bb78', '#f56565', '#ed8936'], borderWidth: 0 }]
            },
            options: { responsive: true, maintainAspectRatio: false, cutout: '60%', plugins: { legend: { position: 'bottom' } } }
        });
        
        const vendorColors = { 'HP': '#0096d6', 'Canon': '#cc0000', 'Xerox': '#c8102e', 'Konica Minolta': '#003087', 'Brother': '#004c97', 'Generic': '#718096' };
        new Chart(document.getElementById('vendorChart'), {
            type: 'doughnut',
            data: {
                labels: Object.keys(vendorData),
                datasets: [{ data: Object.values(vendorData), backgroundColor: Object.keys(vendorData).map(v => vendorColors[v] || '#718096'), borderWidth: 0 }]
            },
            options: { responsive: true, maintainAspectRatio: false, cutout: '50%', plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } } } }
        });
        
        new Chart(document.getElementById('tonerChart'), {
            type: 'bar',
            data: {
                labels: ['Black', 'Cyan', 'Magenta', 'Yellow'],
                datasets: [{ data: [tonerData.black||0, tonerData.cyan||0, tonerData.magenta||0, tonerData.yellow||0], backgroundColor: ['#333', '#0bc5ea', '#ed64a6', '#ecc94b'], borderRadius: 6 }]
            },
            options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 100, ticks: { callback: v => v+'%' } }, x: { grid: { display: false } } }, plugins: { legend: { display: false } } }
        });
    </script>
</body>
</html>
"""


class DashboardGenerator:
    """Dashboard generator with Chart.js support"""
    
    def __init__(self, template_dir: str = "."):
        self.template_dir = template_dir
        self.env = None
        
        if JINJA2_AVAILABLE:
            template_file = os.path.join(template_dir, "dashboard_template.html")
            if os.path.exists(template_file):
                self.env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
                logger.info(f"Using external template: {template_file}")
            else:
                self.env = Environment(loader=BaseLoader(), autoescape=True)
                logger.info("Using embedded dashboard template")
    
    def generate(self, data: Dict[str, Any]) -> str:
        if not JINJA2_AVAILABLE:
            return self._basic_html(data)
        
        try:
            # Parse toners from JSON
            for p in data.get('printers', []):
                if p.get('notes'):
                    try:
                        p['toners'] = json.loads(p['notes'])
                    except:
                        p['toners'] = []
            
            data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Ensure stats exist
            if 'stats' not in data:
                printers = data.get('printers', [])
                data['stats'] = {
                    'total': len(printers),
                    'online': sum(1 for p in printers if p.get('status') == 'ONLINE'),
                    'offline': sum(1 for p in printers if p.get('status') == 'OFFLINE'),
                    'error': sum(1 for p in printers if p.get('status') == 'ERROR'),
                    'alerts': len(data.get('alerts', []))
                }
            
            # Ensure vendor_stats exist
            if 'vendor_stats' not in data:
                vendor_counts = defaultdict(int)
                for p in data.get('printers', []):
                    vendor = p.get('manufacturer') or 'Generic'
                    vendor_counts[vendor] += 1
                data['vendor_stats'] = dict(vendor_counts) or {'Generic': 0}
            
            # Ensure toner_stats exist
            if 'toner_stats' not in data:
                data['toner_stats'] = {'black': 0, 'cyan': 0, 'magenta': 0, 'yellow': 0}
            
            # Load template
            template_file = os.path.join(self.template_dir, "dashboard_template.html")
            if os.path.exists(template_file):
                template = self.env.get_template("dashboard_template.html")
            else:
                template = self.env.from_string(DASHBOARD_TEMPLATE)
            
            return template.render(**data)
            
        except Exception as e:
            logger.error(f"Dashboard generation error: {e}")
            return self._basic_html(data)
    
    def _basic_html(self, data: Dict) -> str:
        printers = data.get('printers', [])
        return f"""
        <html>
        <head><title>Printer Dashboard</title></head>
        <body style="font-family: sans-serif; padding: 20px;">
            <h1>🖨️ Printer Dashboard</h1>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Total: {len(printers)} | Online: {sum(1 for p in printers if p.get('status') == 'ONLINE')} | Offline: {sum(1 for p in printers if p.get('status') == 'OFFLINE')}</p>
            <h2>Printers</h2>
            {''.join(f"<p>{p.get('name', p.get('ip'))} - {p.get('status')} - {p.get('manufacturer', 'Unknown')}</p>" for p in printers) or '<p>No printers</p>'}
        </body>
        </html>
        """
    
    def save(self, data: Dict[str, Any], filepath: str = None) -> str:
        html = self.generate(data)
        
        if filepath is None:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
                f.write(html)
                filepath = f.name
        else:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html)
        
        logger.info(f"Dashboard saved: {filepath}")
        return filepath


# ==============================================================================
# UI COMPONENTS - NETWORK PRINTERS
# ==============================================================================

class PrinterMonitorUI:
    """Health Monitor UI for network printers"""
    
    def __init__(self, parent, monitor_service: PrinterMonitorService):
        self.service = monitor_service
        self.frame = ttk.Frame(parent)
        self._setup_ui()
        self._subscribe_events()
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Network Printer Monitor (SNMP)", font=("Arial", 14, "bold")).pack(pady=10)
        
        # Input frame
        input_frame = ttk.Frame(self.frame)
        input_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(input_frame, text="IP:").pack(side=tk.LEFT)
        self.ip_entry = ttk.Entry(input_frame, width=15)
        self.ip_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(input_frame, text="Name:").pack(side=tk.LEFT)
        self.name_entry = ttk.Entry(input_frame, width=15)
        self.name_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(input_frame, text="Vendor:").pack(side=tk.LEFT)
        vendors = ["Auto"] + [m.value for m in Manufacturer]
        self.vendor_combo = ttk.Combobox(input_frame, values=vendors, width=15, state="readonly")
        self.vendor_combo.set("Auto")
        self.vendor_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(input_frame, text="Check", command=self._on_check).pack(side=tk.LEFT, padx=5)
        ttk.Button(input_frame, text="Check All", command=self._on_check_all).pack(side=tk.LEFT, padx=5)
        
        # Progress
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(self.frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_label = ttk.Label(self.frame, text="")
        self.progress_label.pack()
        
        # Results
        self.result_text = scrolledtext.ScrolledText(self.frame, height=18, width=100)
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Button frame
        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(pady=5)
        ttk.Button(btn_frame, text="Clear", command=lambda: self.result_text.delete(1.0, tk.END)).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="View History", command=self._view_history).pack(side=tk.LEFT, padx=5)
    
    def _subscribe_events(self):
        event_bus.subscribe(EventType.PRINTER_CHECK_COMPLETED, self._on_check_completed)
        event_bus.subscribe(EventType.BATCH_CHECK_PROGRESS, self._on_batch_progress)
        event_bus.subscribe(EventType.BATCH_CHECK_COMPLETED, self._on_batch_completed)
    
    def _on_check(self):
        ip = self.ip_entry.get().strip()
        if not ip:
            messagebox.showerror("Error", "Enter IP address")
            return
        
        name = self.name_entry.get().strip() or ip
        vendor = self.vendor_combo.get()
        vendor = None if vendor == "Auto" else vendor
        
        self._log(f"Checking {ip}...")
        
        def do_check():
            self.service.check_printer(ip, name, vendor)
        
        threading.Thread(target=do_check, daemon=True).start()
    
    def _on_check_all(self):
        self._log("Starting batch check...")
        self.progress_var.set(0)
        self.progress_label.config(text="Starting...")
        
        def do_check():
            self.service.check_all_printers()
        
        threading.Thread(target=do_check, daemon=True).start()
    
    def _on_check_completed(self, event: Event):
        result = event.data
        self._display_result(result)
    
    def _on_batch_progress(self, event: Event):
        data = event.data
        progress = (data['completed'] / data['total']) * 100
        self.progress_var.set(progress)
        self.progress_label.config(text=f"{data['completed']}/{data['total']}")
    
    def _on_batch_completed(self, event: Event):
        results = event.data['results']
        self._log(f"\n{'='*60}\nBatch complete: {len(results)} printers\n{'='*60}")
        self.progress_var.set(100)
        self.progress_label.config(text="Complete")
    
    def _log(self, msg: str):
        self.result_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.result_text.see(tk.END)
    
    def _display_result(self, r):
        text = f"\n{'='*60}\n"
        text += f"Printer: {r.name} ({r.ip})\n"
        
        status = r.status if isinstance(r.status, str) else r.status.value if hasattr(r.status, 'value') else str(r.status)
        text += f"Status: {status} | Vendor: {r.manufacturer or 'Unknown'}"
        
        if hasattr(r, 'detection_confidence') and r.detection_confidence:
            text += f" ({r.detection_confidence:.0%})"
        text += f"\n"
        text += f"Method: {r.check_method} | Duration: {r.check_duration_ms}ms\n"
        
        if r.model:
            text += f"Model: {r.model}\n"
        if r.serial:
            text += f"Serial: {r.serial}\n"
        
        if r.toner_levels:
            text += "Toner:\n"
            for t in r.toner_levels:
                warn = " ⚠️" if t.is_low else ""
                crit = " 🚨" if t.is_critical else ""
                text += f"  {t.name}: {t.level}%{warn}{crit}\n"
        
        if r.page_count:
            text += f"Pages: {r.page_count:,}\n"
        
        if r.error_message:
            text += f"Note: {r.error_message}\n"
        
        text += f"{'='*60}\n"
        self.result_text.insert(tk.END, text)
        self.result_text.see(tk.END)
    
    def _view_history(self):
        ip = self.ip_entry.get().strip()
        if not ip:
            messagebox.showerror("Error", "Enter IP address")
            return
        
        history = self.service.get_printer_history(ip)
        if not history:
            messagebox.showinfo("History", "No history found")
            return
        
        text = f"History for {ip} (last 24h):\n\n"
        for h in history[:20]:
            text += f"[{h.get('timestamp')}] {h.get('status')} - Toner: {h.get('toner_level')}%\n"
        
        win = tk.Toplevel()
        win.title(f"History: {ip}")
        win.geometry("500x400")
        txt = scrolledtext.ScrolledText(win)
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        txt.insert(tk.END, text)


# ==============================================================================
# UI COMPONENTS - LOCAL PRINTERS (NEW IN v6.1)
# ==============================================================================

class LocalPrintersUI:
    """UI for managing local Windows printers"""
    
    def __init__(self, parent, local_service):
        self.service = local_service
        self.frame = ttk.Frame(parent)
        self._setup_ui()
        self._refresh_data()
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Local Windows Printers", font=("Arial", 14, "bold")).pack(pady=10)
        
        # Top buttons
        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(btn_frame, text="🔄 Refresh", command=self._refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="➕ Add Printer", command=self._add_printer_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🗑️ Remove", command=self._remove_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="⭐ Set Default", command=self._set_default).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="⏸️ Pause", command=self._pause_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="▶️ Resume", command=self._resume_selected).pack(side=tk.LEFT, padx=5)
        
        # Summary frame
        self.summary_frame = ttk.LabelFrame(self.frame, text="Summary", padding=10)
        self.summary_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.summary_label = ttk.Label(self.summary_frame, text="Loading...")
        self.summary_label.pack(anchor=tk.W)
        
        # Notebook for printers/jobs/drivers/ports/spooler
        notebook = ttk.Notebook(self.frame)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Printers tab
        printers_frame = ttk.Frame(notebook)
        notebook.add(printers_frame, text="Printers")
        
        columns = ('Name', 'Type', 'Status', 'Driver', 'Port', 'Jobs', 'Default')
        self.printer_tree = ttk.Treeview(printers_frame, columns=columns, show='headings', height=12)
        
        for col in columns:
            self.printer_tree.heading(col, text=col, command=lambda c=col: self._sort_tree(c))
            width = 150 if col in ('Name', 'Driver') else 100
            self.printer_tree.column(col, width=width)
        
        scrollbar = ttk.Scrollbar(printers_frame, orient=tk.VERTICAL, command=self.printer_tree.yview)
        self.printer_tree.configure(yscrollcommand=scrollbar.set)
        
        self.printer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.printer_tree.bind('<Button-3>', self._show_context_menu)
        self.printer_tree.bind('<Double-1>', self._show_printer_details)
        
        # Jobs tab
        jobs_frame = ttk.Frame(notebook)
        notebook.add(jobs_frame, text="Print Jobs")
        
        job_columns = ('ID', 'Printer', 'Document', 'User', 'Status', 'Pages', 'Size')
        self.job_tree = ttk.Treeview(jobs_frame, columns=job_columns, show='headings', height=10)
        
        for col in job_columns:
            self.job_tree.heading(col, text=col)
            width = 150 if col in ('Printer', 'Document') else 80
            self.job_tree.column(col, width=width)
        
        job_scroll = ttk.Scrollbar(jobs_frame, orient=tk.VERTICAL, command=self.job_tree.yview)
        self.job_tree.configure(yscrollcommand=job_scroll.set)
        
        self.job_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        job_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        job_btn_frame = ttk.Frame(jobs_frame)
        job_btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(job_btn_frame, text="Cancel Job", command=self._cancel_job).pack(side=tk.LEFT, padx=5)
        ttk.Button(job_btn_frame, text="Cancel All", command=self._cancel_all_jobs).pack(side=tk.LEFT, padx=5)
        ttk.Button(job_btn_frame, text="Refresh Jobs", command=self._refresh_jobs).pack(side=tk.LEFT, padx=5)
        
        # Drivers tab
        drivers_frame = ttk.Frame(notebook)
        notebook.add(drivers_frame, text="Drivers")
        
        driver_columns = ('Name', 'Version', 'Manufacturer')
        self.driver_tree = ttk.Treeview(drivers_frame, columns=driver_columns, show='headings', height=12)
        
        for col in driver_columns:
            self.driver_tree.heading(col, text=col)
            self.driver_tree.column(col, width=200)
        
        self.driver_tree.pack(fill=tk.BOTH, expand=True)
        
        # Ports tab
        ports_frame = ttk.Frame(notebook)
        notebook.add(ports_frame, text="Ports")
        
        port_columns = ('Name', 'Type', 'Address', 'Port', 'Protocol')
        self.port_tree = ttk.Treeview(ports_frame, columns=port_columns, show='headings', height=12)
        
        for col in port_columns:
            self.port_tree.heading(col, text=col)
            self.port_tree.column(col, width=120)
        
        self.port_tree.pack(fill=tk.BOTH, expand=True)
        
        # Spooler tab
        spooler_frame = ttk.Frame(notebook)
        notebook.add(spooler_frame, text="Spooler")
        
        self.spooler_text = scrolledtext.ScrolledText(spooler_frame, height=10, width=80)
        self.spooler_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        spooler_btns = ttk.Frame(spooler_frame)
        spooler_btns.pack(pady=5)
        ttk.Button(spooler_btns, text="Restart Spooler", command=self._restart_spooler).pack(side=tk.LEFT, padx=5)
        ttk.Button(spooler_btns, text="Clear & Restart", command=self._clear_restart_spooler).pack(side=tk.LEFT, padx=5)
        ttk.Button(spooler_btns, text="Refresh Status", command=self._refresh_spooler).pack(side=tk.LEFT, padx=5)
    
    def _refresh_data(self):
        """Refresh all data"""
        if not self.service:
            self.summary_label.config(text="Windows agents not available")
            return
        
        def do_refresh():
            try:
                printers = self.service.get_all_printers()
                jobs = self.service.get_all_jobs()
                drivers = self.service.get_all_drivers()
                ports = self.service.get_all_ports()
                summary = self.service.get_summary()
                spooler = self.service.get_spooler_status()
                
                self.frame.after(0, lambda: self._update_ui(printers, jobs, drivers, ports, summary, spooler))
            except Exception as e:
                logger.error(f"Refresh error: {e}")
                self.frame.after(0, lambda: self.summary_label.config(text=f"Error: {e}"))
        
        threading.Thread(target=do_refresh, daemon=True).start()
    
    def _update_ui(self, printers, jobs, drivers, ports, summary, spooler):
        """Update UI with data"""
        # Clear trees
        for tree in [self.printer_tree, self.job_tree, self.driver_tree, self.port_tree]:
            for item in tree.get_children():
                tree.delete(item)
        
        # Update summary
        summary_text = (
            f"Printers: {summary['total_printers']} | "
            f"Jobs: {summary['total_jobs']} | "
            f"Spooler: {spooler.status} | "
            f"Default: {summary.get('default_printer', 'None')}"
        )
        self.summary_label.config(text=summary_text)
        
        # Update printers
        for p in printers:
            status = p.status.value if hasattr(p.status, 'value') else str(p.status)
            ptype = p.printer_type.value if hasattr(p.printer_type, 'value') else str(p.printer_type)
            default = "⭐" if p.is_default else ""
            driver = p.driver_name[:30] + "..." if len(p.driver_name) > 30 else p.driver_name
            
            self.printer_tree.insert('', tk.END, values=(
                p.name, ptype, status, driver, p.port_name, p.jobs_count, default
            ))
        
        # Update jobs
        for j in jobs:
            status = j.status.value if hasattr(j.status, 'value') else str(j.status)
            size_kb = j.size_bytes / 1024 if j.size_bytes else 0
            doc = j.document_name[:30] + "..." if len(j.document_name) > 30 else j.document_name
            
            self.job_tree.insert('', tk.END, values=(
                j.job_id, j.printer_name, doc, j.user_name,
                status, f"{j.pages_printed}/{j.total_pages}", f"{size_kb:.1f} KB"
            ))
        
        # Update drivers
        for d in drivers:
            self.driver_tree.insert('', tk.END, values=(
                d.name, d.version, d.manufacturer
            ))
        
        # Update ports
        for p in ports:
            self.port_tree.insert('', tk.END, values=(
                p.name, p.port_type, p.host_address or "",
                p.port_number if p.host_address else "", p.protocol
            ))
        
        # Update spooler
        self._update_spooler_text(spooler)
    
    def _update_spooler_text(self, spooler):
        self.spooler_text.delete(1.0, tk.END)
        spooler_info = f"""
Spooler Status
{'='*50}
Status: {spooler.status}
Start Type: {spooler.start_type}
PID: {spooler.pid or 'N/A'}
Memory Usage: {spooler.memory_usage_mb:.1f} MB

Spool Folder: {spooler.spool_folder}
Spool Files: {spooler.spool_files_count}
Spool Size: {spooler.spool_size_mb:.2f} MB
{'='*50}
        """
        self.spooler_text.insert(tk.END, spooler_info)
    
    def _refresh_jobs(self):
        if not self.service:
            return
        
        jobs = self.service.get_all_jobs()
        
        for item in self.job_tree.get_children():
            self.job_tree.delete(item)
        
        for j in jobs:
            status = j.status.value if hasattr(j.status, 'value') else str(j.status)
            size_kb = j.size_bytes / 1024 if j.size_bytes else 0
            
            self.job_tree.insert('', tk.END, values=(
                j.job_id, j.printer_name, j.document_name[:30],
                j.user_name, status, f"{j.pages_printed}/{j.total_pages}", f"{size_kb:.1f} KB"
            ))
    
    def _refresh_spooler(self):
        if not self.service:
            return
        
        spooler = self.service.get_spooler_status()
        self._update_spooler_text(spooler)
    
    def _get_selected_printer(self) -> Optional[str]:
        selection = self.printer_tree.selection()
        if selection:
            values = self.printer_tree.item(selection[0], 'values')
            return values[0] if values else None
        return None
    
    def _show_context_menu(self, event):
        item = self.printer_tree.identify_row(event.y)
        if item:
            self.printer_tree.selection_set(item)
            
            menu = tk.Menu(self.frame, tearoff=0)
            menu.add_command(label="Set as Default", command=self._set_default)
            menu.add_command(label="Pause", command=self._pause_selected)
            menu.add_command(label="Resume", command=self._resume_selected)
            menu.add_separator()
            menu.add_command(label="Properties", command=self._show_printer_details)
            menu.add_command(label="Cancel All Jobs", command=self._cancel_all_jobs_for_printer)
            menu.add_separator()
            menu.add_command(label="Remove", command=self._remove_selected)
            
            menu.tk_popup(event.x_root, event.y_root)
    
    def _show_printer_details(self, event=None):
        name = self._get_selected_printer()
        if not name or not self.service:
            return
        
        printer = self.service.get_printer(name)
        if not printer:
            return
        
        win = tk.Toplevel(self.frame)
        win.title(f"Printer: {name}")
        win.geometry("500x450")
        
        text = scrolledtext.ScrolledText(win)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        status = printer.status.value if hasattr(printer.status, 'value') else str(printer.status)
        ptype = printer.printer_type.value if hasattr(printer.printer_type, 'value') else str(printer.printer_type)
        
        details = f"""
Printer Details: {printer.name}
{'='*50}

General:
  Name: {printer.name}
  Type: {ptype}
  Status: {status} (Code: {printer.status_code})
  Default: {'Yes' if printer.is_default else 'No'}
  Shared: {'Yes' if printer.is_shared else 'No'}
  Share Name: {printer.share_name or 'N/A'}

Driver:
  Name: {printer.driver_name}
  Manufacturer: {printer.manufacturer or 'Unknown'}

Port:
  Name: {printer.port_name}

Location: {printer.location or 'Not set'}
Comment: {printer.comment or 'None'}

Capabilities:
  Color: {'Yes' if printer.is_color else 'No'}
  Duplex: {'Yes' if printer.is_duplex else 'No'}

Statistics:
  Current Jobs: {printer.jobs_count}
  Total Pages Printed: {printer.total_pages_printed}
  Total Jobs Printed: {printer.total_jobs_printed}

{'='*50}
        """
        
        text.insert(tk.END, details)
    
    def _add_printer_dialog(self):
        win = tk.Toplevel(self.frame)
        win.title("Add Network Printer")
        win.geometry("450x300")
        win.transient(self.frame)
        win.grab_set()
        
        ttk.Label(win, text="Add Network Printer", font=("Arial", 12, "bold")).pack(pady=10)
        
        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.X, padx=20)
        
        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        name_entry = ttk.Entry(form, width=35)
        name_entry.grid(row=0, column=1, pady=5, padx=5)
        
        ttk.Label(form, text="IP Address:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ip_entry = ttk.Entry(form, width=35)
        ip_entry.grid(row=1, column=1, pady=5, padx=5)
        
        ttk.Label(form, text="Driver:").grid(row=2, column=0, sticky=tk.W, pady=5)
        
        drivers = []
        if self.service:
            try:
                drivers = [d.name for d in self.service.get_all_drivers()]
            except:
                pass
        
        driver_combo = ttk.Combobox(form, values=drivers, width=32)
        if drivers:
            driver_combo.set(drivers[0])
        driver_combo.grid(row=2, column=1, pady=5, padx=5)
        
        ttk.Label(form, text="Port Number:").grid(row=3, column=0, sticky=tk.W, pady=5)
        port_entry = ttk.Entry(form, width=35)
        port_entry.insert(0, "9100")
        port_entry.grid(row=3, column=1, pady=5, padx=5)
        
        def do_add():
            name = name_entry.get().strip()
            ip = ip_entry.get().strip()
            driver = driver_combo.get()
            port = int(port_entry.get() or 9100)
            
            if not name or not ip or not driver:
                messagebox.showerror("Error", "Fill all required fields", parent=win)
                return
            
            if self.service.add_network_printer(name, ip, driver, port):
                messagebox.showinfo("Success", f"Printer '{name}' added", parent=win)
                win.destroy()
                self._refresh_data()
            else:
                messagebox.showerror("Error", "Failed to add printer", parent=win)
        
        ttk.Button(win, text="Add Printer", command=do_add).pack(pady=20)
    
    def _remove_selected(self):
        name = self._get_selected_printer()
        if not name:
            messagebox.showinfo("Info", "Select a printer")
            return
        
        if not messagebox.askyesno("Confirm", f"Remove printer '{name}'?"):
            return
        
        if self.service.remove_printer(name):
            messagebox.showinfo("Success", f"Printer '{name}' removed")
            self._refresh_data()
        else:
            messagebox.showerror("Error", "Failed to remove printer")
    
    def _set_default(self):
        name = self._get_selected_printer()
        if not name:
            messagebox.showinfo("Info", "Select a printer")
            return
        
        if self.service.set_default_printer(name):
            messagebox.showinfo("Success", f"'{name}' is now default")
            self._refresh_data()
        else:
            messagebox.showerror("Error", "Failed to set default")
    
    def _pause_selected(self):
        name = self._get_selected_printer()
        if not name:
            return
        
        if self.service.pause_printer(name):
            self._refresh_data()
    
    def _resume_selected(self):
        name = self._get_selected_printer()
        if not name:
            return
        
        if self.service.resume_printer(name):
            self._refresh_data()
    
    def _cancel_job(self):
        selection = self.job_tree.selection()
        if not selection:
            return
        
        values = self.job_tree.item(selection[0], 'values')
        job_id = int(values[0])
        printer_name = values[1]
        
        if self.service.cancel_job(printer_name, job_id):
            self._refresh_jobs()
    
    def _cancel_all_jobs(self):
        if not messagebox.askyesno("Confirm", "Cancel ALL print jobs?"):
            return
        
        printers = self.service.get_all_printers()
        for p in printers:
            self.service.cancel_all_jobs(p.name)
        
        self._refresh_jobs()
        messagebox.showinfo("Done", "All jobs cancelled")
    
    def _cancel_all_jobs_for_printer(self):
        name = self._get_selected_printer()
        if not name:
            return
        
        self.service.cancel_all_jobs(name)
        self._refresh_jobs()
    
    def _restart_spooler(self):
        if not messagebox.askyesno("Confirm", "Restart Print Spooler?"):
            return
        
        if self.service.restart_spooler():
            messagebox.showinfo("Success", "Spooler restarted")
            self._refresh_spooler()
        else:
            messagebox.showerror("Error", "Failed to restart spooler")
    
    def _clear_restart_spooler(self):
        if not messagebox.askyesno("Confirm", "Clear spool folder and restart?\nThis will cancel all pending jobs."):
            return
        
        success, deleted = self.service.clear_and_restart_spooler()
        
        if success:
            messagebox.showinfo("Success", f"Spooler restarted. {deleted} files deleted.")
            self._refresh_data()
        else:
            messagebox.showerror("Error", "Failed")
    
    def _sort_tree(self, col):
        items = [(self.printer_tree.set(k, col), k) for k in self.printer_tree.get_children('')]
        items.sort()
        
        for index, (val, k) in enumerate(items):
            self.printer_tree.move(k, '', index)


# ==============================================================================
# UI COMPONENTS - DEPLOYMENT
# ==============================================================================

class PrinterDeploymentUI:
    """Deployment UI"""
    
    def __init__(self, parent, deploy_service: PrinterDeploymentService):
        self.service = deploy_service
        self.frame = ttk.Frame(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Printer Deployment", font=("Arial", 14, "bold")).pack(pady=10)
        
        form = ttk.LabelFrame(self.frame, text="Configuration", padding=10)
        form.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(form, text="IP:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.ip_entry = ttk.Entry(form, width=20)
        self.ip_entry.grid(row=0, column=1, pady=5, padx=5)
        
        ttk.Label(form, text="Name:").grid(row=0, column=2, sticky=tk.W, pady=5)
        self.name_entry = ttk.Entry(form, width=20)
        self.name_entry.grid(row=0, column=3, pady=5, padx=5)
        
        ttk.Label(form, text="Driver:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.driver_entry = ttk.Entry(form, width=50)
        self.driver_entry.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=5, padx=5)
        
        btns = ttk.Frame(self.frame)
        btns.pack(pady=10)
        ttk.Button(btns, text="Map Printer", command=self._map).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Remove", command=self._remove).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Reset Queue", command=self._reset_queue).pack(side=tk.LEFT, padx=5)
        
        self.log = scrolledtext.ScrolledText(self.frame, height=15, width=100)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    
    def _log(self, msg: str):
        self.log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see(tk.END)
    
    def _map(self):
        ip = self.ip_entry.get().strip()
        name = self.name_entry.get().strip()
        driver = self.driver_entry.get().strip()
        
        if not ip or not name:
            messagebox.showerror("Error", "Enter IP and name")
            return
        
        self._log(f"Mapping {name}...")
        
        def do_map():
            result = self.service.map_printer(ip, name, driver)
            self._log(f"{'✓' if result.success else '✗'} {result.message}")
            if result.details:
                self._log(f"  {result.details}")
        
        threading.Thread(target=do_map, daemon=True).start()
    
    def _remove(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Error", "Enter name")
            return
        
        result = self.service.remove_printer(name)
        self._log(f"{'✓' if result.success else '✗'} {result.message}")
    
    def _reset_queue(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Error", "Enter name")
            return
        
        result = self.service.reset_queue(name)
        self._log(f"{'✓' if result.success else '✗'} {result.message}")


# ==============================================================================
# UI COMPONENTS - SPOOLER
# ==============================================================================

class SpoolerUI:
    """Spooler Reset UI (legacy)"""
    
    def __init__(self, parent, spooler_service: SpoolerService):
        self.service = spooler_service
        self.frame = ttk.Frame(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Spooler Management", font=("Arial", 14, "bold")).pack(pady=10)
        
        warn = ttk.LabelFrame(self.frame, text="⚠️ Warning", padding=10)
        warn.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(warn, text="These operations may interrupt active print jobs.", foreground="red").pack()
        
        btns = ttk.Frame(self.frame)
        btns.pack(pady=15)
        ttk.Button(btns, text="Stop", command=self._stop, width=12).pack(side=tk.LEFT, padx=10)
        ttk.Button(btns, text="Clean", command=self._clean, width=12).pack(side=tk.LEFT, padx=10)
        ttk.Button(btns, text="Start", command=self._start, width=12).pack(side=tk.LEFT, padx=10)
        ttk.Button(btns, text="Full Reset", command=self._full, width=12).pack(side=tk.LEFT, padx=10)
        
        self.log = scrolledtext.ScrolledText(self.frame, height=18, width=100)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    
    def _log(self, msg: str):
        self.log.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see(tk.END)
    
    def _stop(self):
        r = self.service.stop()
        self._log(f"{'✓' if r.success else '✗'} {r.message}")
    
    def _start(self):
        r = self.service.start()
        self._log(f"{'✓' if r.success else '✗'} {r.message}")
    
    def _clean(self):
        r = self.service.clean()
        self._log(f"{'✓' if r.success else '✗'} {r.message}")
    
    def _full(self):
        self._log("=== Full Reset ===")
        r = self.service.full_reset()
        self._log(f"{'✓' if r.success else '✗'} {r.message}")
        if r.details:
            self._log(f"  {r.details}")


# ==============================================================================
# UI COMPONENTS - DISCOVERY
# ==============================================================================

class DiscoveryUI:
    """Network Discovery UI"""
    
    def __init__(self, parent, monitor_service: PrinterMonitorService, config: Dict):
        self.service = monitor_service
        self.config = config
        self.frame = ttk.Frame(parent)
        self._setup_ui()
        self._subscribe_events()
        self._scanning = False
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Network Discovery", font=("Arial", 14, "bold")).pack(pady=10)
        
        # Input
        input_frame = ttk.Frame(self.frame)
        input_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(input_frame, text="Subnet (e.g., 192.168.1):").pack(side=tk.LEFT)
        self.subnet_entry = ttk.Entry(input_frame, width=20)
        self.subnet_entry.pack(side=tk.LEFT, padx=10)
        
        default_subnet = self.config.get('discovery', {}).get('subnet', '')
        if default_subnet:
            self.subnet_entry.insert(0, default_subnet)
        
        ttk.Button(input_frame, text="Scan", command=self._scan).pack(side=tk.LEFT, padx=5)
        ttk.Button(input_frame, text="Stop", command=self._stop).pack(side=tk.LEFT, padx=5)
        
        # Progress
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(self.frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_label = ttk.Label(self.frame, text="")
        self.progress_label.pack()
        
        # Results
        columns = ('IP', 'Name', 'Manufacturer', 'Model')
        self.tree = ttk.Treeview(self.frame, columns=columns, show='headings', height=12)
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=150)
        
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(pady=5)
        ttk.Button(btn_frame, text="Add Selected to Config", command=self._add_to_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Load Saved", command=self._load_saved).pack(side=tk.LEFT, padx=5)
    
    def _subscribe_events(self):
        event_bus.subscribe(EventType.DISCOVERY_PROGRESS, self._on_progress)
        event_bus.subscribe(EventType.DISCOVERY_COMPLETED, self._on_completed)
    
    def _scan(self):
        subnet = self.subnet_entry.get().strip()
        if not subnet:
            messagebox.showerror("Error", "Enter subnet")
            return
        
        if not SNMP_ENGINE_AVAILABLE:
            messagebox.showerror("Error", "SNMP engine not available")
            return
        
        self._scanning = True
        self.progress_var.set(0)
        self.progress_label.config(text="Scanning...")
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        def do_scan():
            self.service.discover_printers(subnet)
        
        threading.Thread(target=do_scan, daemon=True).start()
    
    def _stop(self):
        self._scanning = False
        self.progress_label.config(text="Stopped")
    
    def _on_progress(self, event: Event):
        if not self._scanning:
            return
        
        data = event.data
        progress = (data['completed'] / data['total']) * 100
        self.progress_var.set(progress)
        self.progress_label.config(text=f"Scanning {data['ip']}... ({data['completed']}/{data['total']})")
    
    def _on_completed(self, event: Event):
        self._scanning = False
        found = event.data['found']
        
        self.progress_var.set(100)
        self.progress_label.config(text=f"Found {len(found)} printers")
        
        for p in found:
            self.tree.insert('', tk.END, values=(
                p.get('ip'),
                p.get('name'),
                p.get('manufacturer'),
                p.get('model_hint', '')
            ))
    
    def _add_to_config(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Info", "Select printers to add")
            return
        
        added = 0
        existing_ips = {p.get('ip') for p in self.config.get('printers', [])}
        
        for item in selected:
            values = self.tree.item(item, 'values')
            ip = values[0]
            
            if ip not in existing_ips:
                self.config.setdefault('printers', []).append({
                    'ip': ip,
                    'name': values[1] or f"Printer_{ip.split('.')[-1]}",
                    'manufacturer': values[2] or None
                })
                added += 1
        
        if added > 0:
            ConfigManager.save_config(self.config)
            messagebox.showinfo("Added", f"Added {added} printers to config")
        else:
            messagebox.showinfo("Info", "All selected printers already in config")
    
    def _load_saved(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        saved = self.service.db.get_discovered_printers()
        
        for p in saved:
            self.tree.insert('', tk.END, values=(
                p.get('ip'),
                p.get('name'),
                p.get('manufacturer'),
                p.get('model', '')
            ))
        
        self.progress_label.config(text=f"Loaded {len(saved)} saved printers")


# ==============================================================================
# UI COMPONENTS - SETTINGS
# ==============================================================================

class SettingsUI:
    """Settings UI"""
    
    def __init__(self, parent, config: Dict, on_save: Callable = None):
        self.config = config
        self.on_save = on_save
        self.frame = ttk.Frame(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Settings", font=("Arial", 14, "bold")).pack(pady=10)
        
        notebook = ttk.Notebook(self.frame)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # SNMP Tab
        snmp_frame = ttk.Frame(notebook, padding=10)
        notebook.add(snmp_frame, text="SNMP")
        
        ttk.Label(snmp_frame, text="Community:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.community = ttk.Entry(snmp_frame, width=20)
        self.community.insert(0, self.config.get('snmp', {}).get('community', 'public'))
        self.community.grid(row=0, column=1, pady=5, sticky=tk.W)
        
        ttk.Label(snmp_frame, text="Timeout (s):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.timeout = ttk.Spinbox(snmp_frame, from_=1, to=10, width=10)
        self.timeout.set(self.config.get('snmp', {}).get('timeout', 2))
        self.timeout.grid(row=1, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(snmp_frame, text="Workers:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.workers = ttk.Spinbox(snmp_frame, from_=1, to=50, width=10)
        self.workers.set(self.config.get('snmp', {}).get('max_workers', 10))
        self.workers.grid(row=2, column=1, sticky=tk.W, pady=5)
        
        # Scheduler Tab
        sched_frame = ttk.Frame(notebook, padding=10)
        notebook.add(sched_frame, text="Scheduler")
        
        self.sched_enabled = tk.BooleanVar(value=self.config.get('scheduler', {}).get('enabled', False))
        ttk.Checkbutton(sched_frame, text="Enable automatic monitoring", variable=self.sched_enabled).pack(anchor=tk.W, pady=5)
        
        int_frame = ttk.Frame(sched_frame)
        int_frame.pack(anchor=tk.W, pady=5)
        ttk.Label(int_frame, text="Interval (min):").pack(side=tk.LEFT)
        self.interval = ttk.Spinbox(int_frame, from_=5, to=1440, width=8)
        self.interval.set(self.config.get('scheduler', {}).get('interval_minutes', 30))
        self.interval.pack(side=tk.LEFT, padx=10)
        
        deb_frame = ttk.Frame(sched_frame)
        deb_frame.pack(anchor=tk.W, pady=5)
        ttk.Label(deb_frame, text="Debounce count:").pack(side=tk.LEFT)
        self.debounce = ttk.Spinbox(deb_frame, from_=1, to=10, width=8)
        self.debounce.set(self.config.get('scheduler', {}).get('debounce_count', 2))
        self.debounce.pack(side=tk.LEFT, padx=10)
        
        # Notifications Tab
        notif_frame = ttk.Frame(notebook, padding=10)
        notebook.add(notif_frame, text="Notifications")
        
        self.email_enabled = tk.BooleanVar(value=self.config.get('email', {}).get('enabled', False))
        ttk.Checkbutton(notif_frame, text="Email notifications", variable=self.email_enabled).pack(anchor=tk.W, pady=5)
        
        email_form = ttk.Frame(notif_frame)
        email_form.pack(anchor=tk.W, pady=5, fill=tk.X)
        
        ttk.Label(email_form, text="SMTP Server:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.smtp_server = ttk.Entry(email_form, width=30)
        self.smtp_server.insert(0, self.config.get('email', {}).get('smtp_server', ''))
        self.smtp_server.grid(row=0, column=1, pady=2, padx=5)
        
        ttk.Label(email_form, text="Sender:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.email_sender = ttk.Entry(email_form, width=30)
        self.email_sender.insert(0, self.config.get('email', {}).get('sender', ''))
        self.email_sender.grid(row=1, column=1, pady=2, padx=5)
        
        ttk.Label(email_form, text="Password:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.email_password = ttk.Entry(email_form, width=30, show="*")
        self.email_password.insert(0, self.config.get('email', {}).get('password', ''))
        self.email_password.grid(row=2, column=1, pady=2, padx=5)
        
        ttk.Label(email_form, text="Recipients:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.email_recipients = ttk.Entry(email_form, width=40)
        self.email_recipients.insert(0, ', '.join(self.config.get('email', {}).get('recipients', [])))
        self.email_recipients.grid(row=3, column=1, pady=2, padx=5)
        
        ttk.Separator(notif_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        
        self.slack_enabled = tk.BooleanVar(value=self.config.get('slack', {}).get('enabled', False))
        ttk.Checkbutton(notif_frame, text="Slack notifications", variable=self.slack_enabled).pack(anchor=tk.W, pady=5)
        
        slack_frame = ttk.Frame(notif_frame)
        slack_frame.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(slack_frame, text="Webhook URL:").pack(side=tk.LEFT)
        self.slack_webhook = ttk.Entry(slack_frame, width=50)
        self.slack_webhook.insert(0, self.config.get('slack', {}).get('webhook_url', ''))
        self.slack_webhook.pack(side=tk.LEFT, padx=5)
        
        self.teams_enabled = tk.BooleanVar(value=self.config.get('teams', {}).get('enabled', False))
        ttk.Checkbutton(notif_frame, text="Teams notifications", variable=self.teams_enabled).pack(anchor=tk.W, pady=5)
        
        teams_frame = ttk.Frame(notif_frame)
        teams_frame.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(teams_frame, text="Webhook URL:").pack(side=tk.LEFT)
        self.teams_webhook = ttk.Entry(teams_frame, width=50)
        self.teams_webhook.insert(0, self.config.get('teams', {}).get('webhook_url', ''))
        self.teams_webhook.pack(side=tk.LEFT, padx=5)
        
        # Suppression Tab
        supp_frame = ttk.Frame(notebook, padding=10)
        notebook.add(supp_frame, text="Suppression")
        
        ttk.Label(supp_frame, text="Alert Suppression Windows", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=5)
        
        self.night_enabled = tk.BooleanVar(value=self.config.get('suppression', {}).get('night_window', {}).get('enabled', False))
        ttk.Checkbutton(supp_frame, text="Night suppression (22:00-06:00)", variable=self.night_enabled).pack(anchor=tk.W, pady=5)
        
        self.weekend_enabled = tk.BooleanVar(value=self.config.get('suppression', {}).get('weekend', {}).get('enabled', False))
        ttk.Checkbutton(supp_frame, text="Weekend suppression", variable=self.weekend_enabled).pack(anchor=tk.W, pady=5)
        
        ttk.Label(supp_frame, text="(Suppressed alerts are logged but not sent)", foreground="gray").pack(anchor=tk.W, pady=10)
        
        # Printers Tab
        printers_frame = ttk.Frame(notebook, padding=10)
        notebook.add(printers_frame, text="Network Printers")
        
        ttk.Label(printers_frame, text="Format: name - ip - manufacturer (one per line)").pack(anchor=tk.W)
        self.printers_text = scrolledtext.ScrolledText(printers_frame, height=12, width=60)
        self.printers_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        for p in self.config.get('printers', []):
            line = f"{p.get('name', '')} - {p.get('ip', '')} - {p.get('manufacturer', '')}\n"
            self.printers_text.insert(tk.END, line)
        
        # Local Printers Tab
        local_frame = ttk.Frame(notebook, padding=10)
        notebook.add(local_frame, text="Local Printers")
        
        self.local_enabled = tk.BooleanVar(value=self.config.get('local_printers', {}).get('enabled', True))
        ttk.Checkbutton(local_frame, text="Enable local printer monitoring", variable=self.local_enabled).pack(anchor=tk.W, pady=5)
        
        ttk.Label(local_frame, text="Local printers are automatically detected from Windows.", foreground="gray").pack(anchor=tk.W, pady=5)
        
        # Save button
        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Save Settings", command=self._save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Reset to Defaults", command=self._reset).pack(side=tk.LEFT, padx=5)
    
    def _save(self):
        # SNMP
        self.config['snmp']['community'] = self.community.get()
        self.config['snmp']['timeout'] = int(self.timeout.get())
        self.config['snmp']['max_workers'] = int(self.workers.get())
        
        # Scheduler
        self.config['scheduler']['enabled'] = self.sched_enabled.get()
        self.config['scheduler']['interval_minutes'] = int(self.interval.get())
        self.config['scheduler']['debounce_count'] = int(self.debounce.get())
        
        # Email
        self.config['email']['enabled'] = self.email_enabled.get()
        self.config['email']['smtp_server'] = self.smtp_server.get()
        self.config['email']['sender'] = self.email_sender.get()
        self.config['email']['password'] = self.email_password.get()
        self.config['email']['recipients'] = [r.strip() for r in self.email_recipients.get().split(',') if r.strip()]
        
        # Slack
        self.config['slack']['enabled'] = self.slack_enabled.get()
        self.config['slack']['webhook_url'] = self.slack_webhook.get()
        
        # Teams
        self.config['teams']['enabled'] = self.teams_enabled.get()
        self.config['teams']['webhook_url'] = self.teams_webhook.get()
        
        # Suppression
        self.config.setdefault('suppression', {}).setdefault('night_window', {})['enabled'] = self.night_enabled.get()
        self.config.setdefault('suppression', {}).setdefault('weekend', {})['enabled'] = self.weekend_enabled.get()
        
        # Local printers
        self.config.setdefault('local_printers', {})['enabled'] = self.local_enabled.get()
        
        # Parse network printers
        printers = []
        for line in self.printers_text.get(1.0, tk.END).strip().split('\n'):
            parts = [p.strip() for p in line.split('-')]
            if len(parts) >= 2 and parts[1]:
                p = {'name': parts[0], 'ip': parts[1]}
                if len(parts) >= 3 and parts[2]:
                    p['manufacturer'] = parts[2]
                printers.append(p)
        self.config['printers'] = printers
        
        if ConfigManager.save_config(self.config):
            messagebox.showinfo("Saved", "Settings saved. Restart app for full effect.")
            if self.on_save:
                self.on_save()
        else:
            messagebox.showerror("Error", "Save failed")
    
    def _reset(self):
        if messagebox.askyesno("Confirm", "Reset all settings to defaults?"):
            self.config = ConfigManager.get_default_config()
            messagebox.showinfo("Reset", "Settings reset. Click Save to apply.")


# ==============================================================================
# UI COMPONENTS - REPORTS
# ==============================================================================

class ReportsUI:
    """Reports UI"""
    
    def __init__(self, parent, monitor_service: PrinterMonitorService, dashboard: DashboardGenerator):
        self.service = monitor_service
        self.dashboard = dashboard
        self.frame = ttk.Frame(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        ttk.Label(self.frame, text="Reports & History", font=("Arial", 14, "bold")).pack(pady=10)
        
        btns = ttk.Frame(self.frame)
        btns.pack(pady=10)
        ttk.Button(btns, text="View Alerts", command=self._alerts).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="View Operations", command=self._operations).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Open Dashboard", command=self._dashboard).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Export CSV", command=self._export).pack(side=tk.LEFT, padx=5)
        
        self.display = scrolledtext.ScrolledText(self.frame, height=22, width=100)
        self.display.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    
    def _alerts(self):
        self.display.delete(1.0, tk.END)
        alerts = self.service.db.get_alerts(hours=24)
        
        self.display.insert(tk.END, f"ALERTS (24h) - {len(alerts)}\n{'='*60}\n\n")
        
        for a in alerts:
            supp = " [SUPPRESSED]" if a.suppressed else ""
            channels = f" via {', '.join(a.channels_sent)}" if a.channels_sent else ""
            ts = a.timestamp.strftime('%m/%d %H:%M') if hasattr(a.timestamp, 'strftime') else str(a.timestamp)
            severity = a.severity.value if hasattr(a.severity, 'value') else str(a.severity)
            self.display.insert(tk.END, f"[{ts}] {severity.upper()}: {a.alert_type}{supp}\n")
            self.display.insert(tk.END, f"  {a.printer_ip}: {a.message}{channels}\n\n")
    
    def _operations(self):
        self.display.delete(1.0, tk.END)
        ops = self.service.db.get_operations(50)
        
        self.display.insert(tk.END, f"OPERATIONS (last 50)\n{'='*60}\n\n")
        
        for o in ops:
            self.display.insert(tk.END, f"[{o['timestamp']}] {o['operation']} - {o['target']}: {o['status']}\n")
            if o.get('details'):
                self.display.insert(tk.END, f"  {o['details']}\n")
            self.display.insert(tk.END, "\n")
    
    def _dashboard(self):
        data = self.service.get_dashboard_data()
        path = self.dashboard.save(data)
        webbrowser.open(f'file://{os.path.abspath(path)}')
    
    def _export(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        
        try:
            import csv
            data = self.service.db.get_latest_printer_statuses()
            
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['IP', 'Name', 'Status', 'Manufacturer', 'Model', 'Toner', 'Pages', 'Method', 'Timestamp'])
                for r in data:
                    w.writerow([
                        r.get('ip'), r.get('name'), r.get('status'),
                        r.get('manufacturer'), r.get('model'),
                        r.get('toner_level'), r.get('page_count'),
                        r.get('check_method'), r.get('timestamp')
                    ])
            
            messagebox.showinfo("Exported", f"Saved to {path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))


# ==============================================================================
# MAIN APPLICATION
# ==============================================================================

class PrinterManagementApp:
    """Main application v6.1 with Windows Local Printer Agents"""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Printer Management Suite v6.1")
        self.root.geometry("1150x900")
        
        # Initialize components
        self.config = ConfigManager.load_config()
        self.db = DatabaseManager()
        self.alert_service = AlertService(self.config, self.db)
        
        # SNMP Engine for network printers
        self.snmp_engine = None
        if SNMP_ENGINE_AVAILABLE:
            try:
                self.snmp_engine = create_snmp_engine(self.config)
            except Exception as e:
                logger.error(f"SNMP engine init failed: {e}")
        
        # Windows Local Printer Service
        self.local_service = None
        if WINDOWS_AGENTS_AVAILABLE and IS_WINDOWS:
            try:
                self.local_service = LocalPrinterService()
            except Exception as e:
                logger.error(f"Local printer service init failed: {e}")
        
        # Services
        self.monitor_service = PrinterMonitorService(
            self.config, self.db, self.alert_service, self.snmp_engine
        )
        self.deploy_service = PrinterDeploymentService(self.db)
        self.spooler_service = SpoolerService(self.db)
        
        # Scheduler
        self.scheduler = PrinterScheduler(self.monitor_service, self.config)
        
        # Dashboard generator
        self.dashboard = DashboardGenerator()
        
        # Start event bus
        event_bus.start(self.root)
        
        self._log_startup()
        self._create_menu()
        self._create_tabs()
        self._create_status_bar()
        
        # Start scheduler
        self.scheduler.start()
        
        # Cleanup on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _log_startup(self):
        logger.info(f"Platform: {platform.system()}")
        logger.info(f"Python: {sys.version.split()[0]}")
        logger.info(f"Network printers configured: {len(self.config.get('printers', []))}")
        
        logger.info("Modules status:")
        logger.info(f"  OID Registry: {OID_REGISTRY_AVAILABLE}")
        logger.info(f"  SNMP Engine: {SNMP_ENGINE_AVAILABLE}")
        logger.info(f"  Windows Agents: {WINDOWS_AGENTS_AVAILABLE}")
        if WINDOWS_AGENTS_AVAILABLE:
            logger.info(f"    WMI: {WMI_AVAILABLE}")
            logger.info(f"    Win32: {WIN32_AVAILABLE}")
        logger.info(f"  Jinja2: {JINJA2_AVAILABLE}")
        logger.info(f"  APScheduler: {APSCHEDULER_AVAILABLE}")
        logger.info(f"  Requests: {REQUESTS_AVAILABLE}")
    
    def _create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Dashboard", command=self._open_dashboard)
        file_menu.add_command(label="Export Report", command=self._export)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        
        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Run Network Check Now", command=lambda: self.scheduler.run_now())
        tools_menu.add_command(label="Refresh Local Printers", command=self._refresh_local)
        tools_menu.add_separator()
        tools_menu.add_command(label="Test PowerShell", command=self._test_powershell)
        tools_menu.add_command(label="Test Email", command=self._test_email)
        tools_menu.add_separator()
        tools_menu.add_command(label="Clear Database", command=self._clear_db)
        tools_menu.add_command(label="Reload Config", command=self._reload)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._about)
        help_menu.add_command(label="View Log", command=lambda: webbrowser.open("printer_manager.log"))
    
    def _create_tabs(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Network printers (SNMP)
        self.monitor_ui = PrinterMonitorUI(notebook, self.monitor_service)
        notebook.add(self.monitor_ui.frame, text="Network Printers")
        
        # LOCAL PRINTERS (NEW IN v6.1!)
        if self.local_service:
            self.local_ui = LocalPrintersUI(notebook, self.local_service)
            notebook.add(self.local_ui.frame, text="Local Printers")
        else:
            # Placeholder if not available
            placeholder = ttk.Frame(notebook)
            ttk.Label(placeholder, text="Windows Local Printer Agents not available.\n\nInstall required packages:\npip install wmi pywin32", 
                     justify=tk.CENTER).pack(expand=True)
            notebook.add(placeholder, text="Local Printers")
        
        # Deployment
        self.deploy_ui = PrinterDeploymentUI(notebook, self.deploy_service)
        notebook.add(self.deploy_ui.frame, text="Deployment")
        
        # Spooler (legacy)
        self.spooler_ui = SpoolerUI(notebook, self.spooler_service)
        notebook.add(self.spooler_ui.frame, text="Spooler Reset")
        
        # Discovery
        self.discovery_ui = DiscoveryUI(notebook, self.monitor_service, self.config)
        notebook.add(self.discovery_ui.frame, text="Discovery")
        
        # Settings
        self.settings_ui = SettingsUI(notebook, self.config, self._on_settings_saved)
        notebook.add(self.settings_ui.frame, text="Settings")
        
        # Reports
        self.reports_ui = ReportsUI(notebook, self.monitor_service, self.dashboard)
        notebook.add(self.reports_ui.frame, text="Reports")
    
    def _create_status_bar(self):
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        sched = "ON" if self.config.get('scheduler', {}).get('enabled') else "OFF"
        net_printers = len(self.config.get('printers', []))
        
        local_count = 0
        if self.local_service:
            try:
                local_count = len(self.local_service.get_all_printers())
            except:
                pass
        
        status = f"Scheduler: {sched} | Network: {net_printers} | Local: {local_count} | Platform: {platform.system()}"
        
        modules = []
        if not WINDOWS_AGENTS_AVAILABLE:
            modules.append("windows_agents")
        if not SNMP_ENGINE_AVAILABLE:
            modules.append("snmp_engine")
        if not OID_REGISTRY_AVAILABLE:
            modules.append("oid_registry")
        
        if modules:
            status += f" | Missing: {', '.join(modules)}"
        
        self.status_label = ttk.Label(status_frame, text=status, relief=tk.SUNKEN)
        self.status_label.pack(fill=tk.X)
    
    def _on_settings_saved(self):
        """Reload after settings change"""
        self.config = ConfigManager.load_config()
        
        # Recreate SNMP engine
        if self.snmp_engine:
            try:
                self.snmp_engine.shutdown()
            except:
                pass
        
        if SNMP_ENGINE_AVAILABLE:
            try:
                self.snmp_engine = create_snmp_engine(self.config)
            except:
                pass
        
        # Recreate services
        self.alert_service = AlertService(self.config, self.db)
        self.monitor_service = PrinterMonitorService(
            self.config, self.db, self.alert_service, self.snmp_engine
        )
        
        # Restart scheduler
        self.scheduler.stop()
        self.scheduler = PrinterScheduler(self.monitor_service, self.config)
        self.scheduler.start()
        
        # Update status bar
        self._create_status_bar()
        
        logger.info("Settings reloaded")
    
    def _open_dashboard(self):
        data = self.monitor_service.get_dashboard_data()
        path = self.dashboard.save(data)
        webbrowser.open(f'file://{os.path.abspath(path)}')
    
    def _export(self):
        self.reports_ui._export()
    
    def _refresh_local(self):
        if hasattr(self, 'local_ui') and self.local_service:
            self.local_ui._refresh_data()
            messagebox.showinfo("Refreshed", "Local printers refreshed")
        else:
            messagebox.showerror("Error", "Local printer service not available")
    
    def _test_powershell(self):
        ps = get_powershell_exe()
        if os.path.exists(ps):
            try:
                result = run_powershell("$PSVersionTable.PSVersion.ToString()", timeout=5)
                messagebox.showinfo("PowerShell", f"OK!\n\nPath: {ps}\nVersion: {result.stdout.strip()}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        else:
            messagebox.showerror("Error", f"Not found: {ps}")
    
    def _test_email(self):
        test_alert = Alert(
            alert_type="test",
            printer_ip="N/A",
            message="Test email from Printer Management Suite v6.1",
            severity=AlertSeverity.INFO
        )
        success = self.alert_service.dispatcher._send_email(test_alert)
        if success:
            messagebox.showinfo("Success", "Test email sent!")
        else:
            messagebox.showerror("Error", "Email failed. Check configuration.")
    
    def _clear_db(self):
        if messagebox.askyesno("Confirm", "Clear all data? This cannot be undone."):
            self.db.clear_all()
            messagebox.showinfo("Done", "Database cleared")
    
    def _reload(self):
        self._on_settings_saved()
        messagebox.showinfo("Reloaded", "Configuration reloaded")
    
    def _about(self):
        local_status = "Available" if WINDOWS_AGENTS_AVAILABLE else "Not available"
        wmi_status = "✓" if WMI_AVAILABLE else "✗"
        win32_status = "✓" if WIN32_AVAILABLE else "✗"
        
        about_text = f"""
Printer Management Suite v6.1

NEW IN v6.1:
• Windows Local Printer Management
• WMI-based printer queries
• Win32 API integration
• Print job management
• Driver & port management
• Enhanced spooler monitoring

FEATURES:
• Network printers (SNMP) with auto vendor detection
• 15+ vendor OID profiles
• Dashboard with Chart.js
• Multi-channel alerts (Email, Slack, Teams)
• Alert suppression windows
• Network printer discovery

MODULE STATUS:
  Windows Agents: {local_status}
    WMI: {wmi_status}
    Win32: {win32_status}
  SNMP Engine: {'✓' if SNMP_ENGINE_AVAILABLE else '✗'}
  OID Registry: {'✓' if OID_REGISTRY_AVAILABLE else '✗'}
  Jinja2: {'✓' if JINJA2_AVAILABLE else '✗'}
  APScheduler: {'✓' if APSCHEDULER_AVAILABLE else '✗'}
  Requests: {'✓' if REQUESTS_AVAILABLE else '✗'}

SUPPORTED VENDORS:
HP, Canon, Xerox, Konica Minolta, Brother,
Epson, Lexmark, Ricoh, Kyocera, Samsung,
Sharp, OKI, Toshiba, Dell, Fuji Xerox
        """
        
        messagebox.showinfo("About", about_text)
    
    def _on_close(self):
        logger.info("Shutting down...")
        event_bus.stop()
        self.scheduler.stop()
        
        if self.snmp_engine:
            try:
                self.snmp_engine.shutdown()
            except:
                pass
        
        if self.local_service:
            try:
                self.local_service.shutdown()
            except:
                pass
        
        self.root.destroy()


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    """Main entry point"""
    root = tk.Tk()
    
    # Set icon if available
    try:
        root.iconbitmap('printer.ico')
    except:
        pass
    
    # Create and run app
    app = PrinterManagementApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()