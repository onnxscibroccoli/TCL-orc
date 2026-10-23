"""Bundle contract/circuit sources into the wheel as ``tcl/_data`` so an installed
``tcl zk`` / ``tcl chain`` works without a source checkout. Fails the build if the
sources (incl. the OpenZeppelin submodules) are absent, rather than shipping a
wheel that only fails at runtime."""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
TREES = [("circuits", "*.circom"), ("contracts", "*.sol"),
         ("lib/openzeppelin-contracts/contracts", "*.sol"),
         ("lib/openzeppelin-contracts-upgradeable/contracts", "*.sol")]
LICENSES = ["LICENSE", "lib/openzeppelin-contracts/LICENSE",
            "lib/openzeppelin-contracts-upgradeable/LICENSE"]


class BuildWithData(build_py):
    def run(self):
        super().run()
        if getattr(self, "editable_mode", False):
            return  # editable installs resolve artifacts from the checkout itself
        dest = Path(self.build_lib) / "tcl" / "_data"
        shutil.rmtree(dest, ignore_errors=True)
        for tree, pat in TREES:
            src = HERE / tree
            files = sorted(src.rglob(pat)) if src.is_dir() else []
            if not files:
                raise SystemExit(f"build: {tree} has no {pat} files "
                                 "(run `git submodule update --init --recursive`)")
            for f in files:
                out = dest / f.relative_to(HERE)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
        for lic in LICENSES:
            if (HERE / lic).is_file():
                out = dest / lic
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HERE / lic, out)


setup(cmdclass={"build_py": BuildWithData})
