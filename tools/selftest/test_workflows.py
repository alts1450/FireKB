"""Desktop intake and reliability regression entry for doctor/selftest."""
import sys
import unittest
from pathlib import Path

def main():
    root = Path(__file__).resolve().parents[2]
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 4

if __name__ == '__main__':
    raise SystemExit(main())
