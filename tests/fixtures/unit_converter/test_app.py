"""Unit tests for the unit converter app.

Covers the acceptance criteria defined in SPEC.md.
"""

import subprocess
import sys
import unittest
from pathlib import Path

# Import the convert function from app
from app import convert, main

class TestConvertFunction(unittest.TestCase):
    def assertAlmostEqualRel(self, a, b, rel_tol=1e-6):
        # Helper for relative tolerance
        self.assertTrue(abs(a - b) <= rel_tol * max(abs(a), abs(b), 1.0), f"{a} !≈ {b}")

    def test_length_conversions(self):
        self.assertAlmostEqualRel(convert(1, "km", "m"), 1000)
        self.assertAlmostEqualRel(convert(5, "mi", "km"), 8.04672)
        self.assertAlmostEqualRel(convert(2, "ft", "in"), 24)

    def test_mass_conversions(self):
        self.assertAlmostEqualRel(convert(2, "lb", "kg"), 0.907184)
        self.assertAlmostEqualRel(convert(500, "g", "oz"), 17.6369805)

    def test_temperature_conversions(self):
        self.assertAlmostEqualRel(convert(100, "c", "f"), 212)
        self.assertAlmostEqualRel(convert(0, "k", "c"), -273.15)
        self.assertAlmostEqualRel(convert(32, "f", "c"), 0)

    def test_invalid_unit(self):
        with self.assertRaises(ValueError):
            convert(1, "lightyear", "m")
        with self.assertRaises(ValueError):
            convert(1, "m", "stone")

    def test_mismatched_categories(self):
        with self.assertRaises(ValueError):
            convert(1, "m", "kg")
        with self.assertRaises(ValueError):
            convert(1, "c", "ft")

class TestCLI(unittest.TestCase):
    def run_cli(self, args):
        """Run the module as a subprocess and capture output.
        Returns (returncode, stdout, stderr).
        """
        cmd = [sys.executable, "-m", "app"] + args
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def test_cli_success(self):
        rc, out, err = self.run_cli(["5", "mi", "km"])
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        # Expected result rounded to 4 decimal places
        self.assertEqual(out, f"{convert(5, 'mi', 'km'):.4f}")

    def test_cli_help(self):
        rc, out, err = self.run_cli(["-h"])
        self.assertEqual(rc, 0)
        self.assertIn("Convert between length, mass, and temperature units", out)
        self.assertEqual(err, "")

    def test_cli_invalid_args(self):
        rc, out, err = self.run_cli(["foo", "m", "km"])
        self.assertNotEqual(rc, 0)
        self.assertIn("could not convert string to float", err)
        self.assertEqual(out, "")

    def test_cli_invalid_conversion(self):
        rc, out, err = self.run_cli(["1", "m", "kg"])
        self.assertNotEqual(rc, 0)
        self.assertIn("Cannot convert between different categories", err)
        self.assertEqual(out, "")

if __name__ == "__main__":
    unittest.main()
