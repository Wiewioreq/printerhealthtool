"""Unit tests for WMIAgent._safe_int helper"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from windows_agents import WMIAgent


class TestSafeInt(unittest.TestCase):
    """Tests for WMIAgent._safe_int — verifies it handles WMI type quirks"""

    def setUp(self):
        self.agent = WMIAgent()

    def test_none_returns_default(self):
        self.assertEqual(self.agent._safe_int(None), 0)

    def test_none_returns_custom_default(self):
        self.assertEqual(self.agent._safe_int(None, default=1), 1)

    def test_valid_integer(self):
        self.assertEqual(self.agent._safe_int(42), 42)

    def test_string_integer(self):
        """WMI sometimes returns numeric values as strings"""
        self.assertEqual(self.agent._safe_int("12345678"), 12345678)

    def test_zero_string(self):
        self.assertEqual(self.agent._safe_int("0"), 0)

    def test_invalid_string_returns_default(self):
        self.assertEqual(self.agent._safe_int("not_a_number"), 0)

    def test_invalid_string_returns_custom_default(self):
        self.assertEqual(self.agent._safe_int("bad", default=99), 99)

    def test_float_truncates(self):
        self.assertEqual(self.agent._safe_int(3.9), 3)

    def test_zero_integer(self):
        self.assertEqual(self.agent._safe_int(0), 0)


if __name__ == '__main__':
    unittest.main()
