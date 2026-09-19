"""Build a native x64 Windows Installer package using Python 3.12 msilib.

Run after PyInstaller: python build_msi.py
No customer configuration, license, passwords, or readings enter the package.
"""
import os
from pathlib import Path
import re
import sys
import uuid

if sys.platform != "win32" or sys.version_info >= (3, 13):
    raise SystemExit("Build the MSI with Windows Python 3.12 (msilib is required).")

import msilib
from msilib import schema, sequence

ROOT = Path(__file__).resolve().parent
NAME = "TempTrek Environmental Monitoring System"
VERSION = re.search(r"^AppVersion=(.+)$", (ROOT / "installer.iss").read_text(), re.MULTILINE).group(1).strip()
UPGRADE_CODE = "{4D6E0706-0706-40D0-9F4F-A6E0196D0479}"


def guid(label):
    return "{" + str(uuid.uuid5(uuid.UUID(UPGRADE_CODE.strip("{}")), label)).upper() + "}"


def add_dialogs(db):
    msilib.add_data(db, "TextStyle", [("DefaultFont", "Segoe UI", 9, 0, 0), ("HeadingFont", "Segoe UI", 14, 0, 1)])
    welcome = msilib.Dialog(db, "WelcomeDlg", 50, 50, 370, 230, 3, "[ProductName] Setup", "Install", "Install", "Cancel")
    welcome.text("Heading", 20, 18, 330, 35, 3, r"{\HeadingFont}Install TempTrek")
    welcome.text("Description", 20, 64, 330, 85, 3,
                 "Install [ProductName] [ProductVersion] for all users.\r\n\r\nWindows administrator permission is required. Your existing application data is preserved.")
    welcome.line("Divider", 15, 180, 340, 1)
    welcome.pushbutton("Install", 210, 194, 65, 20, 3, "Install", "Cancel").event("EndDialog", "Return")
    welcome.pushbutton("Cancel", 285, 194, 65, 20, 3, "Cancel", "Install").event("EndDialog", "Exit")

    progress = msilib.Dialog(db, "ProgressDlg", 50, 50, 370, 180, 1, "[ProductName] Setup", "Cancel", "Cancel", "Cancel")
    progress.text("Heading", 20, 20, 330, 30, 3, r"{\HeadingFont}Updating TempTrek")
    progress.text("Action", 20, 70, 330, 30, 3, "Please wait...").mapping("ActionText", "Text")
    progress.control("Progress", "ProgressBar", 20, 115, 330, 16, 1, None, None, None, None).mapping("SetProgress", "Progress")
    progress.pushbutton("Cancel", 285, 150, 65, 20, 3, "Cancel", None).event("EndDialog", "Exit")

    for name, heading, body in (
        ("ExitDialog", "Setup completed", "The requested operation completed successfully. You can launch TempTrek from the Start menu after installation."),
        ("FatalError", "Setup failed", "Windows Installer could not complete the operation. Consult the installer log for details."),
        ("UserExit", "Setup cancelled", "The installation was cancelled."),
    ):
        dialog = msilib.Dialog(db, name, 50, 50, 370, 210, 3, "[ProductName] Setup", "Finish", "Finish", "Finish")
        dialog.text("Heading", 20, 20, 330, 35, 3, r"{\HeadingFont}" + heading)
        dialog.text("Message", 20, 70, 330, 70, 3, body)
        dialog.pushbutton("Finish", 285, 170, 65, 20, 3, "Finish", None).event("EndDialog", "Return")


def build():
    executable = ROOT / "dist" / (NAME + ".exe")
    if not executable.is_file():
        raise SystemExit("Build the EXE first; dist application is missing.")
    output = ROOT / "dist" / f"TempTrek-{VERSION}-x64.msi"
    db = msilib.init_database(str(output), schema, NAME, guid("product:" + VERSION), VERSION, "The Automation People")
    msilib.add_data(db, "Property", [
        ("UpgradeCode", UPGRADE_CODE), ("ALLUSERS", "1"), ("INSTALLLEVEL", "1"),
        ("ARPNOMODIFY", "1"), ("ARPNOREPAIR", "1"), ("ARPPRODUCTICON", "TempTrekIcon.exe"),
        ("DefaultUIFont", "DefaultFont"), ("SecureCustomProperties", "OLDERPRODUCTS;NEWERPRODUCTS"),
    ])
    msilib.add_data(db, "LaunchCondition", [
        ("VersionNT64", "TempTrek requires 64-bit Windows."),
        ("Installed OR NOT NEWERPRODUCTS", "A newer version of TempTrek is already installed."),
    ])
    msilib.add_data(db, "Upgrade", [
        (UPGRADE_CODE, None, VERSION, None, 1, None, "OLDERPRODUCTS"),
        (UPGRADE_CODE, VERSION, None, None, 2, None, "NEWERPRODUCTS"),
    ])
    for table in ("InstallExecuteSequence", "InstallUISequence", "AdminExecuteSequence", "AdminUISequence", "AdvtExecuteSequence"):
        rows = list(getattr(sequence, table))
        if table == "InstallExecuteSequence":
            rows = [(action, condition, 1510 if action == "RemoveExistingProducts" else number) for action, condition, number in rows]
        if table == "InstallUISequence":
            rows += [("WelcomeDlg", "NOT Installed", 1250), ("ProgressDlg", None, 1290)]
        if table == "AdminUISequence":
            rows = [r for r in rows if r[0] not in ("ExitDialog", "FatalError", "UserExit")]
        msilib.add_data(db, table, rows)
    add_dialogs(db)

    cab = msilib.CAB("TempTrek.cab")
    feature = msilib.Feature(db, "MainApplication", "TempTrek", "Desktop application", 1, directory="INSTALLDIR")
    feature.set_current()
    root = msilib.Directory(db, cab, None, str(ROOT), "TARGETDIR", "SourceDir")
    program_files = msilib.Directory(db, cab, root, "", "ProgramFiles64Folder", ".")
    install = msilib.Directory(db, cab, program_files, "", "INSTALLDIR", "TMPTREK|" + NAME, componentflags=0)
    install.start_component("Application", feature, 0, NAME + ".exe", guid("component:application"))
    file_id = install.add_file(NAME + ".exe", src=str(executable))
    menu = msilib.Directory(db, cab, root, "", "ProgramMenuFolder", ".")
    msilib.Directory(db, cab, menu, "", "AppMenuFolder", "TMPTREK|TempTrek")
    msilib.Directory(db, cab, root, "", "DesktopFolder", ".")
    # Advertised shortcuts require a PE icon source with the target's extension.
    # Use the built application's embedded logo, also for Installed apps.
    msilib.add_data(db, "Icon", [("TempTrekIcon.exe", msilib.Binary(str(executable)))])
    msilib.add_data(db, "Shortcut", [
        ("StartMenuShortcut", "AppMenuFolder", "TempTrek", "Application", "MainApplication", None, NAME, None, "TempTrekIcon.exe", 0, 1, "INSTALLDIR"),
        ("DesktopShortcut", "DesktopFolder", "TempTrek", "Application", "MainApplication", None, NAME, None, "TempTrekIcon.exe", 0, 1, "INSTALLDIR"),
    ])
    msilib.add_data(db, "RemoveFile", [("RemoveAppMenuFolder", "Application", None, "AppMenuFolder", 2)])
    cab.commit(db)
    db.Commit()
    verify = db
    view = verify.OpenView("SELECT `File`, `FileSize` FROM `File`")
    view.Execute(None)
    record = view.Fetch()
    assert record.GetString(1) == file_id and record.GetInteger(2) == executable.stat().st_size
    assert view.Fetch() is None, "Unexpected extra files in MSI"
    print(f"Created: {output}")
    print(f"Version: {VERSION}; architecture: x64; embedded application: {executable.stat().st_size:,} bytes")


if __name__ == "__main__":
    build()
