"""Plaintext exports are retired; use encrypted portable backups."""


def main():
    print("Use Administration > Backup & Restore > CREATE BACKUP NOW.")
    print("Transfer the .ttbackup file, then use SELECT BACKUP FILE on the destination computer.")
    print("Keep the backup password separately; it is required for restoration.")


if __name__ == "__main__":
    main()
