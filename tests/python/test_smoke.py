"""Initial test-harness smoke tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from native_host import HOST_NAME, PROTOCOL_VERSION


ROOT = Path(__file__).resolve().parents[2]


class HarnessSmokeTest(unittest.TestCase):
    """Verify canonical JSON fixtures load without external dependencies."""

    def test_protocol_contract_is_valid_json(self) -> None:
        contract = json.loads((ROOT / "protocol" / "v2.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["version"], PROTOCOL_VERSION)
        self.assertEqual(contract["nativeHost"], HOST_NAME)
        self.assertIn("probe", contract["actions"])


if __name__ == "__main__":
    unittest.main()
