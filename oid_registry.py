"""
OID Registry Module - Vendor OID Profiles and Auto-Detection

Provides vendor-specific SNMP OID profiles, manufacturer detection,
and standard MIB constants for printer monitoring.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger("PrinterManager.OIDRegistry")


# ==============================================================================
# ENUMS
# ==============================================================================

class Manufacturer(Enum):
    """Supported printer manufacturers"""
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
# STANDARD MIB CONSTANTS
# ==============================================================================

class StandardMIB:
    """Standard SNMP MIB OID constants for printers"""

    # RFC 1213 - MIB-II System group
    SYS_DESCR = "1.3.6.1.2.1.1.1.0"
    SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
    SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
    SYS_NAME = "1.3.6.1.2.1.1.5.0"

    # RFC 2790 - Host Resources MIB
    HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3.1"
    HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1.1"
    HR_PRINTER_DETECTED_ERROR_STATE = "1.3.6.1.2.1.25.3.5.1.2.1"
    HR_STORAGE_DESCR = "1.3.6.1.2.1.25.2.3.1.3.1"
    HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5.1"
    HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6.1"

    # RFC 3805 - Printer MIB v2
    PRT_GENERAL_SERIAL_NUMBER = "1.3.6.1.2.1.43.5.1.1.17.1"
    PRT_GENERAL_CURRENT_OPERATOR = "1.3.6.1.2.1.43.5.1.1.2.1"
    PRT_MARKER_LIFE_COUNT = "1.3.6.1.2.1.43.10.2.1.4.1.1"
    PRT_MARKER_POWER_ON_COUNT = "1.3.6.1.2.1.43.10.2.1.5.1.1"
    PRT_MARKER_COLORANT_VALUE = "1.3.6.1.2.1.43.12.1.1.4.1"
    PRT_MARKER_SUPPLIES_DESCRIPTION = "1.3.6.1.2.1.43.11.1.1.6.1"
    PRT_MARKER_SUPPLIES_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1"
    PRT_MARKER_SUPPLIES_MAX_CAPACITY = "1.3.6.1.2.1.43.11.1.1.8.1"

    # Toner supply indices (standard positions)
    PRT_SUPPLY_BLACK_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1.1"
    PRT_SUPPLY_BLACK_MAX = "1.3.6.1.2.1.43.11.1.1.8.1.1"
    PRT_SUPPLY_CYAN_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1.2"
    PRT_SUPPLY_CYAN_MAX = "1.3.6.1.2.1.43.11.1.1.8.1.2"
    PRT_SUPPLY_MAGENTA_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1.3"
    PRT_SUPPLY_MAGENTA_MAX = "1.3.6.1.2.1.43.11.1.1.8.1.3"
    PRT_SUPPLY_YELLOW_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1.4"
    PRT_SUPPLY_YELLOW_MAX = "1.3.6.1.2.1.43.11.1.1.8.1.4"


# ==============================================================================
# DATA CLASSES
# ==============================================================================

@dataclass
class OIDDefinition:
    """Definition of a single OID with optional fallbacks"""
    name: str
    oid: str
    fallbacks: List[str] = field(default_factory=list)


@dataclass
class VendorProfile:
    """Vendor-specific OID profile for a printer manufacturer"""
    manufacturer: Manufacturer
    model_oids: List[OIDDefinition] = field(default_factory=list)
    serial_oids: List[OIDDefinition] = field(default_factory=list)
    page_count_oids: List[OIDDefinition] = field(default_factory=list)
    toner_oids: Dict[str, OIDDefinition] = field(default_factory=dict)
    toner_max_oids: Dict[str, OIDDefinition] = field(default_factory=dict)
    toner_value_special: Optional[Dict[int, int]] = None
    extra_oids: Dict[str, OIDDefinition] = field(default_factory=dict)


@dataclass
class DetectionResult:
    """Result of vendor detection"""
    manufacturer: Manufacturer
    confidence: float
    matched_pattern: str
    model_hint: Optional[str] = None


# ==============================================================================
# VENDOR DETECTOR
# ==============================================================================

class VendorDetector:
    """Detects printer manufacturer from sysDescr and sysObjectID"""

    # Enterprise OID prefixes
    _ENTERPRISE_OIDS = {
        "1.3.6.1.4.1.11.": Manufacturer.HP,
        "1.3.6.1.4.1.1602.": Manufacturer.CANON,
        "1.3.6.1.4.1.253.": Manufacturer.XEROX,
        "1.3.6.1.4.1.18334.": Manufacturer.KONICA_MINOLTA,
        "1.3.6.1.4.1.2435.": Manufacturer.BROTHER,
        "1.3.6.1.4.1.1248.": Manufacturer.EPSON,
        "1.3.6.1.4.1.641.": Manufacturer.LEXMARK,
        "1.3.6.1.4.1.367.": Manufacturer.RICOH,
        "1.3.6.1.4.1.1347.": Manufacturer.KYOCERA,
        "1.3.6.1.4.1.236.": Manufacturer.SAMSUNG,
    }

    # sysDescr pattern matching: (regex_pattern, manufacturer, confidence, model_group_index_or_None)
    _SYSDESCR_PATTERNS = [
        # HP
        (r"(?i)hp\s+laserjet\s+([\w\d\s\-]+?)(?:\s+series)?\s*$", Manufacturer.HP, 0.95, 1),
        (r"(?i)hp\s+officejet\s+([\w\d\s\-]+?)(?:\s+series)?\s*$", Manufacturer.HP, 0.95, 1),
        (r"(?i)hp\s+colorjet\s+([\w\d\s\-]+?)(?:\s+series)?\s*$", Manufacturer.HP, 0.95, 1),
        (r"(?i)hewlett.?packard", Manufacturer.HP, 0.90, None),
        (r"(?i)\bhp\b.*(laser|print|jet)", Manufacturer.HP, 0.85, None),
        # Canon
        (r"(?i)canon\s+(i-?sensys|imagerunner|lbp|mf)\s+([\w\d\s\-]+?)(?:\s+series)?\s*$",
         Manufacturer.CANON, 0.95, 2),
        (r"(?i)canon", Manufacturer.CANON, 0.90, None),
        # Xerox
        (r"(?i)xerox\s+(workcentre|phaser|versalink|altalink)\s+([\w\d\-]+)",
         Manufacturer.XEROX, 0.95, 2),
        (r"(?i)xerox", Manufacturer.XEROX, 0.90, None),
        # Konica Minolta
        (r"(?i)konica\s*minolta\s+(bizhub|accurio|magicolor)\s*([\w\d\s\-]+?)\s*$",
         Manufacturer.KONICA_MINOLTA, 0.95, 2),
        (r"(?i)konica.?minolta", Manufacturer.KONICA_MINOLTA, 0.90, None),
        (r"(?i)bizhub", Manufacturer.KONICA_MINOLTA, 0.80, None),
        # Brother
        (r"(?i)brother\s+(hl|mfc|dcp|pt)\s*[-]?([\w\d\s\-]+?)\s*$", Manufacturer.BROTHER, 0.95, 2),
        (r"(?i)brother", Manufacturer.BROTHER, 0.90, None),
        # Epson
        (r"(?i)epson\s+(workforce|expression|ecotank|stylus)\s+([\w\d\s\-]+?)\s*$",
         Manufacturer.EPSON, 0.95, 2),
        (r"(?i)seiko\s+epson", Manufacturer.EPSON, 0.95, None),
        (r"(?i)epson", Manufacturer.EPSON, 0.90, None),
        # Lexmark
        (r"(?i)lexmark\s+([\w\d\s\-]+?)\s*$", Manufacturer.LEXMARK, 0.95, 1),
        (r"(?i)lexmark", Manufacturer.LEXMARK, 0.90, None),
        # Ricoh
        (r"(?i)ricoh\s+(aficio|mp|sp|im)\s*([\w\d\s\-]+?)\s*$", Manufacturer.RICOH, 0.95, 2),
        (r"(?i)ricoh", Manufacturer.RICOH, 0.90, None),
        (r"(?i)aficio", Manufacturer.RICOH, 0.80, None),
        # Kyocera
        (r"(?i)kyocera\s+(ecosys|taskalfa|fs)\s*([\w\d\s\-]+?)\s*$", Manufacturer.KYOCERA, 0.95, 2),
        (r"(?i)kyocera", Manufacturer.KYOCERA, 0.90, None),
        (r"(?i)taskalfa", Manufacturer.KYOCERA, 0.80, None),
        # Samsung
        (r"(?i)samsung\s+(m|sl|clx|scx|ml)\s*[-]?([\w\d\s\-]+?)\s*$", Manufacturer.SAMSUNG, 0.95, 2),
        (r"(?i)samsung", Manufacturer.SAMSUNG, 0.90, None),
    ]

    @staticmethod
    def detect(sys_descr: str, sys_object_id: str = None) -> DetectionResult:
        """
        Detect printer manufacturer from sysDescr and optionally sysObjectID.

        Args:
            sys_descr: Value of SNMP sysDescr OID (1.3.6.1.2.1.1.1.0)
            sys_object_id: Value of SNMP sysObjectID OID (1.3.6.1.2.1.1.2.0), optional

        Returns:
            DetectionResult with manufacturer, confidence, matched_pattern, and model_hint
        """
        best_manufacturer = Manufacturer.GENERIC
        best_confidence = 0.0
        best_pattern = ""
        model_hint: Optional[str] = None

        # 1. Try sysObjectID enterprise OID prefix matching (highest confidence)
        if sys_object_id:
            for prefix, mfr in VendorDetector._ENTERPRISE_OIDS.items():
                if sys_object_id.startswith(prefix):
                    oid_confidence = 0.92
                    if oid_confidence > best_confidence:
                        best_manufacturer = mfr
                        best_confidence = oid_confidence
                        best_pattern = f"oid_prefix:{prefix}"
                    break

        # 2. Try sysDescr pattern matching
        if sys_descr:
            for pattern, mfr, confidence, model_group in VendorDetector._SYSDESCR_PATTERNS:
                m = re.search(pattern, sys_descr)
                if m:
                    if confidence > best_confidence:
                        best_manufacturer = mfr
                        best_confidence = confidence
                        best_pattern = pattern
                        if model_group is not None:
                            try:
                                model_hint = m.group(model_group).strip()
                            except (IndexError, AttributeError):
                                pass
                    break  # Use highest-priority match per manufacturer

        logger.debug(
            f"Vendor detection: {best_manufacturer.value} "
            f"(confidence={best_confidence:.2f}, pattern='{best_pattern}')"
        )

        return DetectionResult(
            manufacturer=best_manufacturer,
            confidence=best_confidence,
            matched_pattern=best_pattern,
            model_hint=model_hint,
        )


# ==============================================================================
# VENDOR OID PROFILES
# ==============================================================================

class VendorOIDProfiles:
    """Pre-defined vendor-specific OID profiles for all supported manufacturers"""

    @staticmethod
    def hp() -> VendorProfile:
        """HP printer OID profile (Enterprise prefix: 1.3.6.1.4.1.11)"""
        return VendorProfile(
            manufacturer=Manufacturer.HP,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.11.2.3.9.1.1.7.0",  # HP model name
                    fallbacks=[
                        "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.3.0",
                        StandardMIB.HR_DEVICE_DESCR,
                        StandardMIB.SYS_DESCR,
                    ],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.6.0",  # HP serial
                    fallbacks=[
                        "1.3.6.1.4.1.11.2.3.9.1.1.11.0",
                        StandardMIB.PRT_GENERAL_SERIAL_NUMBER,
                    ],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.28.0",  # HP total page count
                    fallbacks=[
                        "1.3.6.1.4.1.11.2.3.9.1.1.27.0",
                        StandardMIB.PRT_MARKER_LIFE_COUNT,
                    ],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.2.1.124.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.2.1.124.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.2.1.124.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.2.1.124.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.11.2.3.9.4.2.1.1.3.5.0",
                    fallbacks=["1.3.6.1.4.1.11.2.3.9.1.1.3.0"],
                ),
            },
        )

    @staticmethod
    def canon() -> VendorProfile:
        """Canon printer OID profile (Enterprise prefix: 1.3.6.1.4.1.1602)"""
        return VendorProfile(
            manufacturer=Manufacturer.CANON,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.1602.1.1.1.1.0",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.1602.1.3.2.1.4.1",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.1602.1.11.1.3.1.4.301",
                    fallbacks=[
                        "1.3.6.1.4.1.1602.1.1.4.1.0",
                        StandardMIB.PRT_MARKER_LIFE_COUNT,
                    ],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.1602.1.3.4.1.3.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.1602.1.3.4.1.3.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.1602.1.3.4.1.3.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.1602.1.3.4.1.3.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.1602.1.1.1.3.0",
                ),
            },
        )

    @staticmethod
    def xerox() -> VendorProfile:
        """Xerox printer OID profile (Enterprise prefix: 1.3.6.1.4.1.253)"""
        return VendorProfile(
            manufacturer=Manufacturer.XEROX,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.253.8.53.3.2.1.3.1",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.253.8.53.3.2.1.4.1",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.253.8.53.13.2.1.6.1.20.1",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.253.8.53.13.2.1.6.1.24.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.253.8.53.13.2.1.6.1.24.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.253.8.53.13.2.1.6.1.24.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.253.8.53.13.2.1.6.1.24.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.253.8.53.3.2.1.5.1",
                ),
            },
        )

    @staticmethod
    def konica_minolta() -> VendorProfile:
        """Konica Minolta printer OID profile (Enterprise prefix: 1.3.6.1.4.1.18334)"""
        return VendorProfile(
            manufacturer=Manufacturer.KONICA_MINOLTA,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.18334.1.1.1.1.1.1.1.3.0",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.18334.1.1.1.1.1.1.1.4.0",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.18334.1.1.1.2.1.2.1.3.0",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.5.1.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.5.1.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.5.1.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.5.1.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition(
                    "black_max",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.4.1.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_MAX],
                ),
                "cyan": OIDDefinition(
                    "cyan_max",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.4.1.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_MAX],
                ),
                "magenta": OIDDefinition(
                    "magenta_max",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.4.1.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_MAX],
                ),
                "yellow": OIDDefinition(
                    "yellow_max",
                    "1.3.6.1.4.1.18334.1.1.1.2.2.2.1.4.1.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_MAX],
                ),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.18334.1.1.1.1.1.1.1.5.0",
                ),
            },
        )

    @staticmethod
    def brother() -> VendorProfile:
        """Brother printer OID profile (Enterprise prefix: 1.3.6.1.4.1.2435)"""
        return VendorProfile(
            manufacturer=Manufacturer.BROTHER,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.1.0",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.8.0",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.4.33.0",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.4.9.0",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.4.10.0",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.4.11.0",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.4.12.0",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.2435.2.3.9.4.2.1.5.5.17.0",
                ),
            },
        )

    @staticmethod
    def epson() -> VendorProfile:
        """Epson printer OID profile (Enterprise prefix: 1.3.6.1.4.1.1248)"""
        return VendorProfile(
            manufacturer=Manufacturer.EPSON,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.1248.1.1.3.1.3.8.0",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.1248.1.1.3.1.4.1.1.12.1",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.1248.1.1.3.1.11.1.4.1.1",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.1248.1.1.3.1.11.1.6.1.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.1248.1.1.3.1.11.1.6.1.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.1248.1.1.3.1.11.1.6.1.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.1248.1.1.3.1.11.1.6.1.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.1248.1.1.3.1.3.5.0",
                ),
            },
        )

    @staticmethod
    def lexmark() -> VendorProfile:
        """Lexmark printer OID profile (Enterprise prefix: 1.3.6.1.4.1.641)"""
        return VendorProfile(
            manufacturer=Manufacturer.LEXMARK,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.641.2.1.2.1.2.1",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.641.2.1.2.1.4.1",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.641.2.1.5.1.7.1",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.641.2.1.5.1.5.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.641.2.1.5.1.5.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.641.2.1.5.1.5.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.641.2.1.5.1.5.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition(
                    "black_max",
                    "1.3.6.1.4.1.641.2.1.5.1.6.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_MAX],
                ),
                "cyan": OIDDefinition(
                    "cyan_max",
                    "1.3.6.1.4.1.641.2.1.5.1.6.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_MAX],
                ),
                "magenta": OIDDefinition(
                    "magenta_max",
                    "1.3.6.1.4.1.641.2.1.5.1.6.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_MAX],
                ),
                "yellow": OIDDefinition(
                    "yellow_max",
                    "1.3.6.1.4.1.641.2.1.5.1.6.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_MAX],
                ),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.641.2.1.2.1.5.1",
                ),
            },
        )

    @staticmethod
    def ricoh() -> VendorProfile:
        """Ricoh printer OID profile (Enterprise prefix: 1.3.6.1.4.1.367)"""
        return VendorProfile(
            manufacturer=Manufacturer.RICOH,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.367.3.2.1.2.1.4.0",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.367.3.2.1.2.1.7.0",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.367.3.2.1.2.19.5.1.5.1",
                    fallbacks=[
                        "1.3.6.1.4.1.367.3.2.1.2.19.5.1.5.2",
                        StandardMIB.PRT_MARKER_LIFE_COUNT,
                    ],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.5.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.5.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.5.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.5.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition(
                    "black_max",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.6.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_MAX],
                ),
                "cyan": OIDDefinition(
                    "cyan_max",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.6.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_MAX],
                ),
                "magenta": OIDDefinition(
                    "magenta_max",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.6.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_MAX],
                ),
                "yellow": OIDDefinition(
                    "yellow_max",
                    "1.3.6.1.4.1.367.3.2.1.2.24.1.1.6.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_MAX],
                ),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.367.3.2.1.2.1.8.0",
                ),
            },
        )

    @staticmethod
    def kyocera() -> VendorProfile:
        """Kyocera printer OID profile (Enterprise prefix: 1.3.6.1.4.1.1347)"""
        return VendorProfile(
            manufacturer=Manufacturer.KYOCERA,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.1347.43.5.1.1.28.1",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.1347.43.5.1.1.8.1",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.1347.43.10.1.1.12.1.1",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.1347.43.11.1.1.9.1.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.1347.43.11.1.1.9.1.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.1347.43.11.1.1.9.1.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.1347.43.11.1.1.9.1.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition(
                    "black_max",
                    "1.3.6.1.4.1.1347.43.11.1.1.8.1.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_MAX],
                ),
                "cyan": OIDDefinition(
                    "cyan_max",
                    "1.3.6.1.4.1.1347.43.11.1.1.8.1.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_MAX],
                ),
                "magenta": OIDDefinition(
                    "magenta_max",
                    "1.3.6.1.4.1.1347.43.11.1.1.8.1.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_MAX],
                ),
                "yellow": OIDDefinition(
                    "yellow_max",
                    "1.3.6.1.4.1.1347.43.11.1.1.8.1.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_MAX],
                ),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.1347.43.5.1.1.27.1",
                ),
            },
        )

    @staticmethod
    def samsung() -> VendorProfile:
        """Samsung printer OID profile (Enterprise prefix: 1.3.6.1.4.1.236)"""
        return VendorProfile(
            manufacturer=Manufacturer.SAMSUNG,
            model_oids=[
                OIDDefinition(
                    "model",
                    "1.3.6.1.4.1.236.11.5.1.1.1.1.4.0",
                    fallbacks=[StandardMIB.HR_DEVICE_DESCR, StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    "1.3.6.1.4.1.236.11.5.1.1.1.1.7.0",
                    fallbacks=[StandardMIB.PRT_GENERAL_SERIAL_NUMBER],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    "1.3.6.1.4.1.236.11.5.1.1.4.1.1.2.1",
                    fallbacks=[StandardMIB.PRT_MARKER_LIFE_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    "1.3.6.1.4.1.236.11.5.1.1.3.1.1.4.1",
                    fallbacks=[StandardMIB.PRT_SUPPLY_BLACK_LEVEL],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    "1.3.6.1.4.1.236.11.5.1.1.3.1.1.4.2",
                    fallbacks=[StandardMIB.PRT_SUPPLY_CYAN_LEVEL],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    "1.3.6.1.4.1.236.11.5.1.1.3.1.1.4.3",
                    fallbacks=[StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    "1.3.6.1.4.1.236.11.5.1.1.3.1.1.4.4",
                    fallbacks=[StandardMIB.PRT_SUPPLY_YELLOW_LEVEL],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={
                "firmware": OIDDefinition(
                    "firmware",
                    "1.3.6.1.4.1.236.11.5.1.1.1.1.5.0",
                ),
            },
        )

    @staticmethod
    def generic() -> VendorProfile:
        """Generic/fallback printer OID profile using standard MIBs"""
        return VendorProfile(
            manufacturer=Manufacturer.GENERIC,
            model_oids=[
                OIDDefinition(
                    "model",
                    StandardMIB.HR_DEVICE_DESCR,
                    fallbacks=[StandardMIB.SYS_DESCR],
                ),
            ],
            serial_oids=[
                OIDDefinition(
                    "serial",
                    StandardMIB.PRT_GENERAL_SERIAL_NUMBER,
                    fallbacks=[],
                ),
            ],
            page_count_oids=[
                OIDDefinition(
                    "page_count",
                    StandardMIB.PRT_MARKER_LIFE_COUNT,
                    fallbacks=[StandardMIB.PRT_MARKER_POWER_ON_COUNT],
                ),
            ],
            toner_oids={
                "black": OIDDefinition(
                    "black_toner",
                    StandardMIB.PRT_SUPPLY_BLACK_LEVEL,
                    fallbacks=[],
                ),
                "cyan": OIDDefinition(
                    "cyan_toner",
                    StandardMIB.PRT_SUPPLY_CYAN_LEVEL,
                    fallbacks=[],
                ),
                "magenta": OIDDefinition(
                    "magenta_toner",
                    StandardMIB.PRT_SUPPLY_MAGENTA_LEVEL,
                    fallbacks=[],
                ),
                "yellow": OIDDefinition(
                    "yellow_toner",
                    StandardMIB.PRT_SUPPLY_YELLOW_LEVEL,
                    fallbacks=[],
                ),
            },
            toner_max_oids={
                "black": OIDDefinition("black_max", StandardMIB.PRT_SUPPLY_BLACK_MAX),
                "cyan": OIDDefinition("cyan_max", StandardMIB.PRT_SUPPLY_CYAN_MAX),
                "magenta": OIDDefinition("magenta_max", StandardMIB.PRT_SUPPLY_MAGENTA_MAX),
                "yellow": OIDDefinition("yellow_max", StandardMIB.PRT_SUPPLY_YELLOW_MAX),
            },
            toner_value_special={-3: 100, -2: 0, -1: 50},
            extra_oids={},
        )


# ==============================================================================
# OID REGISTRY
# ==============================================================================

class OIDRegistry:
    """Registry that maps Manufacturer enum to VendorProfile instances"""

    _profiles: Dict[Manufacturer, VendorProfile] = {}
    _initialized: bool = False

    @classmethod
    def _initialize(cls) -> None:
        """Lazily build the profile cache"""
        if cls._initialized:
            return
        cls._profiles = {
            Manufacturer.HP: VendorOIDProfiles.hp(),
            Manufacturer.CANON: VendorOIDProfiles.canon(),
            Manufacturer.XEROX: VendorOIDProfiles.xerox(),
            Manufacturer.KONICA_MINOLTA: VendorOIDProfiles.konica_minolta(),
            Manufacturer.BROTHER: VendorOIDProfiles.brother(),
            Manufacturer.EPSON: VendorOIDProfiles.epson(),
            Manufacturer.LEXMARK: VendorOIDProfiles.lexmark(),
            Manufacturer.RICOH: VendorOIDProfiles.ricoh(),
            Manufacturer.KYOCERA: VendorOIDProfiles.kyocera(),
            Manufacturer.SAMSUNG: VendorOIDProfiles.samsung(),
            Manufacturer.GENERIC: VendorOIDProfiles.generic(),
        }
        cls._initialized = True
        logger.debug("OIDRegistry initialized with %d vendor profiles", len(cls._profiles))

    @classmethod
    def get_profile(cls, manufacturer: Manufacturer) -> VendorProfile:
        """
        Return the VendorProfile for the given manufacturer.

        Falls back to the Generic profile if the manufacturer is not found.

        Args:
            manufacturer: Manufacturer enum value

        Returns:
            VendorProfile for the manufacturer
        """
        cls._initialize()
        profile = cls._profiles.get(manufacturer)
        if profile is None:
            logger.warning(
                "No OID profile for %s, using Generic", manufacturer.value
            )
            profile = cls._profiles[Manufacturer.GENERIC]
        return profile

    @classmethod
    def get_all_profiles(cls) -> Dict[Manufacturer, VendorProfile]:
        """Return all registered vendor profiles"""
        cls._initialize()
        return dict(cls._profiles)
