import unittest
from scripts.container_validation import runtime_command, validate_image, artifact_hashes

class ContainerValidationTests(unittest.TestCase):
    def test_enforces_isolation_and_immutable_image(self):
        image='sha256:'+'a'*64
        argv=runtime_command(image,'test-run','/tmp/evidence')
        for flag in ('--read-only','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--user=65532:65532','--memory=4g','--cpus=2','--pids-limit=512'):
            self.assertIn(flag,argv)
        self.assertEqual(argv[-1],image)
        self.assertNotIn('--privileged',argv)
        self.assertNotIn('/var/run/docker.sock',' '.join(argv))

    def test_rejects_mutable_or_malformed_images(self):
        for value in ('node:22','sha256:abc','sha256:'+'A'*64,''):
            with self.assertRaises(ValueError):validate_image(value)

    def test_rejects_container_created_symlinks(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);artifacts=root/'artifacts';artifacts.mkdir()
            secret=root/'host';secret.write_text('host data')
            (artifacts/'provenance.json').symlink_to(secret)
            with self.assertRaises(ValueError):artifact_hashes(artifacts)
            self.assertEqual(secret.read_text(),'host data')

    def test_accepts_only_regular_single_link_artifacts(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'result').write_text('result')
            self.assertEqual(set(artifact_hashes(root)),{'result'})
            (root/'nested').mkdir()
            with self.assertRaises(ValueError):artifact_hashes(root)
