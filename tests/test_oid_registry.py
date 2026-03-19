"""Unit tests for VendorDetector with real sysDescr strings"""
import sys
import os
import unittest

# Allow running from any working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oid_registry import VendorDetector, Manufacturer


class TestVendorDetection(unittest.TestCase):

    REAL_SYSDESCR = [
        ("HP ETHERNET MULTI-ENVIRONMENT,ROM V.49.006.00,JETDIRECT,JD172,EEPROM V.49.006.01", Manufacturer.HP),
        ("HP LaserJet Pro MFP M428fdn", Manufacturer.HP),
        ("Canon iR-ADV C5560 /P", Manufacturer.CANON),
        ("CANON MF740C Series", Manufacturer.CANON),
        ("Xerox VersaLink C405 v53.006.01.000", Manufacturer.XEROX),
        ("Fuji Xerox ApeosPort-V C5575", Manufacturer.XEROX),
        ("Brother HL-L2370DW series", Manufacturer.BROTHER),
        ("Brother MFC-L8900CDW series", Manufacturer.BROTHER),
        ("RICOH MP C3004 1.12 / RICOH Network Printer C model", Manufacturer.RICOH),
        ("RICOH IM C3000", Manufacturer.RICOH),
        ("KYOCERA ECOSYS M5526cdw", Manufacturer.KYOCERA),
        ("Kyocera TASKalfa 3253ci", Manufacturer.KYOCERA),
        ("KONICA MINOLTA bizhub C558", Manufacturer.KONICA_MINOLTA),
        ("Samsung CLX-6260FW Series", Manufacturer.SAMSUNG),
        ("EPSON AL-M320DN", Manufacturer.EPSON),
        ("Lexmark MS610dn", Manufacturer.LEXMARK),
    ]

    def test_vendor_detection_from_sysdescr(self):
        for sys_descr, expected_manufacturer in self.REAL_SYSDESCR:
            with self.subTest(sys_descr=sys_descr):
                result = VendorDetector.detect(sys_descr)
                self.assertEqual(
                    result.manufacturer, expected_manufacturer,
                    f"Expected {expected_manufacturer.value} for '{sys_descr}', "
                    f"got {result.manufacturer.value} (confidence={result.confidence})"
                )
                self.assertGreater(
                    result.confidence, 0.5,
                    f"Low confidence for '{sys_descr}': {result.confidence}"
                )

    def test_model_hint_extracted(self):
        result = VendorDetector.detect("HP LaserJet Pro MFP M428fdn")
        self.assertIsNotNone(result.model_hint)
        self.assertIn("428", result.model_hint)

    def test_unknown_returns_generic(self):
        result = VendorDetector.detect("Unknown device XYZ-123")
        self.assertEqual(result.manufacturer, Manufacturer.GENERIC)

    def test_empty_string(self):
        result = VendorDetector.detect("")
        self.assertEqual(result.manufacturer, Manufacturer.GENERIC)

    def test_enterprise_oid_detection(self):
        # HP enterprise OID prefix
        result = VendorDetector.detect("Some HP printer", "1.3.6.1.4.1.11.2.3.9.1")
        self.assertEqual(result.manufacturer, Manufacturer.HP)

        # Canon
        result = VendorDetector.detect("Canon device", "1.3.6.1.4.1.1602.1.1.1.1")
        self.assertEqual(result.manufacturer, Manufacturer.CANON)


if __name__ == '__main__':
    unittest.main()
