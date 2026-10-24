import copy
import datetime as dt
import json
import unittest

import build_report as br
import fetch_prices as fp

PAGE = '<script>var cfg = {"hubId": 12345, "productId": 83046673};</script>'
CONTRACTS = [{"marketStrip": "Dec26", "lastPrice": 21.4, "lastTime": "10/22/2026 4:15 PM", "marketId": 999},
             {"marketStrip": "Dec27", "lastPrice": 23.0}]


def base():
    return {'series': {'CORSIA': {'26.06': 17, '26.08': 12.4}, 'KAU26': {'26.10.22': 29550}},
            'volume': {'KAU26': {'26.10.22': 100}},
            'meta': {'CORSIA': {'cur': 'USD', 'auto': False, 'eco': True}, 'KAU26': {'cur': 'KRW', 'cat': 'kr', 'auto': True}},
            'status': {'CORSIA': {'mode': 'manual', 'source': 'blog'}}, 'fx': {'USD': 1400}}


def getter(page=PAGE, contracts=CONTRACTS, fail_api=False):
    def get(url, headers=None, timeout=30):
        if 'contract-data' in url or 'getContractsAsJson' in url:
            if fail_api:
                raise OSError('blocked')
            return json.dumps(contracts)
        return page
    return get


class FetchCorsiaTest(unittest.TestCase):
    def test_success_records_auto_value(self):
        d = base()
        self.assertTrue(fp.fetch_corsia(d, getter()))
        self.assertEqual(d['series']['CORSIA']['26.10.22'], 21.4)
        self.assertTrue(d['meta']['CORSIA']['auto'])
        self.assertEqual(d['status']['CORSIA']['mode'], 'auto')
        self.assertEqual(d['status']['CORSIA']['contract'], 'Dec26')
        self.assertEqual(d['sources']['CORSIA']['since'], '26.10.22')

    def test_since_kept_on_later_runs(self):
        d = base()
        fp.fetch_corsia(d, getter())
        later = [dict(CONTRACTS[0], lastPrice=22.0, lastTime='10/23/2026 4:15 PM')]
        fp.fetch_corsia(d, getter(contracts=later))
        self.assertEqual(d['sources']['CORSIA']['since'], '26.10.22')
        self.assertEqual(d['status']['CORSIA']['last'], '26.10.23')

    def test_failures_keep_existing_data(self):
        cases = [getter(page='no hub here'), getter(fail_api=True), getter(contracts=[]),
                 getter(contracts=[{"lastPrice": 5000}]), getter(contracts=[{"marketStrip": "Dec26"}])]
        for g in cases:
            d = base()
            before = copy.deepcopy(d)
            self.assertFalse(fp.fetch_corsia(d, g))
            self.assertEqual(d, before)

    def test_report_compares_only_ice_values(self):
        d = base()
        fp.fetch_corsia(d, getter())
        rep = br.build(d, dt.date(2026, 10, 24))
        i = next(x for x in rep['indicators'] if x['key'] == 'CORSIA')
        self.assertTrue(i['auto'])
        self.assertEqual(i['n_points'], 1)          # 17·12.4 수동 대표값은 비교에서 제외
        self.assertEqual(i['last'], 21.4)
        self.assertFalse(any('관측별 출처' in a for a in i['alerts']))


if __name__ == '__main__':
    unittest.main()
