# Building ACE-DAW Transfer 1.4.10

## Windows x64

Use 64-bit CPython 3.10 with Tcl/Tk. From a clean virtual environment in the repository root, run the following in PowerShell. The MSI builder uses the Windows Installer API provided by this Python version.

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -q
python -m PyInstaller --clean --noconfirm ACE-DAW-Transfer.spec
python packaging/build_windows_msi.py dist/ACE-DAW-Transfer-1.4.10 dist/ACE-DAW-Transfer-1.4.10-Windows-x64.msi
$env:PYTHONPATH=(Get-Location).Path
python packaging/smoke.py dist/ACE-DAW-Transfer-1.4.10/ACE-DAW-Transfer.exe
```

Choose a fresh MSI filename for each build. The payload installs per user and adds a Start menu entry.

## macOS

The **macOS installers** workflow uses native Apple Silicon and Intel machines with Python 3.11 and Tcl/Tk. Each builds an architecture-specific application, checks its executable and ad hoc signature, exercises conversions, and creates a component package. The distribution selects the native component on installation. The workflow installs the combined package and exercises that copy on each architecture. The **Installed macOS window** workflow can verify window startup and language switching against an existing installer run.

To build one native component on a Mac from the repository root:

```sh
python -m pip install -r packaging/requirements-mac.txt
bash packaging/build-mac.sh
```

With both component packages in the same directory, assemble the distribution:

```sh
productbuild --distribution packaging/Distribution.xml --package-path components ACE-DAW-Transfer-1.4.10-macOS.pkg
```

## Manuals

The two self-contained HTML documents under `docs/` are the source for the English and Chinese PDFs. Print using their A4 CSS with background graphics enabled and browser headers and footers disabled.

