import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / 'bin/worktree-harness'

class WorktreeTests(unittest.TestCase):
    def test_isolated_creation_collision_resistance_and_dirty_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'README').write_text('baseline')
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','baseline'],check=True)
            def run(*args):
                return subprocess.run(['bash',str(HARNESS),*args],cwd=root,capture_output=True,text=True)
            first=run('create','agent/a')
            self.assertEqual(first.returncode,0,first.stderr)
            second=run('create','agent_a')
            self.assertEqual(second.returncode,0,second.stderr)
            data=json.loads((root/'.worktrees/ports.json').read_text())
            self.assertEqual(len(set(data.values())),2)
            worktrees=subprocess.check_output(['git','-C',str(root),'worktree','list','--porcelain'],text=True)
            paths=[Path(line[len('worktree '):]) for line in worktrees.splitlines() if line.startswith('worktree ')][1:]
            (root/'.worktrees'/hashlib.sha256(b'agent/a').hexdigest()[:24]/'valuable.txt').write_text('preserve this')
            refused=run('remove','agent/a')
            self.assertNotEqual(refused.returncode,0)
            self.assertTrue((root/'.worktrees'/hashlib.sha256(b'agent/a').hexdigest()[:24]/'valuable.txt').exists())
            injection=run('create',"bad';touch OWNED;#")
            self.assertNotEqual(injection.returncode,0)
            self.assertFalse((root/'OWNED').exists())
