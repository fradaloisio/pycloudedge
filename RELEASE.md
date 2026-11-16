# pycloudedge 0.1.13 release preparation

This candidate is on `fix/corrective-0.1.13` and is required by CloudEdge Home
Assistant **1.5.0**. Its release notes are the `0.1.13` section of `CHANGELOG.md`.
Supported Python versions start at **3.10**; CI tests **3.10** and **3.14**.

## Build a candidate without releasing

Use an isolated environment with `requirements-dev.txt` installed. From this
repository root:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -p no:cacheprovider -o addopts='' -q
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYCLOUDEDGE=0.1.13 python -m build --outdir dist-staging/0.1.13
python -m twine check --strict dist-staging/0.1.13/*
git diff --check
```

The version override produces a local **0.1.13** candidate without a Git tag;
it does not publish anything. `cloudedge/_version.py` is generated and ignored,
so there is no tracked version constant to bump.

Install the wheel in fresh Python **3.10** and **3.14** environments with
`pytest` and the runtime dependencies, including the optional `paho-mqtt`.
Copy `tests/` outside this checkout and run the suite from there so imports
exercise the installed wheel. Check both `cloudedge.__version__` and
`importlib.metadata.version('pycloudedge')` report **0.1.13**. Also build a wheel
from the source archive and run the same checks. This catches missing source
files and package metadata differences.

Install the candidate wheel into the integration's Python 3.14 test environment
before installing its requirements, then run the integration suite. For the
coordinated publication sequence, see `../cloudedge-ha/RELEASE.md`.

## Publication after approval

1. Review and commit the candidate with the owner's configured author identity,
   without `Co-authored-by` trailers; merge it and require CI to pass.
2. Create and push **v0.1.13** on the approved commit.
3. In a fresh checkout of that tag, with no version override, run `python -m build`
   into an empty output directory and `python -m twine check --strict dist/*`.
   Confirm the wheel and source archive metadata both say **0.1.13**, and rerun
   installed-artifact tests.
4. Upload only those checked **0.1.13** files to PyPI and publish the GitHub
   release using the changelog section as its notes.
5. Verify a new environment can install **pycloudedge==0.1.13** from PyPI before
   releasing CloudEdge Home Assistant **1.5.0**.

Prepared local archives are candidates, not published releases. Keep account
files, logs, caches and credentials out of every archive.
