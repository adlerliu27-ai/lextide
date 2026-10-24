# macOS only. Build through scripts/build_macos.py to stage icons and notices.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).parents[1]
staging = Path(os.environ['LEXTIDE_BUILD_STAGING'])
version = os.environ['LEXTIDE_BUILD_VERSION']
datas = collect_data_files('lextide', includes=['desktop/assets/**/*.png',
    'desktop/assets/**/*.svg', 'desktop/assets/**/*.json', 'desktop/translations/*.json'])
datas += [(str(Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
               / 'qtbase_zh_CN.qm'), 'PySide6/Qt/translations')]
datas += copy_metadata('lextide', recursive=True)
datas += [(str(staging / 'licenses'), 'licenses')]
datas += [(str(staging / 'build-info.json'), '.')]
datas += [(str(root / 'offline-runtime/pyproject.toml'), 'offline-runtime'),
          (str(root / 'offline-runtime/uv.lock'), 'offline-runtime')]
datas += [(str(root / 'docs/release'), 'release-docs')]
# The optional external Python must read source, not the frozen PYZ archive.
# Only application .py files, never research, user data, models or the offline venv.
for source in sorted((root / 'src/lextide').rglob('*.py')):
    relative = source.relative_to(root / 'src')
    datas.append((str(source), str(Path('worker-source') / relative.parent)))
a = Analysis(
    [str(root / 'packaging/macos/launcher.py')],
    pathex=[str(root / 'src')],
    binaries=[], datas=datas,
    hiddenimports=['lextide.desktop.worker', 'lextide.desktop.app', 'lextide.cli',
                   'lextide.longform.process'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'ruff', 'numpy', 'onnxruntime', 'kokoro_onnx',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf'],
    noarchive=False,
)
# Editable-install metadata otherwise leaks the build machine's source directory.
a.datas = [entry for entry in a.datas if not entry[0].endswith('/direct_url.json')]
# QtGui's default hook gathers this optional virtual keyboard and its GPL-only
# frameworks. The native macOS app uses system input, not an on-screen keyboard.
def uses_virtual_keyboard(entry):
    paths = [entry[0], entry[1]] if entry[2] == 'SYMLINK' else [entry[0]]
    return any('QtVirtualKeyboard' in path or 'libqtvirtualkeyboardplugin.dylib' in path
               for path in paths)

a.binaries = [entry for entry in a.binaries if not uses_virtual_keyboard(entry)]
a.datas = [entry for entry in a.datas if not uses_virtual_keyboard(entry)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='Lextide',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch='arm64',
    codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Lextide')
app = BUNDLE(
    coll, name='Lextide.app', icon=str(staging / 'Lextide.icns'),
    bundle_identifier='local.lextide.development', version=version,
    info_plist={
        'CFBundleDisplayName': 'Lextide', 'CFBundleName': 'Lextide',
        'CFBundleShortVersionString': version, 'CFBundleVersion': version,
        'NSHighResolutionCapable': True, 'NSPrincipalClass': 'NSApplication',
        'LSMinimumSystemVersion': '13.0',
        'LSApplicationCategoryType': 'public.app-category.education',
        'LextideDistributionStatus': 'DEVELOPMENT_RELEASE_PENDING',
        'LextideBuildCommit': os.environ['LEXTIDE_BUILD_COMMIT'],
        'CFBundleLocalizations': ['en', 'zh_CN'],
        'CFBundleDevelopmentRegion': 'en',
    },
)
