import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RuntimeTests(unittest.TestCase):
    def run_plan(self, plan, workspace, state):
        path = workspace / 'plan.json'
        path.write_text(json.dumps(plan))
        return subprocess.run([sys.executable, str(ROOT / 'agents/orchestrator.py'), '--plan', str(path), '--workspace', str(workspace), '--state', str(state)], capture_output=True, text=True)

    def test_dependencies_and_durable_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = {'id': 'first', 'command': [sys.executable, '-c', "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"], 'worktree': '.'}
            second = {'id': 'second', 'depends': ['first'], 'command': [sys.executable, '-c', "from pathlib import Path; assert Path('count').read_text()=='x'"], 'worktree': '.'}
            state = root / 'state.sqlite'
            result = self.run_plan({'tasks': [second, task]}, root, state)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'count').exists(), result.stdout)
            self.assertEqual(self.run_plan({'tasks': [second, task]}, root, state).returncode, 2)
            self.assertEqual((root / 'count').read_text(), 'x')
            task['command'][-1] = 'raise SystemExit(0)'
            self.assertNotEqual(self.run_plan({'tasks': [task]}, root, state).returncode, 0)

    def test_invalid_graph_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                [{'id': 'a', 'depends': ['missing'], 'command': ['true']}],
                [{'id': 'a', 'depends': ['a'], 'command': ['true']}],
                [{'id': 'a', 'command': ['true'], 'worktree': '..'}],
                [{'id': 'a', 'command': 'echo injected'}],
            ]
            for tasks in cases:
                self.assertNotEqual(self.run_plan({'tasks': tasks}, root, root/'state').returncode, 0)

    def test_failure_blocks_dependents_and_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = [
                {'id': 'fail', 'command': [sys.executable, '-c', "from pathlib import Path; p=Path('attempts'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); raise SystemExit(1)"], 'attempts': 2},
                {'id': 'blocked', 'depends': ['fail'], 'command': [sys.executable, '-c', "from pathlib import Path; Path('bad').touch()"]},
            ]
            self.assertNotEqual(self.run_plan({'tasks': tasks}, root, root/'state').returncode, 0)
            self.assertTrue((root/'attempts').exists())
            self.assertEqual((root/'attempts').read_text(), 'xx')
            self.assertFalse((root/'bad').exists())
            self.run_plan({'tasks': tasks}, root, root/'state')
            self.assertEqual((root/'attempts').read_text(), 'xx')

    def test_timeout_terminates_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_plan({'tasks': [{'id':'slow','command':[sys.executable,'-c','import time; time.sleep(10)'],'timeout':0.1}]},root,root/'state')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('timeout',result.stdout)

if __name__ == '__main__':
    unittest.main()

class SourceIdentityTests(unittest.TestCase):
    def test_cached_success_rejected_after_source_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'.gitignore').write_text('state*\nplan.json\n')
            (root/'source.py').write_text('version = 1\n')
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','baseline'],check=True)
            plan = {'tasks':[{'id':'check','command':[sys.executable,'-c','pass']}]}
            runner = RuntimeTests()
            self.assertEqual(runner.run_plan(plan,root,root/'state').returncode,0)
            self.assertEqual(runner.run_plan(plan,root,root/'state').returncode,0)
            (root/'source.py').write_text('version = 2\n')
            result = runner.run_plan(plan,root,root/'state')
            self.assertNotEqual(result.returncode,0,'cached success cannot certify different code')

class DaemonTests(unittest.TestCase):
    def test_once_propagates_plan_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'plan.json').write_text(json.dumps({'tasks':[{'id':'fail','command':[sys.executable,'-c','raise SystemExit(3)']}]}))
            result=subprocess.run([sys.executable,str(ROOT/'agents/daemon.py'),'--once','--workspace',str(root),'--plan',str(root/'plan.json'),'--state',str(root/'state')],capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertIn('task_result',result.stdout)

    def test_interrupted_running_task_is_not_replayed(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            plan={'tasks':[{'id':'once','command':[sys.executable,'-c',"from pathlib import Path; Path('ran').touch()"]}]}
            runner=RuntimeTests()
            self.assertEqual(runner.run_plan(plan,root,root/'state').returncode,0)
            with sqlite3.connect(root/'state') as db:
                db.execute("UPDATE tasks SET status='running'")
            (root/'ran').unlink()
            result=runner.run_plan(plan,root,root/'state')
            self.assertEqual(result.returncode,2)
            self.assertFalse((root/'ran').exists())

class NonGitIdentityTests(unittest.TestCase):
    def test_non_git_success_is_never_reused_without_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check.py').write_text('pass\n')
            plan={'tasks':[{'id':'check','command':[sys.executable,'check.py']}]}
            runner=RuntimeTests()
            self.assertEqual(runner.run_plan(plan,root,root/'state').returncode,0)
            (root/'check.py').write_text('raise SystemExit(9)\n')
            self.assertNotEqual(runner.run_plan(plan,root,root/'state').returncode,0)
