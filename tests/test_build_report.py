import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import build_report as br

TODAY = dt.date(2026, 10, 13)


def fixture():
    return {'series': {'KOC': {'26.09.08': 100, '26.10.08': 120, '26.10.12': 150},
                       'KOC22': {'26.09.08': 200, '26.10.08': 240, '26.10.12': 270},
                       'IKOC22': {'26.09.08': 300, '26.10.08': 330, '26.10.12': 450},
                       'KAU26': {'26.09.08': 200, '26.10.08': 240, '26.10.12': 245},
                       'CORSIA': {'26.06': 17, '26.08': 12.4}},
            'volume': {'KOC': {'26.09.08': 10, '26.10.08': 10, '26.10.12': 0},
                       'KOC22': {'26.09.08': 30, '26.10.08': 30, '26.10.12': 0},
                       'IKOC22': {'26.09.08': 5, '26.10.08': 5, '26.10.12': 0},
                       'KAU26': {'26.09.08': 100, '26.10.08': 100, '26.10.12': 100}},
            'meta': {'CORSIA': {'cur': 'USD', 'auto': False}},
            'status': {'CORSIA': {'mode': 'manual', 'source': 'August futures; original unverified'}},
            'fx': {'USD': 1333}, 'fx_asof': '2026-10-12'}


def items(data):
    return {i['key']: i for i in br.build(data, TODAY)['indicators']}


class ReportTests(unittest.TestCase):
    def test_family_weighting(self):
        i = items(fixture())
        self.assertEqual(i['KOC_TRD']['last'], 210)
        self.assertEqual(i['IKOC_TRD']['last'], 330)
        self.assertEqual(i['KOC_TRD']['volume'], 40)
        self.assertEqual({p['key'] for p in i['KOC_TRD']['weighted_audit']['components']['26.10.08']['members']}, {'KOC', 'KOC22'})

    def test_zero_volume_is_reference(self):
        i = items(fixture())['IKOC22']
        self.assertEqual(i['price_kind'], 'reference_no_trade')
        self.assertAlmostEqual(i['published_price_changes']['직전관측'], 450 / 330 - 1)
        self.assertAlmostEqual(i['changes']['직전관측'], .1)
        self.assertEqual(i['last_trade']['label'], '26.10.08')
        self.assertEqual(items(fixture())['IKOC_TRD']['last_label'], '26.10.08')

    def test_unknown_volume_not_trade(self):
        d = fixture()
        d['volume']['IKOC22'] = {}
        i = items(d)
        self.assertEqual(i['IKOC22']['price_kind'], 'volume_unknown')
        self.assertTrue(all(v is None for v in i['IKOC22']['changes'].values()))
        self.assertNotIn('IKOC_TRD', i)

    def test_zero_only_no_weighted_fallback(self):
        d = fixture()
        d['volume']['IKOC22'] = dict.fromkeys(d['series']['IKOC22'], 0)
        d['series']['IKOC_TRD'] = {'26.10.12': 999}
        self.assertNotIn('IKOC_TRD', items(d))

    def test_stored_weighted_mismatch_audited(self):
        d = fixture()
        d['series']['KOC_TRD'] = {'26.10.08': 999}
        d['volume']['KOC_TRD'] = {'26.10.08': 1}
        i = items(d)['KOC_TRD']
        self.assertEqual(i['last'], 210)
        self.assertIn('26.10.08', i['weighted_audit']['stored_mismatches'])

    def test_rounding_half_won_tolerated(self):
        d = fixture()
        d['series']['KOC_TRD'] = {'26.09.08': 175, '26.10.08': 210}
        d['volume']['KOC_TRD'] = {'26.09.08': 40, '26.10.08': 40}
        self.assertEqual(items(d)['KOC_TRD']['weighted_audit']['stored_mismatches'], [])

    def test_month_precision_and_source(self):
        i = items(fixture())['CORSIA']
        self.assertIsNone(i['last_date'])
        self.assertEqual(i['observation']['period_start'], '2026-08-01')
        self.assertEqual(i['observation']['period_end'], '2026-08-31')
        self.assertEqual(i['last'], 12.4)
        self.assertIn('original unverified', i['source'])
        self.assertTrue(all(v is None for v in i['changes'].values()))

    def test_future_and_incomplete_month(self):
        d = fixture()
        d['series']['CORSIA'].update({'26.10': 999, '26.10.14': 1000})
        i = items(d)['CORSIA']
        self.assertEqual(i['last'], 12.4)
        self.assertEqual(len(i['excluded']), 2)

    def test_four_digit_year_and_leap_month(self):
        self.assertEqual(br.period('2024.02')[1], dt.date(2024, 2, 29))
        self.assertEqual(br.period('2026-10-12')[0], dt.date(2026, 10, 12))

    def test_invalid_values(self):
        pts, excluded = br.observations({'bad': 1, '26.10.01': float('nan'), '26.10.02': float('inf'), '26.10.03': None, '26.10.04': True, '26.10.05': -1}, {}, TODAY)
        self.assertEqual(pts, [])
        self.assertEqual(len(excluded), 6)

    def test_negative_volume_excluded(self):
        d = fixture()
        d['volume']['IKOC22'] = dict.fromkeys(d['series']['IKOC22'], -10)
        self.assertNotIn('IKOC_TRD', items(d))

    def test_no_invented_recent_baseline(self):
        pts, _ = br.observations({'26.08.01': 100, '26.10.12': 200}, {}, TODAY)
        values, bases = br.returns(pts)
        self.assertIsNone(values['1개월'])
        self.assertGreater(bases['1개월']['baseline_lag_days'], 7)

    def test_same_date_reference_ratio_suppressed(self):
        r = br.build(fixture(), TODAY)
        ratio = next(x for x in r['derived'] if x['name'] == 'KOC / KAU26')
        self.assertIsNone(ratio['value'])
        self.assertIsNotNone(ratio['latest_quote_ratio'])

    def test_asynchronous_ratio_and_common_trade(self):
        ratio = next(x for x in br.build(fixture(), TODAY)['derived'] if x['name'] == 'KOC_TRD / KAU26')
        self.assertIsNone(ratio['value'])
        self.assertEqual(ratio['last_common_trade'], {'label': '26.10.08', 'value': 210 / 240})

    def test_matching_trade_ratio(self):
        d = fixture()
        d['series']['KAU26'].pop('26.10.12')
        ratio = next(x for x in br.build(d, TODAY)['derived'] if x['name'] == 'KOC_TRD / KAU26')
        self.assertEqual(ratio['value'], .875)
        self.assertTrue(ratio['comparable'])

    def test_fx_snapshot_not_historical(self):
        i = items(fixture())['CORSIA']
        self.assertEqual(i['last_krw'], 12.4 * 1333)
        self.assertEqual(i['fx_basis'], 'latest_snapshot_not_historical')
        ratio = next(x for x in br.build(fixture(), TODAY)['derived'] if x['name'] == 'CORSIA / KAU26')
        self.assertIsNone(ratio['value'])

    def test_missing_fx(self):
        d = fixture()
        d['fx'] = {}
        self.assertIsNone(items(d)['CORSIA']['last_krw'])

    def test_source_cutoff(self):
        d = fixture()
        d['sources'] = {'CORSIA': {'since': '26.08', 'src': 'fallback'}}
        self.assertEqual(items(d)['CORSIA']['n_points'], 1)

    def test_stopped_status(self):
        d = fixture()
        d['status']['KAU26'] = {'mode': 'stopped', 'note': 'No longer returned'}
        self.assertTrue(any('수집 중단' in x for x in items(d)['KAU26']['alerts']))

    def test_input_not_mutated(self):
        d = fixture()
        before = copy.deepcopy(d)
        br.build(d, TODAY)
        self.assertEqual(d, before)

    def test_cli_deterministic_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'prices.json'
            p.write_text(json.dumps(fixture()), encoding='utf-8')
            before = p.read_bytes()
            cmd = [sys.executable, str(br.ROOT / 'build_report.py'), '--today', str(TODAY), '--input', str(p), '--output-dir', tmp]
            subprocess.run(cmd, check=True, capture_output=True)
            first = (Path(tmp) / 'report_digest.json').read_bytes()
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual(first, (Path(tmp) / 'report_digest.json').read_bytes())
            self.assertEqual(before, p.read_bytes())
            self.assertEqual(json.loads(first)['source_sha256'], hashlib.sha256(before).hexdigest())

    def test_live_v3_regressions(self):
        d = json.loads((br.ROOT / 'tests/fixtures/prices_v3_2026-10-13.json').read_text())
        # Pin assertions to the observed 2026-10-13 snapshot, not later bot updates.
        d['series'] = {k: {l: v for l, v in s.items() if br.period(l)[1] <= TODAY} for k, s in d['series'].items()}
        i = items(d)
        self.assertEqual(i['IKOC22']['observation']['volume'], 0)
        self.assertEqual(i['IKOC22']['last'], 30450)
        self.assertTrue(all(v is None for v in i['IKOC22']['changes'].values()))
        self.assertTrue(all(v is None for v in i['KOC']['changes'].values()))
        self.assertAlmostEqual(i['KOC']['published_price_changes']['1개월'], 28200 / 19350 - 1)
        self.assertAlmostEqual(i['KOC_TRD']['changes']['1개월'], 30000 / (1145517000 / 45000) - 1)
        self.assertEqual(i['KOC_TRD']['weighted_audit']['stored_mismatches'], [])
        self.assertEqual(i['IKOC_TRD']['weighted_audit']['stored_mismatches'], [])
        self.assertEqual(i['CORSIA']['last'], 12.4)


if __name__ == '__main__':
    unittest.main()
