"""Build a merged restore in staging without modifying current data."""
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil

from storage_security import atomic_write


def merge_history(current, incoming):
    if isinstance(current, dict) and isinstance(incoming, dict):
        result = dict(current)
        for key, value in incoming.items():
            result[key] = merge_history(current[key], value) if key in current else value
        return result
    if isinstance(current, list) and isinstance(incoming, list):
        result = list(current)
        seen = {json.dumps(item, sort_keys=True) for item in result}
        for item in incoming:
            identity = json.dumps(item, sort_keys=True)
            if identity not in seen:
                result.append(item)
                seen.add(identity)
        return result
    return current


def merge_restore(root, staged, runtime):
    root, staged = Path(root), Path(staged)
    added = 0
    # Preserve backup-only/conflicting artifacts under reports, with stable names.
    def preserve(relative, data):
        digest = hashlib.sha256(data).hexdigest()
        destination = staged / 'reports' / 'restored_backups' / digest / relative
        atomic_write(destination, data)

    for folder in ('config', 'live', 'reports'):
        for current in sorted((root / folder).rglob('*')):
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise ValueError('Current data contains a filesystem link.')
            if not current.is_file():
                continue
            relative = current.relative_to(root)
            target = staged / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(current, target)
                continue
            old, backup = current.read_bytes(), target.read_bytes()
            if old == backup:
                continue
            if folder == 'config' and current.name in ('approvals.json', 'viewed_records.json', 'backup_history.json'):
                def decode(data):
                    text = data.decode('utf-8')
                    return json.loads(runtime['deobfuscate_data'](text) or text)
                combined = merge_history(decode(old), decode(backup))
                atomic_write(target, runtime['obfuscate_data'](json.dumps(combined)))
            elif folder == 'live' and current.suffix.lower() == '.csv' and not current.name.lower().startswith('audit_'):
                existing = list(csv.reader(io.StringIO(old.decode('utf-8-sig'))))
                incoming = list(csv.reader(io.StringIO(backup.decode('utf-8-sig'))))
                if not existing or not incoming or existing[0] != incoming[0] or existing[0][0] != 'Timestamp':
                    preserve(relative, backup)
                    atomic_write(target, old)
                    continue
                rows = existing[1:]
                seen = {tuple(row) for row in rows}
                readings = existing[0] == ['Timestamp', 'IP', 'Port', 'SlaveID', 'Register', 'Value']
                keys = {tuple(row[:5]) for row in rows} if readings else set()
                for row in incoming[1:]:
                    if len(row) != len(existing[0]):
                        raise ValueError(f'Malformed backup CSV: {relative}')
                    if tuple(row) in seen:
                        continue
                    if readings and tuple(row[:5]) in keys:
                        preserve(relative, backup)
                        continue
                    rows.append(row)
                    seen.add(tuple(row))
                    keys.add(tuple(row[:5]))
                    if readings:
                        added += 1
                output = io.StringIO(newline='')
                writer = csv.writer(output)
                writer.writerow(existing[0])
                writer.writerows(sorted(rows, key=lambda row: row[0]))
                atomic_write(target, output.getvalue())
            else:
                preserve(relative, backup)
                atomic_write(target, old)
    # Audit files imported from another snapshot cannot join the active hash chain.
    for audit in (staged / 'live').glob('Audit_*.csv'):
        relative = audit.relative_to(staged)
        if not (root / relative).exists():
            preserve(relative, audit.read_bytes())
            audit.unlink()
    return added
