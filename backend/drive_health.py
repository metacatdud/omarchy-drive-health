#!/usr/bin/env python3
"""Read-only SMART collector and unprivileged report renderer. Python stdlib only."""
import argparse
from contextlib import closing
import datetime as dt
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sqlite3
import tempfile
import time

CACHE = Path('/var/lib/omarchy-drive-health/report.json')
SMARTCTL = '/usr/bin/smartctl'
LIMIT = 2 * 1024 * 1024
SYSFS = Path('/sys')


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def clean(value):
    return ''.join(c for c in str(value or '') if c.isprintable())[:160]


def read_json(path):
    with open(path, 'rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('JSON exceeds size limit')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def smart(args):
    # No shell, arbitrary flags, self-tests, writes, or user-provided device paths.
    result = subprocess.run([SMARTCTL, *args], capture_output=True, timeout=20)
    if len(result.stdout) > LIMIT:
        raise ValueError('SMART report exceeds size limit')
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError('Invalid SMART object')
    return data, result.returncode


def normalize(data, device, code=0):
    model = clean(data.get('model_name') or data.get('scsi_model_name') or device)
    serial = data.get('serial_number')
    identity = model + '\0' + str(serial or device)
    drive = dict(id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                 model=model, device=device, stable_id=bool(serial),
                 temperature_c=number(data.get('temperature', {}).get('current')),
                 power_on_hours=number(data.get('power_on_time', {}).get('hours')),
                 wear_used_pct=None, written_tb=None, media_errors=None,
                 spare_pct=None, status='unknown', issues=[], error_counters={})
    nvme = data.get('nvme_smart_health_information_log')
    passed = data.get('smart_status', {}).get('passed')
    if isinstance(nvme, dict):
        drive['wear_used_pct'] = number(nvme.get('percentage_used'))
        units = number(nvme.get('data_units_written'))
        drive['written_tb'] = units * 512000 / 1e12 if units is not None else None
        drive['media_errors'] = number(nvme.get('media_errors'))
        if drive['media_errors'] is not None:
            drive['error_counters']['media_errors'] = drive['media_errors']
        drive['spare_pct'] = number(nvme.get('available_spare'))
        drive['critical_warning'] = number(nvme.get('critical_warning'))
        if number(nvme.get('critical_warning')) not in (None, 0):
            drive['issues'].append('critical_warning')
    else:
        # Prefer smartmontools' normalized field; ATA attribute IDs are NOT universal.
        drive['wear_used_pct'] = number(data.get('endurance_used', {}).get('current'))
        attrs = {a.get('id'): a for a in data.get('ata_smart_attributes', {}).get('table', [])}
        if model.startswith('Samsung SSD '):
            wear = number(attrs.get(177, {}).get('value'))
            if drive['wear_used_pct'] is None and wear is not None and wear <= 100:
                drive['wear_used_pct'] = 100 - wear
            sectors = number(attrs.get(241, {}).get('raw', {}).get('value'))
            if sectors is not None:
                drive['written_tb'] = sectors * 512 / 1e12
        # Check decoded names as well as IDs: vendors reuse ATA numbers.
        known_error_names = {5: {'Reallocated_Sector_Ct', 'Reallocated_Sector_Count'},
                             187: {'Reported_Uncorrect'}, 197: {'Current_Pending_Sector'},
                             198: {'Offline_Uncorrectable'}}
        for ident, names in known_error_names.items():
            attr = attrs.get(ident, {})
            raw = number(attr.get('raw', {}).get('value')) if attr.get('name') in names else None
            if raw is not None:
                drive['error_counters']['ata_%s' % ident] = raw
            if raw is not None and raw > 0:
                drive['issues'].append('ata_%s' % ident)
    if passed is False or code & 8:
        drive['issues'].append('smart_failed')
    if drive['media_errors']:
        drive['issues'].append('media_errors')
    if drive['wear_used_pct'] is not None and drive['wear_used_pct'] >= 90:
        drive['issues'].append('wear_high')
    if code & 7:
        drive['issues'].append('read_incomplete')
    # These are distinct signals: bit 4 is a CURRENT threshold failure,
    # not a historical log entry. See smartctl(8), EXIT STATUS.
    for bit, reason in ((16, 'ata_threshold'), (32, 'ata_past_threshold'),
                        (64, 'error_log'), (128, 'selftest_log')):
        if code & bit:
            drive['issues'].append(reason)
    drive['status'] = ('critical' if any(x in drive['issues'] for x in ('smart_failed', 'critical_warning'))
                       else 'warning' if drive['issues'] else 'ok' if passed is True else 'unknown')
    return drive


def explain_health(drive, previous, previous_at, now):
    """Explain observable differences, never predict failure or infer event times.

    All existing health severities remain intact. An unchanged error counter
    is context, not grounds to downgrade a hardware warning.
    """
    previous = previous or {}
    details = []
    counters = {k: dict(v) for k, v in previous.get('counter_observations', {}).items()}
    # v0.2 already saved NVMe media_errors; use that genuine older observation.
    if 'media_errors' not in counters and number(previous.get('media_errors')) is not None and previous_at is not None:
        counters['media_errors'] = dict(value=previous['media_errors'], observed_at=previous_at,
                                        baseline_at=previous_at, last_increase_at=None)
    for key, value in drive.get('error_counters', {}).items():
        if number(value) is None:
            continue
        old = counters.get(key)
        comparable = old is not None and now > old['observed_at']
        delta = value - old['value'] if comparable else None
        state = dict(value=value, observed_at=now,
                     baseline_at=old['baseline_at'] if comparable and delta >= 0 else now,
                     last_increase_at=now if delta is not None and delta > 0 else old.get('last_increase_at') if comparable else None)
        if delta is not None and delta < 0:
            state['last_increase_at'] = None
        counters[key] = state
        if value > 0 or (delta is not None and delta < 0):
            details.append(dict(code=key, severity='warning', value=value,
                                previous_value=old['value'] if comparable else None,
                                comparison_at=old['observed_at'] if comparable else None,
                                delta=delta if delta is not None and delta > 0 else None,
                                observation='baseline' if delta is None else 'increased' if delta > 0 else 'decreased' if delta < 0 else 'unchanged',
                                baseline_at=state['baseline_at'], last_increase_at=state['last_increase_at']))
    drive['counter_observations'] = counters
    old_flags = previous.get('flag_observations', {})
    flags = {}
    nvme_bits = ((1, 'nvme_spare'), (2, 'nvme_temperature'), (4, 'nvme_reliability'),
                 (8, 'nvme_readonly'), (16, 'nvme_backup'))
    codes = [key for key in drive['issues'] if key not in drive.get('error_counters', {})]
    if 'critical_warning' in codes and isinstance(drive.get('critical_warning'), int):
        bits = drive['critical_warning']
        codes.remove('critical_warning')
        codes.extend(name for bit, name in nvme_bits if bits & bit)
        if bits & ~31:
            codes.append('critical_warning')  # Unknown/new bits stay critical.
    for key in codes:
        first = old_flags.get(key)
        if first is None:
            first = previous_at if key in previous.get('issues', []) and previous_at is not None else now
        if first > now:
            first = now  # Clock rollback: do not manufacture negative elapsed time.
        flags[key] = first
        details.append(dict(code=key, severity='critical' if key in ('smart_failed', 'critical_warning') or key.startswith('nvme_') else 'warning',
                            observation='flag', first_observed_at=first))
    drive['flag_observations'] = flags
    drive['health_details'] = details
    if drive['status'] == 'ok' and any(d['observation'] == 'decreased' for d in details):
        drive['status'] = 'warning'
    return drive


def sata_read_type(device, kind):
    """Use kernel metadata only; never probe an unknown disk to identify it."""
    block = SYSFS / 'class/block' / Path(device).name
    rotation = (block / 'queue/rotational').read_text().strip()
    if rotation == '1':
        return None
    if rotation != '0':
        raise ValueError('Skipped: unknown rotational status')
    hardware = (block / 'device').resolve(strict=True)
    if (any(re.fullmatch(r'usb\d+', part) for part in hardware.parts)
            or not any(re.fullmatch(r'ata\d+', part) for part in hardware.parts)):
        raise ValueError('Skipped: not a confirmed internal ATA device')
    if (block / 'device/power/runtime_status').read_text().strip() != 'active':
        raise ValueError('Skipped: device is suspended or runtime power state is unknown')
    # Linux --scan can label SATA as SCSI. Avoid -d auto: its detection commands
    # can wake a disk before smartctl applies the power-mode guard.
    return 'ata' if kind == 'ata' else 'sat'


def collect():
    report = dict(schema=1, collected_at=int(time.time()), drives=[], errors=[])
    try:
        scan, code = smart(['--scan', '--json'])
        if code:
            raise ValueError('SMART scan failed')
        seen = set()
        for entry in scan.get('devices', [])[:64]:
            device, kind = entry.get('name', ''), entry.get('type', '')
            # Internal SATA/NVMe only in v0.1; USB/RAID adapters need separate validation.
            if not re.fullmatch(r'/dev/(sd[a-z]+|nvme\d+(n\d+)?)', device) or kind not in ('sat', 'ata', 'nvme', 'scsi'):
                continue
            sys_device = (Path('/sys/class/block') / Path(device).name / 'device').resolve()
            if any(re.fullmatch(r'usb\d+', part) for part in sys_device.parts):
                continue
            try:
                if kind == 'nvme':
                    if not re.fullmatch(r'/dev/nvme\d+(n\d+)?', device):
                        continue
                    options = []
                else:
                    if not re.fullmatch(r'/dev/sd[a-z]+', device):
                        continue
                    kind = sata_read_type(device, kind)
                    if kind is None:
                        continue
                    # Fail closed both for standby/sleep and unsupported/unknown
                    # ATA power checks. Never retry without this guard.
                    options = ['--nocheck=standby,254,253']
                data, status = smart(['--all', '--json', '-d', kind, *options, device])
                if kind != 'nvme' and status in (253, 254):
                    reason = 'standby/sleep' if status == 254 else 'unknown or unsupported power mode'
                    report['errors'].append(device + ': SMART read skipped: ' + reason)
                    continue
                if kind != 'nvme' and data.get('rotation_rate') != 0:
                    if status & 7:
                        report['errors'].append('Could not identify SSD at ' + device)
                    continue
                drive = normalize(data, device, status)
                if drive['id'] not in seen:
                    report['drives'].append(drive)
                    seen.add(drive['id'])
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                report['errors'].append(device + ': ' + clean(exc))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report['errors'].append(clean(exc))
    return report


def atomic_write(path, report, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.report-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SettingsError(ValueError):
    pass


def save_warranty(path, drive_id, until, tbw):
    """User-owned settings only. Blank fields clear values; other drives survive."""
    if not re.fullmatch(r'[a-f0-9]{24}', drive_id):
        raise SettingsError('invalid_id')
    until, tbw = until.strip(), tbw.strip()
    if until:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', until):
                raise ValueError()
            dt.date.fromisoformat(until)
        except ValueError:
            raise SettingsError('invalid_date') from None
    rating = None
    if tbw:
        if len(tbw) > 32 or not re.fullmatch(r'[0-9]+(?:[.,][0-9]+)?', tbw):
            raise SettingsError('invalid_tbw')
        rating = float(tbw.replace(',', '.'))
        if not math.isfinite(rating) or not 0 < rating <= 1e9:
            raise SettingsError('invalid_tbw')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize read/merge/write across widget instances on multiple displays.
    with open(path.with_suffix(path.suffix + '.lock'), 'a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            settings = read_json(path) if path.exists() else {}
        except (ValueError, UnicodeError):
            raise SettingsError('invalid_config') from None
        drives = settings.setdefault('drives', {})
        if not isinstance(drives, dict) or not isinstance(drives.get(drive_id, {}), dict):
            raise SettingsError('invalid_config')
        entry = dict(drives.get(drive_id, {}))
        for key, value in (('warranty_until', until or None), ('rated_tbw', rating)):
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        if entry:
            drives[drive_id] = entry
        else:
            drives.pop(drive_id, None)
        atomic_write(path, settings, mode=0o600)


def record_history(report, database):
    """Enroll by identity, retain hourly samples for 90 days and daily spans for 5 years.

    Daily writes are only the difference between that UTC day's first/last
    observations. Missing time is not interpolated or billed to another day.
    """
    now = report['collected_at']
    hour = now // 3600
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
    database.parent.mkdir(parents=True, exist_ok=True)
    # Create privately before sqlite opens it; the public report is separate.
    fd = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(database, timeout=30)) as db, db:
        db.execute('PRAGMA journal_mode=DELETE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise ValueError('Unsupported history database version')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS drives (
                id TEXT PRIMARY KEY, first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL, latest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS samples (
                id TEXT NOT NULL, hour INTEGER NOT NULL, observed INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (id, hour));
            CREATE TABLE IF NOT EXISTS daily (
                id TEXT NOT NULL, day TEXT NOT NULL, first_at INTEGER NOT NULL,
                last_at INTEGER NOT NULL, first_tb REAL, last_tb REAL,
                reset INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (id, day));
            PRAGMA user_version=1;
        ''')
        for drive in report['drives']:
            # A failed read must not enroll an unstable placeholder identity.
            if 'read_incomplete' in drive['issues']:
                explain_health(drive, None, None, now)
                continue
            old = db.execute('SELECT last_seen,latest FROM drives WHERE id=?', (drive['id'],)).fetchone()
            explain_health(drive, json.loads(old[1]) if old else None, old[0] if old else None, now)
            payload = json.dumps(drive, allow_nan=False)
            db.execute('INSERT INTO drives VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, latest=excluded.latest',
                       (drive['id'], now, now, payload))
            db.execute('INSERT INTO samples VALUES (?, ?, ?, ?) ON CONFLICT(id,hour) DO UPDATE SET observed=excluded.observed, payload=excluded.payload',
                       (drive['id'], hour, now, payload))
            written = number(drive.get('written_tb'))
            if written is None:
                continue
            db.execute('''INSERT INTO daily (id,day,first_at,last_at,first_tb,last_tb) VALUES (?,?,?,?,?,?)
                ON CONFLICT(id,day) DO UPDATE SET
                last_at=excluded.last_at,
                reset=CASE WHEN excluded.last_tb < daily.last_tb THEN 1 ELSE daily.reset END,
                first_tb=COALESCE(daily.first_tb,excluded.first_tb),
                last_tb=COALESCE(excluded.last_tb,daily.last_tb)''',
                       (drive['id'], day, now, now, written, written))
        db.execute('DELETE FROM samples WHERE hour < ?', (hour - 90 * 24,))
        cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=1826)).isoformat()
        db.execute('DELETE FROM daily WHERE day < ?', (cutoff,))
        enrolled = db.execute('SELECT id,first_seen,last_seen,latest FROM drives ORDER BY last_seen DESC').fetchall()
        report['enrolled_count'] = len(enrolled)
        current = {d['id']: d for d in report['drives']}
        for ident, first_seen, last_seen, payload in enrolled:
            if ident not in current:
                if len(report['drives']) >= 64:
                    continue
                drive = json.loads(payload)
                drive.update(status='unknown', issues=['not_seen'], present=False)
                report['drives'].append(drive)
            else:
                drive = current[ident]
                drive['present'] = 'read_incomplete' not in drive['issues']
            drive.update(first_seen=first_seen, last_seen=last_seen)
            rows = db.execute('SELECT day,first_at,last_at,first_tb,last_tb,reset FROM daily WHERE id=? ORDER BY day DESC LIMIT 7', (ident,)).fetchall()
            drive['daily'] = [dict(day=d, coverage_hours=round((end-start)/3600, 2),
                                   written_gb=round((b-a)*1000, 3) if a is not None and b is not None and end > start and not reset and b >= a else None,
                                   counter_reset=bool(reset)) for d,start,end,a,b,reset in reversed(rows)]
            drive['sample_count'] = db.execute('SELECT count(*) FROM samples WHERE id=?', (ident,)).fetchone()[0]
    return report


def warranty(settings, drive, today):
    result = dict(rated_tbw=None, used_pct=None, expires=None, days_left=None)
    if not isinstance(settings, dict):
        raise ValueError('Drive settings must be an object')
    rating = number(settings.get('rated_tbw'))
    if rating is not None and rating > 0:
        result['rated_tbw'] = rating
        written = drive.get('written_tb')
        result['used_pct'] = written / rating * 100 if number(written) is not None else None
    expiry = settings.get('warranty_until')
    if expiry:
        end = dt.date.fromisoformat(expiry)
        result.update(expires=end.isoformat(), days_left=(end - today).days)
    return result


def render(report, config, now=None):
    now = time.time() if now is None else now
    if report.get('schema') != 1 or not isinstance(report.get('drives'), list) or len(report['drives']) > 64:
        raise ValueError('Unsupported report')
    timestamp = number(report.get('collected_at'))
    report['stale'] = timestamp is None or now - timestamp > 7200 or timestamp > now + 300
    mappings = config.get('drives', {})
    if not isinstance(mappings, dict):
        raise ValueError('drives must be an object')
    for drive in report['drives']:
        drive['stale'] = drive.get('present') is False or now - drive.get('last_seen', timestamp or 0) > 7200
        drive['warranty'] = warranty(mappings.get(drive['id'], {}), drive, dt.datetime.fromtimestamp(now, dt.timezone.utc).date())
    report['demo'] = False
    return report


def demo():
    report = dict(schema=1, collected_at=int(time.time()), errors=[], drives=[
        normalize({'model_name': 'Example NVMe SSD 1TB', 'serial_number': 'demo-a',
                   'smart_status': {'passed': True}, 'temperature': {'current': 38},
                   'nvme_smart_health_information_log': {'percentage_used': 12, 'data_units_written': 140625000,
                    'media_errors': 0, 'available_spare': 100, 'critical_warning': 0}}, '/dev/nvme0'),
        normalize({'model_name': 'Example SATA SSD 2TB', 'serial_number': 'demo-b',
                   'smart_status': {'passed': True}, 'temperature': {'current': 42},
                   'endurance_used': {'current': 92}}, '/dev/sda')])
    report['drives'][1]['written_tb'] = 552
    settings = {'drives': {d['id']: {'rated_tbw': 600, 'warranty_until': (dt.date.today() + dt.timedelta(days=430)).isoformat()} for d in report['drives']}}
    report = render(report, settings)
    report['demo'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--save-warranty', metavar='DRIVE_ID')
    parser.add_argument('--warranty-until', default='')
    parser.add_argument('--rated-tbw', default='')
    parser.add_argument('--cache', type=Path, default=CACHE)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'omarchy-drive-health/drives.json')
    args = parser.parse_args()
    if args.save_warranty is not None:
        try:
            if args.collect or args.demo:
                raise SettingsError('invalid_request')
            save_warranty(args.config, args.save_warranty, args.warranty_until, args.rated_tbw)
            result = {'ok': True}
        except SettingsError as exc:
            result = {'ok': False, 'error': str(exc)}
        except OSError:
            result = {'ok': False, 'error': 'write_failed'}
        print(json.dumps(result))
        return
    try:
        if args.collect:
            report = record_history(collect(), args.cache.parent / 'history.sqlite3')
            atomic_write(args.cache, report)
            return
        result = demo() if args.demo else render(read_json(args.cache), read_json(args.config) if args.config.exists() else {})
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if args.collect:
            raise SystemExit('Collection failed: ' + clean(exc))
        result = dict(schema=1, drives=[], errors=[clean(exc)], stale=True, demo=False)
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
