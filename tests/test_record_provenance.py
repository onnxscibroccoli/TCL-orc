import tempfile
from pathlib import Path
import unittest
from scripts.record_provenance import ARTIFACTS, inventory, check

class InventoryTests(unittest.TestCase):
    def test_changed_or_missing_artifacts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for rel in ARTIFACTS:
                path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic inventory fixture')
            manifest=inventory(root);check(root,manifest)
            (root/ARTIFACTS[0]).write_bytes(b'mutated')
            with self.assertRaises(ValueError):check(root,manifest)
            (root/ARTIFACTS[0]).unlink()
            with self.assertRaises(FileNotFoundError):inventory(root)
