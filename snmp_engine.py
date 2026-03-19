"""
Async SNMP Engine Module
Version 2.0 - With OID Registry Integration and Fallback Support
"""

import subprocess
import re
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
import platform
import shutil

# Import OID Registry
from oid_registry import (
    OIDRegistry, VendorDetector, VendorOIDProfiles, 
    Manufacturer, VendorProfile, OIDDefinition, StandardMIB, DetectionResult
)

logger = logging.getLogger("PrinterManager.SNMP")


# ==============================================================================
# DATA MODELS
# ==============================================================================

class PrinterStatus:
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class TonerLevel:
    """Toner level for a single color"""
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
    """Result of a printer check"""
    ip: str
    name: str
    status: str
    timestamp: datetime = field(default_factory=datetime.now)
    device_description: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    serial: Optional[str] = None
    firmware: Optional[str] = None
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
    def low_toner_list(self) -> List[TonerLevel]:
        return [t for t in self.toner_levels if t.is_low]
    
    @property
    def critical_toner_list(self) -> List[TonerLevel]:
        return [t for t in self.toner_levels if t.is_critical]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'ip': self.ip,
            'name': self.name,
            'status': self.status,
            'timestamp': self.timestamp.isoformat(),
            'device_description': self.device_description,
            'model': self.model,
            'manufacturer': self.manufacturer,
            'serial': self.serial,
            'toner_levels': [
                {'name': t.name, 'level': t.level, 'is_low': t.is_low, 'is_critical': t.is_critical}
                for t in self.toner_levels
            ],
            'page_count': self.page_count,
            'error_message': self.error_message,
            'check_method': self.check_method,
            'check_duration_ms': self.check_duration_ms,
            'detection_confidence': self.detection_confidence
        }


# ==============================================================================
# SNMP HELPERS
# ==============================================================================

class SNMPHelper:
    """Helper class for SNMP operations"""
    
    @staticmethod
    def get_snmp_tools() -> Dict[str, Optional[str]]:
        """Check available SNMP tools"""
        tools = {
            'snmpget': shutil.which('snmpget'),
            'snmpwalk': shutil.which('snmpwalk'),
            'snmpbulkget': shutil.which('snmpbulkget'),
            'snmpbulkwalk': shutil.which('snmpbulkwalk'),
        }
        return tools
    
    @staticmethod
    def is_snmp_available() -> bool:
        """Check if SNMP tools are available"""
        tools = SNMPHelper.get_snmp_tools()
        return tools.get('snmpget') is not None or tools.get('snmpwalk') is not None
    
    @staticmethod
    def parse_snmp_string(output: str) -> Optional[str]:
        """Parse SNMP string value from output"""
        if not output or not output.strip():
            return None
        
        output = output.strip()
        
        # Handle "No Such Instance" errors
        if 'No Such Instance' in output or 'No Such Object' in output:
            return None
        
        # Handle timeout
        if 'Timeout' in output or 'No Response' in output:
            return None
        
        # Try common formats
        patterns = [
            (r'STRING:\s*"?([^"]*)"?', 1),
            (r'STRING:\s*(.+)$', 1),
            (r'Hex-STRING:\s*(.+)$', 1),
            (r'=\s*"?([^"]+)"?$', 1),
        ]
        
        for pattern, group in patterns:
            match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(group).strip().strip('"\'')
                if value:
                    # Handle hex encoding
                    if re.match(r'^([0-9A-Fa-f]{2}\s*)+$', value):
                        try:
                            hex_bytes = bytes.fromhex(value.replace(' ', ''))
                            return hex_bytes.decode('utf-8', errors='ignore').strip()
                        except:
                            pass
                    return value
        
        # Last resort - return cleaned output
        if '=' in output:
            return output.split('=')[-1].strip().strip('"\'')
        
        return None
    
    @staticmethod
    def parse_snmp_integer(output: str) -> Optional[int]:
        """Parse SNMP integer value from output"""
        if not output or not output.strip():
            return None
        
        output = output.strip()
        
        # Handle errors
        if 'No Such' in output or 'Timeout' in output:
            return None
        
        # Try common formats
        patterns = [
            r'INTEGER:\s*(-?\d+)',
            r'Counter32:\s*(\d+)',
            r'Counter64:\s*(\d+)',
            r'Gauge32:\s*(\d+)',
            r'Gauge64:\s*(\d+)',
            r'=\s*(-?\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        
        # Try to find any number
        match = re.search(r'(-?\d+)', output)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        
        return None
    
    @staticmethod
    def get_ping_command(ip: str, count: int = 1, timeout: int = 2) -> List[str]:
        """Get platform-appropriate ping command"""
        if platform.system() == "Windows":
            return ['ping', '-n', str(count), '-w', str(timeout * 1000), ip]
        else:
            return ['ping', '-c', str(count), '-W', str(timeout), ip]


# ==============================================================================
# ASYNC SNMP ENGINE
# ==============================================================================

class AsyncSNMPEngine:
    """
    Async SNMP engine with:
    - ThreadPoolExecutor for concurrent queries
    - Auto vendor detection via OID Registry
    - Fallback OID support
    - Configurable timeouts and retries
    """
    
    def __init__(self, config: Dict[str, Any], max_workers: int = 10):
        self.config = config.get('snmp', {})
        self.community = self.config.get('community', 'public')
        self.version = str(self.config.get('version', '2c'))
        self.timeout = self.config.get('timeout', 2)
        self.retries = self.config.get('retries', 1)
        self.max_workers = max_workers
        
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._snmp_available = SNMPHelper.is_snmp_available()
        
        logger.info(f"AsyncSNMPEngine initialized: workers={max_workers}, "
                   f"community={self.community}, version={self.version}, "
                   f"snmp_tools={'available' if self._snmp_available else 'NOT FOUND'}")
    
    def shutdown(self):
        """Shutdown the executor"""
        self._executor.shutdown(wait=False)
        logger.info("AsyncSNMPEngine shutdown")
    
    # ==========================================================================
    # PUBLIC API
    # ==========================================================================
    
    def check_single(self, ip: str, name: str = None, 
                     manufacturer: Manufacturer = None) -> PrinterCheckResult:
        """
        Check a single printer with auto vendor detection.
        
        Args:
            ip: Printer IP address
            name: Printer display name (optional)
            manufacturer: Force specific manufacturer (optional, auto-detect if None)
        
        Returns:
            PrinterCheckResult with all available data
        """
        name = name or ip
        start_time = datetime.now()
        
        logger.info(f"Checking printer: {name} ({ip})")
        
        # Check if SNMP tools available
        if not self._snmp_available:
            logger.warning("SNMP tools not available, falling back to ping")
            return self._fallback_ping(ip, name, start_time, "SNMP tools not installed")
        
        try:
            # Step 1: Get sysDescr for vendor detection
            sys_descr = self._get_oid_value(ip, StandardMIB.SYS_DESCR)
            
            if sys_descr is None:
                logger.warning(f"No SNMP response from {ip}, trying ping")
                return self._fallback_ping(ip, name, start_time, "No SNMP response")
            
            # Step 2: Auto-detect manufacturer
            detection = self._detect_vendor(sys_descr, ip, manufacturer)
            detected_manufacturer = detection.manufacturer
            
            logger.info(f"Vendor: {detected_manufacturer.value} "
                       f"(confidence: {detection.confidence:.0%})")
            
            # Step 3: Get vendor profile
            profile = OIDRegistry.get_profile(detected_manufacturer)
            
            # Step 4: Query all data using profile
            result = self._query_printer_data(ip, name, profile, sys_descr, detection)
            
            # Calculate duration
            result.check_duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            logger.info(f"Check complete: {name} - {result.status} "
                       f"({result.check_duration_ms}ms)")
            
            return result
            
        except Exception as e:
            logger.error(f"Check failed for {ip}: {e}")
            return self._fallback_ping(ip, name, start_time, str(e))
    
    def check_batch(self, printers: List[Dict[str, Any]],
                    progress_callback: Callable[[int, int, PrinterCheckResult], None] = None
                    ) -> List[PrinterCheckResult]:
        """
        Check multiple printers concurrently.
        
        Args:
            printers: List of printer configs [{'ip': '...', 'name': '...', 'manufacturer': '...'}]
            progress_callback: Optional callback(completed, total, result)
        
        Returns:
            List of PrinterCheckResult
        """
        results = []
        total = len(printers)
        
        if total == 0:
            logger.warning("No printers to check")
            return results
        
        logger.info(f"Starting batch check: {total} printers, {self.max_workers} workers")
        
        # Submit all jobs
        futures = {}
        for printer in printers:
            ip = printer.get('ip')
            if not ip:
                continue
            
            name = printer.get('name', ip)
            manufacturer_str = printer.get('manufacturer')
            
            # Parse manufacturer
            manufacturer = None
            if manufacturer_str:
                try:
                    manufacturer = Manufacturer(manufacturer_str)
                except ValueError:
                    logger.debug(f"Unknown manufacturer: {manufacturer_str}")
            
            future = self._executor.submit(self.check_single, ip, name, manufacturer)
            futures[future] = printer
        
        # Collect results
        completed = 0
        for future in as_completed(futures, timeout=60):
            try:
                result = future.result(timeout=30)
                results.append(result)
                completed += 1
                
                if progress_callback:
                    try:
                        progress_callback(completed, total, result)
                    except Exception as e:
                        logger.error(f"Progress callback error: {e}")
                
            except Exception as e:
                printer = futures[future]
                logger.error(f"Batch check failed for {printer.get('ip')}: {e}")
                
                # Add error result
                results.append(PrinterCheckResult(
                    ip=printer.get('ip', 'unknown'),
                    name=printer.get('name', 'unknown'),
                    status=PrinterStatus.ERROR,
                    error_message=str(e),
                    check_method="error"
                ))
                completed += 1
        
        logger.info(f"Batch check complete: {len(results)}/{total} successful")
        return results
    
    # ==========================================================================
    # VENDOR DETECTION
    # ==========================================================================
    
    def _detect_vendor(self, sys_descr: str, ip: str, 
                       forced_manufacturer: Manufacturer = None) -> DetectionResult:
        """Detect vendor with fallback strategies"""
        
        # If manufacturer is forced, use it
        if forced_manufacturer:
            return DetectionResult(
                manufacturer=forced_manufacturer,
                confidence=1.0,
                matched_pattern="forced"
            )
        
        # Try detection from sysDescr
        detection = VendorDetector.detect(sys_descr)
        
        if detection.confidence >= 0.7:
            return detection
        
        # Try sysObjectID for additional hints
        sys_object_id = self._get_oid_value(ip, StandardMIB.SYS_OBJECT_ID)
        if sys_object_id:
            oid_detection = VendorDetector.detect(sys_descr, sys_object_id)
            if oid_detection.confidence > detection.confidence:
                return oid_detection
        
        # Low confidence - return what we have
        if detection.confidence > 0:
            return detection
        
        # No match - return generic
        return DetectionResult(
            manufacturer=Manufacturer.GENERIC,
            confidence=0.0,
            matched_pattern=""
        )
    
    # ==========================================================================
    # DATA QUERIES
    # ==========================================================================
    
    def _query_printer_data(self, ip: str, name: str, profile: VendorProfile,
                            sys_descr: str, detection: DetectionResult) -> PrinterCheckResult:
        """Query all printer data using vendor profile"""
        
        # Query model (with fallbacks)
        model = self._query_with_fallbacks(ip, profile.model_oids)
        
        # Query serial
        serial = self._query_with_fallbacks(ip, profile.serial_oids)
        
        # Query page count
        page_count = self._query_integer_with_fallbacks(ip, profile.page_count_oids)
        
        # Query toner levels (concurrent)
        toner_levels = self._query_toner_levels(ip, profile)
        
        # Query extra data
        firmware = None
        if 'firmware' in profile.extra_oids:
            firmware = self._get_oid_value(ip, profile.extra_oids['firmware'].oid)
        
        return PrinterCheckResult(
            ip=ip,
            name=name,
            status=PrinterStatus.ONLINE,
            device_description=sys_descr,
            model=model or detection.model_hint,
            manufacturer=profile.manufacturer.value,
            serial=serial,
            firmware=firmware,
            toner_levels=toner_levels,
            page_count=page_count,
            check_method=f"snmp_{profile.manufacturer.value.lower().replace(' ', '_')}",
            detection_confidence=detection.confidence,
            raw_data={
                'sys_descr': sys_descr,
                'detection_pattern': detection.matched_pattern
            }
        )
    
    def _query_with_fallbacks(self, ip: str, oid_defs: List[OIDDefinition]) -> Optional[str]:
        """Query OID with fallbacks until success"""
        for oid_def in oid_defs:
            # Try primary OID
            value = self._get_oid_value(ip, oid_def.oid)
            if value:
                return value
            
            # Try fallbacks
            for fallback_oid in oid_def.fallbacks:
                value = self._get_oid_value(ip, fallback_oid)
                if value:
                    logger.debug(f"Using fallback OID for {oid_def.name}")
                    return value
        
        return None
    
    def _query_integer_with_fallbacks(self, ip: str, oid_defs: List[OIDDefinition]) -> Optional[int]:
        """Query integer OID with fallbacks"""
        for oid_def in oid_defs:
            value = self._get_oid_integer(ip, oid_def.oid)
            if value is not None:
                return value
            
            for fallback_oid in oid_def.fallbacks:
                value = self._get_oid_integer(ip, fallback_oid)
                if value is not None:
                    return value
        
        return None
    
    def _query_toner_levels(self, ip: str, profile: VendorProfile) -> List[TonerLevel]:
        """Query toner levels concurrently"""
        toner_levels = []
        
        # Submit concurrent queries for toner values
        toner_futures = {}
        max_futures = {}
        
        for color, oid_def in profile.toner_oids.items():
            future = self._executor.submit(self._get_oid_integer, ip, oid_def.oid)
            toner_futures[future] = (color, oid_def)
        
        for color, oid_def in profile.toner_max_oids.items():
            future = self._executor.submit(self._get_oid_integer, ip, oid_def.oid)
            max_futures[future] = color
        
        # Collect toner values
        toner_values = {}
        for future in as_completed(toner_futures, timeout=10):
            color, oid_def = toner_futures[future]
            try:
                value = future.result(timeout=5)
                if value is not None:
                    toner_values[color] = value
                elif oid_def.fallbacks:
                    # Try fallbacks
                    for fallback_oid in oid_def.fallbacks:
                        value = self._get_oid_integer(ip, fallback_oid)
                        if value is not None:
                            toner_values[color] = value
                            break
            except Exception as e:
                logger.debug(f"Toner query failed for {color}: {e}")
        
        # Collect max values
        max_values = {}
        for future in as_completed(max_futures, timeout=10):
            color = max_futures[future]
            try:
                value = future.result(timeout=5)
                if value is not None and value > 0:
                    max_values[color] = value
            except:
                pass
        
        # Calculate percentages
        special_values = profile.toner_value_special or {-3: 100, -2: 0, -1: 50}
        
        for color, value in toner_values.items():
            max_val = max_values.get(color, 100)
            
            # Handle special values
            if value in special_values:
                level = special_values[value]
            elif value < 0:
                # Unknown negative value
                level = 50
            elif max_val > 0 and max_val != 100:
                # Calculate percentage
                level = int((value / max_val) * 100)
            else:
                # Assume it's already a percentage
                level = value
            
            # Clamp to 0-100
            level = max(0, min(100, level))
            
            toner_levels.append(TonerLevel(
                name=color,
                level=level,
                max_capacity=max_val
            ))
        
        # Sort by standard color order
        color_order = ['black', 'cyan', 'magenta', 'yellow']
        toner_levels.sort(key=lambda t: color_order.index(t.name) if t.name in color_order else 99)
        
        return toner_levels
    
    # ==========================================================================
    # LOW-LEVEL SNMP
    # ==========================================================================
    
    def _get_oid_value(self, ip: str, oid: str) -> Optional[str]:
        """Get single OID value as string"""
        if not oid:
            return None
        
        try:
            cmd = [
                'snmpget',
                f'-v{self.version}',
                '-c', self.community,
                '-t', str(self.timeout),
                '-r', str(self.retries),
                ip,
                oid
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 2
            )
            
            if result.returncode == 0 and result.stdout:
                return SNMPHelper.parse_snmp_string(result.stdout)
            
        except subprocess.TimeoutExpired:
            logger.debug(f"Timeout getting OID {oid} from {ip}")
        except Exception as e:
            logger.debug(f"Error getting OID {oid} from {ip}: {e}")
        
        return None
    
    def _get_oid_integer(self, ip: str, oid: str) -> Optional[int]:
        """Get single OID value as integer"""
        if not oid:
            return None
        
        try:
            cmd = [
                'snmpget',
                f'-v{self.version}',
                '-c', self.community,
                '-t', str(self.timeout),
                '-r', str(self.retries),
                ip,
                oid
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 2
            )
            
            if result.returncode == 0 and result.stdout:
                return SNMPHelper.parse_snmp_integer(result.stdout)
            
        except subprocess.TimeoutExpired:
            logger.debug(f"Timeout getting OID {oid} from {ip}")
        except Exception as e:
            logger.debug(f"Error getting OID {oid} from {ip}: {e}")
        
        return None
    
    def _walk_oid(self, ip: str, oid: str) -> List[Tuple[str, str]]:
        """Walk an OID tree and return list of (oid, value) tuples"""
        results = []
        
        if not oid:
            return results
        
        try:
            cmd = [
                'snmpwalk',
                f'-v{self.version}',
                '-c', self.community,
                '-t', str(self.timeout),
                '-r', str(self.retries),
                ip,
                oid
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=(self.timeout + 2) * 3  # Longer timeout for walk
            )
            
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.strip().split('\n'):
                    if '=' in line:
                        parts = line.split('=', 1)
                        if len(parts) == 2:
                            oid_part = parts[0].strip()
                            value_part = parts[1].strip()
                            
                            # Parse based on type
                            if 'STRING' in value_part:
                                value = SNMPHelper.parse_snmp_string(value_part)
                            elif 'INTEGER' in value_part or 'Counter' in value_part or 'Gauge' in value_part:
                                value = str(SNMPHelper.parse_snmp_integer(value_part))
                            else:
                                value = value_part
                            
                            if value:
                                results.append((oid_part, value))
            
        except Exception as e:
            logger.debug(f"Walk failed for OID {oid}: {e}")
        
        return results
    
    # ==========================================================================
    # FALLBACK
    # ==========================================================================
    
    def _fallback_ping(self, ip: str, name: str, start_time: datetime,
                       error_msg: str = None) -> PrinterCheckResult:
        """Fallback to ping when SNMP fails"""
        try:
            cmd = SNMPHelper.get_ping_command(ip, count=1, timeout=2)
            result = subprocess.run(cmd, capture_output=True, timeout=5)
            status = PrinterStatus.ONLINE if result.returncode == 0 else PrinterStatus.OFFLINE
        except:
            status = PrinterStatus.OFFLINE
        
        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        
        # Use a more informative check_method when SNMP tools are missing vs. simply unavailable
        if not self._snmp_available:
            method = "ping_fallback (SNMP unavailable)"
        else:
            method = "ping_fallback"

        return PrinterCheckResult(
            ip=ip,
            name=name,
            status=status,
            check_method=method,
            check_duration_ms=duration_ms,
            error_message=error_msg or ("SNMP unavailable" if status == PrinterStatus.ONLINE else None)
        )


# ==============================================================================
# SNMP DISCOVERY (Bonus Feature)
# ==============================================================================

class SNMPDiscovery:
    """
    Discover printers on the network using SNMP broadcast.
    """
    
    def __init__(self, engine: AsyncSNMPEngine):
        self.engine = engine
    
    def scan_subnet(self, subnet: str, progress_callback: Callable = None) -> List[Dict[str, Any]]:
        """
        Scan a subnet for SNMP-enabled printers.
        
        Args:
            subnet: Subnet in format "192.168.1" (will scan .1-.254)
            progress_callback: Optional callback(current, total, ip)
        
        Returns:
            List of discovered printers with basic info
        """
        discovered = []
        ips = [f"{subnet}.{i}" for i in range(1, 255)]
        total = len(ips)
        
        logger.info(f"Starting subnet scan: {subnet}.0/24")
        
        def check_ip(ip: str) -> Optional[Dict]:
            """Check if IP responds to SNMP printer OIDs"""
            try:
                # Quick check with short timeout
                sys_descr = self.engine._get_oid_value(ip, StandardMIB.SYS_DESCR)
                
                if sys_descr:
                    # Check if it's a printer
                    detection = VendorDetector.detect(sys_descr)
                    
                    # Also check for printer MIB
                    page_count = self.engine._get_oid_integer(ip, StandardMIB.PRT_MARKER_LIFE_COUNT)
                    
                    if detection.manufacturer != Manufacturer.GENERIC or page_count is not None:
                        return {
                            'ip': ip,
                            'name': f"Printer_{ip.split('.')[-1]}",
                            'manufacturer': detection.manufacturer.value,
                            'sys_descr': sys_descr[:100],
                            'model_hint': detection.model_hint,
                            'has_printer_mib': page_count is not None
                        }
            except:
                pass
            return None
        
        # Use executor for concurrent scanning
        futures = {self.engine._executor.submit(check_ip, ip): ip for ip in ips}
        
        completed = 0
        for future in as_completed(futures, timeout=300):
            completed += 1
            ip = futures[future]
            
            if progress_callback:
                try:
                    progress_callback(completed, total, ip)
                except:
                    pass
            
            try:
                result = future.result(timeout=5)
                if result:
                    discovered.append(result)
                    logger.info(f"Discovered: {result['ip']} - {result['manufacturer']}")
            except:
                pass
        
        logger.info(f"Scan complete: {len(discovered)} printers found")
        return discovered


# ==============================================================================
# FACTORY
# ==============================================================================

def create_snmp_engine(config: Dict[str, Any]) -> AsyncSNMPEngine:
    """Factory function to create SNMP engine"""
    max_workers = config.get('snmp', {}).get('max_workers', 10)
    return AsyncSNMPEngine(config, max_workers)