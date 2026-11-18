import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('health', ROOT / 'backend/drive_health.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class HealthTests(unittest.TestCase):
    def nvme(self, **fields):
        return {'model_name': 'Generic NVMe', 'serial_number': 'secret-serial',
                'smart_status': {'passed': True},
                'nvme_smart_health_information_log': dict(percentage_used=12,
                    data_units_written=1953125, critical_warning=0, media_errors=0, **fields)}

    def test_nvme_units_and_no_serial_exposure(self):
        d = h.normalize(self.nvme(), '/dev/nvme0')
        self.assertEqual(d['written_tb'], 1)
        self.assertEqual(d['wear_used_pct'], 12)
        self.assertEqual(d['status'], 'ok')
        self.assertNotIn('secret-serial', json.dumps(d))

    def test_id_survives_device_renaming(self):
        self.assertEqual(h.normalize(self.nvme(), '/dev/nvme0')['id'], h.normalize(self.nvme(), '/dev/nvme1')['id'])

    def test_wear_over_100_not_failure(self):
        raw = self.nvme()
        raw['nvme_smart_health_information_log']['percentage_used'] = 117
        d = h.normalize(raw, '/dev/nvme0')
        self.assertEqual(d['wear_used_pct'], 117)
        self.assertEqual(d['status'], 'warning')

    def test_critical_and_nonzero_smart_exit_preserve_data(self):
        d = h.normalize(self.nvme(), '/dev/nvme0', 8)
        self.assertEqual(d['status'], 'critical')
        self.assertEqual(d['written_tb'], 1)
        self.assertEqual(h.normalize(self.nvme(), '/dev/nvme0', 2)['status'], 'warning')

    def test_missing_not_healthy_zero(self):
        d = h.normalize({}, '/dev/sda', 2)
        self.assertIsNone(d['written_tb'])
        self.assertIsNone(d['wear_used_pct'])
        self.assertNotEqual(d['status'], 'ok')

    def test_vendor_units_not_assumed(self):
        raw = {'model_name': 'Other vendor SSD', 'ata_smart_attributes': {'table': [
            {'id': 241, 'raw': {'value': 1953125000}}, {'id': 177, 'value': 95}]}}
        d = h.normalize(raw, '/dev/sda')
        self.assertIsNone(d['written_tb'])
        self.assertIsNone(d['wear_used_pct'])
        raw['model_name'] = 'Samsung SSD 860 QVO 1TB'
        d = h.normalize(raw, '/dev/sda')
        self.assertEqual(d['written_tb'], 1)
        self.assertEqual(d['wear_used_pct'], 5)

    def test_nonfinite_and_boolean_values_rejected(self):
        for value in (True, float('nan'), float('inf'), -1, '50'):
            self.assertIsNone(h.number(value))

    def test_stale_and_future_cache(self):
        report = {'schema': 1, 'collected_at': 10000, 'drives': []}
        self.assertTrue(h.render(copy.deepcopy(report), {}, 20000)['stale'])
        self.assertTrue(h.render(copy.deepcopy(report), {}, 100)['stale'])
        self.assertFalse(h.render(copy.deepcopy(report), {}, 10001)['stale'])

    def test_warranty_expiry_and_limit_separate(self):
        w = h.warranty({'rated_tbw': 100, 'warranty_until': '2024-02-29'}, {'written_tb': 125}, dt.date(2024, 3, 1))
        self.assertEqual(w['days_left'], -1)
        self.assertEqual(w['used_pct'], 125)
        self.assertIsNone(h.warranty({}, {}, dt.date.today())['days_left'])
        with self.assertRaises(ValueError):
            h.warranty({'warranty_until': 'invalid'}, {}, dt.date.today())

    def test_discovery_filters_hdd_usb_and_unsafe_paths(self):
        devices = [{'name': '/dev/sda', 'type': 'sat'}, {'name': '/dev/sdb', 'type': 'sat'},
                   {'name': '/dev/nvme0', 'type': 'nvme'}, {'name': '/dev/sdc', 'type': 'usbjmicron'},
                   {'name': '/dev/../tmp/x', 'type': 'sat'}]
        replies = [({'devices': devices}, 0), ({'rotation_rate': 7200}, 0),
                   ({'rotation_rate': 0, 'model_name': 'Other SSD'}, 0), (self.nvme(), 0)]
        with patch.object(h, 'smart', side_effect=replies) as runner:
            result = h.collect()
        self.assertEqual(len(result['drives']), 2)
        self.assertEqual(runner.call_count, 4)

    def test_missing_tool_is_reported(self):
        with patch.object(h, 'smart', side_effect=FileNotFoundError('smartctl missing')):
            self.assertTrue(h.collect()['errors'])

    def test_linux_scsi_scan_entry_autodetects_sata(self):
        scan = {'devices': [{'name': '/dev/sda', 'type': 'scsi'}]}
        sata = {'rotation_rate': 0, 'model_name': 'Samsung SSD 860 QVO 1TB'}
        with patch.object(h, 'smart', side_effect=[(scan, 0), (sata, 0)]) as runner:
            result = h.collect()
        self.assertEqual(len(result['drives']), 1)
        self.assertEqual(runner.call_args.args[0][-2:], ['auto', '/dev/sda'])

    def test_atomic_report_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            h.atomic_write(path, {'schema': 1})
            self.assertEqual(h.read_json(path), {'schema': 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            path.write_bytes(b'x' * (h.LIMIT + 1))
            with self.assertRaises(ValueError):
                h.read_json(path)

    def test_markup_stays_plain_in_ui(self):
        raw = self.nvme()
        raw['model_name'] = '<img src="https://example.invalid/tracker">'
        self.assertIn('<img', h.normalize(raw, '/dev/nvme0')['model'])
        qml = (ROOT / 'DriveList.qml').read_text()
        self.assertIn('component Label: Text', qml)
        self.assertIn('textFormat: Text.PlainText', qml)
        # All dynamic display values go through Label; no rich-text shared sinks.
        self.assertEqual(qml.count(': Text {'), 1)
        self.assertEqual([line.strip() for line in qml.splitlines() if 'tooltipText:' in line],
                         ['tooltipText: strings.t("settingsTitle")'])
        editor = (ROOT / 'WarrantyEditor.qml').read_text()
        self.assertIn('textFormat: Text.PlainText', editor)
        self.assertNotIn('tooltipText:', editor)
        self.assertIn('tooltipText: strings.t("title")', (ROOT / 'BarWidget.qml').read_text())

    def test_demo_explicit(self):
        result = h.demo()
        self.assertTrue(result['demo'])
        self.assertEqual(len(result['drives']), 2)

    def history_report(self, timestamp, tb=1, present=True):
        drive = h.normalize(self.nvme(), '/dev/nvme0')
        drive['written_tb'] = tb
        return {'schema': 1, 'collected_at': timestamp, 'errors': [], 'drives': [drive] if present else []}

    def test_enroll_hourly_dedup_and_daily_observed_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'history.sqlite3'
            start = 86400 * 20000
            first = h.record_history(self.history_report(start), db)['drives'][0]
            self.assertEqual(first['first_seen'], start)
            self.assertIsNone(first['daily'][0]['written_gb'])
            repeated = h.record_history(self.history_report(start + 300, 1.002), db)['drives'][0]
            self.assertEqual(repeated['sample_count'], 1)
            next_hour = h.record_history(self.history_report(start + 3600, 1.01), db)['drives'][0]
            self.assertEqual(next_hour['sample_count'], 2)
            self.assertAlmostEqual(next_hour['daily'][0]['written_gb'], 10)
            self.assertEqual(next_hour['daily'][0]['coverage_hours'], 1)
            self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_unplug_replug_preserves_identity_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'history.sqlite3'
            h.record_history(self.history_report(10000), db)
            absent = h.record_history(self.history_report(14000, present=False), db)
            self.assertFalse(absent['drives'][0]['present'])
            self.assertEqual(absent['drives'][0]['last_seen'], 10000)
            self.assertTrue(h.render(absent, {}, 14000)['drives'][0]['stale'])
            returned = h.record_history(self.history_report(18000), db)['drives'][0]
            self.assertTrue(returned['present'])
            self.assertEqual(returned['first_seen'], 10000)
            self.assertEqual(returned['sample_count'], 2)

    def test_midnight_gap_and_counter_reset_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'history.sqlite3'
            start = 86400 * 20000
            h.record_history(self.history_report(start, 3), db)
            next_day = h.record_history(self.history_report(start + 86400, 4), db)['drives'][0]
            self.assertIsNone(next_day['daily'][-1]['written_gb'])
            reset = h.record_history(self.history_report(start + 90000, 1), db)['drives'][0]
            self.assertTrue(reset['daily'][-1]['counter_reset'])
            self.assertIsNone(reset['daily'][-1]['written_gb'])

    def test_retention_keeps_registry_and_daily_summaries(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'history.sqlite3'
            h.record_history(self.history_report(10000), db)
            later = h.record_history(self.history_report(10000 + 91 * 86400), db)['drives'][0]
            self.assertEqual(later['sample_count'], 1)
            self.assertEqual(later['first_seen'], 10000)
            self.assertEqual(len(later['daily']), 2)

    def test_failed_reads_do_not_enroll_or_create_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'history.sqlite3'
            report = self.history_report(10000)
            report['drives'][0]['issues'] = ['read_incomplete']
            result = h.record_history(report, db)
            self.assertEqual(result['enrolled_count'], 0)

    def counter_drive(self, count, critical=0):
        raw = self.nvme()
        raw['nvme_smart_health_information_log'].update(media_errors=count, critical_warning=critical)
        return h.normalize(raw, '/dev/nvme0')

    def test_error_baseline_increase_unchanged_are_evidence_only(self):
        first = h.explain_health(self.counter_drive(3), None, None, 100)
        baseline = first['health_details'][0]
        self.assertEqual(baseline['observation'], 'baseline')
        self.assertIsNone(baseline['delta'])
        increased = h.explain_health(self.counter_drive(4), first, 100, 200)
        self.assertEqual(increased['health_details'][0]['delta'], 1)
        self.assertEqual(increased['health_details'][0]['comparison_at'], 100)
        unchanged = h.explain_health(self.counter_drive(4), increased, 200, 300)
        self.assertEqual(unchanged['health_details'][0]['observation'], 'unchanged')
        self.assertEqual(unchanged['health_details'][0]['last_increase_at'], 200)
        self.assertEqual(unchanged['status'], 'warning')
        self.assertEqual(first['counter_observations']['media_errors']['value'], 3)

    def test_counter_decrease_or_clock_rollback_never_invents_delta(self):
        old = h.explain_health(self.counter_drive(5), None, None, 100)
        reduced = h.explain_health(self.counter_drive(1), old, 100, 200)
        detail = reduced['health_details'][0]
        self.assertEqual(detail['observation'], 'decreased')
        self.assertIsNone(detail['delta'])
        self.assertEqual(detail['baseline_at'], 200)
        self.assertIsNone(detail['last_increase_at'])
        backwards = h.explain_health(self.counter_drive(8), old, 100, 50)
        self.assertEqual(backwards['health_details'][0]['observation'], 'baseline')
        self.assertIsNone(backwards['health_details'][0]['delta'])

    def test_missing_counter_does_not_become_zero(self):
        old = h.explain_health(self.counter_drive(3), None, None, 100)
        missing = self.counter_drive(0)
        missing['error_counters'] = {}
        missing['media_errors'] = None
        middle = h.explain_health(missing, old, 100, 200)
        self.assertEqual(middle['health_details'], [])
        later = h.explain_health(self.counter_drive(4), middle, 200, 300)
        self.assertEqual(later['health_details'][0]['comparison_at'], 100)
        self.assertEqual(later['health_details'][0]['delta'], 1)

    def test_legacy_media_counter_migrates_without_fabricated_baseline(self):
        old = self.counter_drive(3)
        del old['error_counters']
        new = h.explain_health(self.counter_drive(4), old, 100, 200)
        self.assertEqual(new['health_details'][0]['delta'], 1)
        self.assertEqual(new['health_details'][0]['comparison_at'], 100)

    def test_current_critical_warning_never_downgraded(self):
        old = h.explain_health(self.counter_drive(3, 3), None, None, 100)
        new = h.explain_health(self.counter_drive(3, 3), old, 100, 200)
        self.assertEqual(new['status'], 'critical')
        flags = {d['code']: d for d in new['health_details']}
        self.assertEqual(flags['nvme_spare']['first_observed_at'], 100)
        self.assertEqual(flags['nvme_temperature']['severity'], 'critical')
        self.assertEqual(flags['media_errors']['observation'], 'unchanged')

    def test_flag_recurrence_starts_new_observation_episode(self):
        old = h.explain_health(self.counter_drive(0, 4), None, None, 100)
        clear = h.explain_health(self.counter_drive(0), old, 100, 200)
        again = h.explain_health(self.counter_drive(0, 4), clear, 200, 300)
        self.assertEqual(again['health_details'][0]['first_observed_at'], 300)

    def test_current_threshold_not_mislabelled_as_history(self):
        drive = h.normalize({}, '/dev/sda', 16 | 32 | 64 | 128)
        self.assertEqual(set(drive['issues']), {'ata_threshold', 'ata_past_threshold', 'error_log', 'selftest_log'})

    def test_ata_gauges_are_counter_differences_not_new_errors(self):
        def sata(count):
            return h.normalize({'model_name': 'Generic SATA', 'smart_status': {'passed': True},
                'ata_smart_attributes': {'table': [{'id': 197, 'name': 'Current_Pending_Sector', 'raw': {'value': count}}]}}, '/dev/sda')
        first = h.explain_health(sata(3), None, None, 100)
        new = h.explain_health(sata(4), first, 100, 200)
        self.assertEqual(new['health_details'][0]['code'], 'ata_197')
        self.assertEqual(new['health_details'][0]['delta'], 1)
        self.assertEqual(h.explain_health(sata(4), new, 200, 300)['status'], 'warning')

    def test_explanations_survive_database_reopen_and_same_hour(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'history.sqlite3'
            def sample(at, count):
                return {'schema': 1, 'collected_at': at, 'errors': [], 'drives': [self.counter_drive(count)]}
            h.record_history(sample(10000, 3), db)
            changed = h.record_history(sample(10060, 4), db)['drives'][0]
            self.assertEqual(changed['health_details'][0]['delta'], 1)
            steady = h.record_history(sample(10120, 4), db)['drives'][0]
            self.assertEqual(steady['health_details'][0]['last_increase_at'], 10060)
            self.assertEqual(steady['sample_count'], 1)

    def test_settings_save_clear_and_preserve_other_drives(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config/drives.json'
            first, second = 'a' * 24, 'b' * 24
            h.save_warranty(path, first, '2030-02-28', '600,5')
            h.save_warranty(path, second, '', '100')
            config = h.read_json(path)
            self.assertEqual(config['drives'][first], {'warranty_until': '2030-02-28', 'rated_tbw': 600.5})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            h.save_warranty(path, first, '', '')
            self.assertEqual(h.read_json(path)['drives'], {second: {'rated_tbw': 100}})

    def test_invalid_settings_never_modify_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drives.json'
            h.save_warranty(path, 'a' * 24, '2030-02-28', '600')
            original = path.read_bytes()
            for date, tbw, error in [('2030-02-30', '600', 'invalid_date'),
                                     ('2030-2-1', '', 'invalid_date'),
                                     ('', '-1', 'invalid_tbw'), ('', 'NaN', 'invalid_tbw'),
                                     ('', '0', 'invalid_tbw'), ('', '1e400', 'invalid_tbw'),
                                     ('', '1000000001', 'invalid_tbw')]:
                with self.assertRaisesRegex(h.SettingsError, error):
                    h.save_warranty(path, 'a' * 24, date, tbw)
                self.assertEqual(path.read_bytes(), original)

    def test_malformed_settings_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drives.json'
            for content in ['bad json', '{"drives": []}', '{"drives": {"' + 'a' * 24 + '": 5}}']:
                path.write_text(content)
                with self.assertRaisesRegex(h.SettingsError, 'invalid_config'):
                    h.save_warranty(path, 'a' * 24, '', '600')
                self.assertEqual(path.read_text(), content)

    def test_settings_preserve_unknown_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drives.json'
            path.write_text(json.dumps({'other': 'keep', 'drives': {'a' * 24: {'note': 'keep'}}}))
            h.save_warranty(path, 'a' * 24, '', '600')
            value = h.read_json(path)
            self.assertEqual(value['other'], 'keep')
            self.assertEqual(value['drives']['a' * 24]['note'], 'keep')

    def test_concurrent_settings_saves_merge_different_drives(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drives.json'
            processes = [subprocess.Popen([sys.executable, '-I', str(ROOT / 'backend/drive_health.py'),
                '--config', str(path), '--save-warranty', char * 24, '--rated-tbw', '600'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for char in 'abcdef']
            for process in processes:
                output, error = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, error)
                self.assertTrue(json.loads(output)['ok'])
            self.assertEqual(len(h.read_json(path)['drives']), 6)

    def test_saved_warranty_immediately_changes_rendered_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'drives.json'
            report = self.history_report(10000, 1)
            h.save_warranty(path, report['drives'][0]['id'], '2030-06-15', '100')
            rendered = h.render(report, h.read_json(path), now=10000)
            self.assertEqual(rendered['drives'][0]['warranty']['used_pct'], 1)
            self.assertEqual(rendered['drives'][0]['warranty']['expires'], '2030-06-15')


if __name__ == '__main__':
    unittest.main()
