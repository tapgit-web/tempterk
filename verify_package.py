"""Read-only checks for the built Windows package and its shortcut resources."""
from pathlib import Path
import marshal
import msilib
import pefile
from PyInstaller.archive.readers import CArchiveReader
from build_msi import VERSION, NAME

root = Path(__file__).resolve().parent
exe = root / 'dist' / (NAME + '.exe')
pe = pefile.PE(str(exe))
resources = {entry.id: entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
assert 3 in resources and 14 in resources, 'Missing application icon resources'
manifest = resources[24].directory.entries[0].directory.entries[0].data.struct
assert b'requireAdministrator' in pe.get_data(manifest.OffsetToData, manifest.Size)
archive = CArchiveReader(str(exe))
main_code = marshal.loads(archive.extract('2'))
assert main_code == compile((root / '2.py').read_bytes(), main_code.co_filename, 'exec'), 'Packaged application differs from current source'
modules = archive.open_embedded_archive('PYZ.pyz')
for name in ('backup_engine', 'storage_security', 'restore_merge', 'password_policy', 'date_display', 'graph_format'):
    code = modules.extract(name)
    assert code == compile((root / (name + '.py')).read_bytes(), code.co_filename, 'exec'), f'Packaged {name} differs from current source'
assert any(name.replace('\\', '/') == 'certifi/cacert.pem' for name in archive.toc)
db = msilib.OpenDatabase(str(root / 'dist' / f'TempTrek-{VERSION}-x64.msi'), msilib.MSIDBOPEN_READONLY)
view = db.OpenView('SELECT `Name`, `Data` FROM `Icon`')
view.Execute(None)
record = view.Fetch()
assert record.GetString(1) == 'TempTrekIcon.exe'
view = db.OpenView('SELECT `Shortcut`, `Icon_`, `IconIndex` FROM `Shortcut`')
view.Execute(None)
shortcuts = set()
while (record := view.Fetch()) is not None:
    shortcuts.add(record.GetString(1))
    assert record.GetString(2) == 'TempTrekIcon.exe'
    assert record.GetInteger(3) == 0
assert shortcuts == {'StartMenuShortcut', 'DesktopShortcut'}
print(f'PASS: {VERSION} MSI shortcuts, embedded logo, administrator manifest and CA bundle')
