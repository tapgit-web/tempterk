"""Precision shared by live, historical and exported graphs."""
def channel_precision(device, register, default=3):
    if device.get('types', {}).get(str(register)) == 'BOOLEAN':
        return 0
    try:
        return max(0, min(10, int(device.get('decimals', {}).get(str(register), default))))
    except (TypeError, ValueError):
        return 3


def graph_precision(devices, lids, default=3):
    if isinstance(lids, str):
        lids = [lids]
    precisions = []
    for lid in lids:
        for device in devices:
            prefix = f"{device['ip']}:{device['port']}:{device.get('slave_id', 1)}:"
            if str(lid).startswith(prefix):
                precisions.append(channel_precision(device, str(lid)[len(prefix):], default))
                break
    return max(precisions, default=int(default))
