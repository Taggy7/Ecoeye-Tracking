import copy
import datetime as dt
import unittest

import build_report as br
import fetch_prices as fp


def row(month, price, flag='y'):
    return (f'<tr><td>&nbsp;&nbsp; CORSIA ELIGIBLE EMISSIONS</td><td nowrap>{month}</td>'
            f'<td class="right">{price}</td><td>{flag}</td><td></td><td></td></tr>')


def page(rows, stamp='Delayed data retrieved on Nov 4 2026 08:12 ET'):
    head = ('<table><tr><th>Contract (Symbol)</th><th>Month</th><th>Last</th><th></th><th>Change</th></tr>'
            '<tr class="gray"><td>&nbsp;&nbsp; &nbsp; ( <a href="#">CP1 26U-ICE</a> )</td><td>Sep\'26</td><td>--</td></tr>')
    return head + ''.join(rows) + f'<tr><td>{stamp}</td></tr></table>'


ROWS = [row("Nov'26", '12.90'), row("Dec'26", '12.90'), row("Mar'27", '12.90'), row("Dec'27", '12.90'), row("Dec'28", '14.00')]


def base():
    return {'series': {'CORSIA': {'26.06': 17, '26.08': 12.4}, 'KAU26': {'26.11.03': 30000}},
            'volume': {'KAU26': {'26.11.03': 100}},
            'meta': {'CORSIA': {'cur': 'USD', 'auto': False, 'eco': True}, 'KAU26': {'cur': 'KRW', 'cat': 'kr', 'auto': True}},
            'status': {'CORSIA': {'mode': 'manual', 'source': 'blog'}}, 'fx': {'USD': 1345}}


def getter(html):
    return lambda url, headers=None, timeout=30: html


class ParseTest(unittest.TestCase):
    def test_parse_rows(self):
        asof, quotes = fp.parse_esignal(page(ROWS))
        self.assertEqual(asof, dt.date(2026, 11, 4))
        self.assertIn((dt.date(2026, 12, 1), 12.9, 'y'), quotes)
        self.assertEqual(len(quotes), 5)            # Sep'26 '--' 행은 가격이 없어 제외


class FetchCorsiaTest(unittest.TestCase):
    def test_yesterday_settlement_on_previous_weekday(self):
        d = base()
        self.assertTrue(fp.fetch_corsia(d, getter(page(ROWS))))
        self.assertEqual(d['series']['CORSIA']['26.11.03'], 12.9)
        self.assertEqual(d['status']['CORSIA']['contract'], 'Dec26')
        self.assertEqual(d['status']['CORSIA']['mode'], 'auto')
        self.assertTrue(d['meta']['CORSIA']['auto'])
        self.assertEqual(d['sources']['CORSIA']['since'], '26.11.03')

    def test_monday_y_maps_to_friday(self):
        d = base()
        fp.fetch_corsia(d, getter(page(ROWS, 'Delayed data retrieved on Nov 9 2026 08:00 ET')))
        self.assertIn('26.11.06', d['series']['CORSIA'])

    def test_today_settlement(self):
        d = base()
        fp.fetch_corsia(d, getter(page([row("Dec'26", '13.05', 's')], 'Delayed data retrieved on Nov 4 2026 18:30 ET')))
        self.assertEqual(d['series']['CORSIA']['26.11.04'], 13.05)

    def test_since_kept_on_later_runs(self):
        d = base()
        fp.fetch_corsia(d, getter(page(ROWS)))
        fp.fetch_corsia(d, getter(page([row("Dec'26", '13.00', 's')], 'Delayed data retrieved on Nov 5 2026 18:30 ET')))
        self.assertEqual(d['sources']['CORSIA']['since'], '26.11.03')
        self.assertEqual(d['status']['CORSIA']['last'], '26.11.05')

    def test_rolls_to_next_december(self):
        d = base()
        fp.fetch_corsia(d, getter(page([row("Mar'27", '13.10'), row("Dec'27", '13.40')],
                                        'Delayed data retrieved on Jan 5 2027 08:00 ET')))
        self.assertEqual(d['status']['CORSIA']['contract'], 'Dec27')
        self.assertEqual(d['series']['CORSIA']['27.01.04'], 13.4)

    def test_failures_keep_existing_data(self):
        cases = [page(ROWS, stamp='no stamp'),                       # 조회 시각 없음
                 page([row("Mar'27", '12.9')]),                     # 12월물 없음
                 page([row("Dec'26", '12.95', '')]),                # 장중 체결가(정산가 아님)
                 page([row("Dec'26", '5000')]),                     # 범위 밖
                 '<html>blocked</html>']
        for html in cases:
            d = base()
            before = copy.deepcopy(d)
            self.assertFalse(fp.fetch_corsia(d, getter(html)))
            self.assertEqual(d, before)

    def test_network_error_keeps_data(self):
        def boom(url, headers=None, timeout=30):
            raise OSError('403')
        d = base()
        before = copy.deepcopy(d)
        self.assertFalse(fp.fetch_corsia(d, boom))
        self.assertEqual(d, before)

    def test_report_compares_only_new_source(self):
        d = base()
        fp.fetch_corsia(d, getter(page(ROWS)))
        rep = br.build(d, dt.date(2026, 11, 4))
        i = next(x for x in rep['indicators'] if x['key'] == 'CORSIA')
        self.assertTrue(i['auto'])
        self.assertEqual(i['n_points'], 1)          # 17·12.4 수동 대표값은 비교에서 제외
        self.assertEqual(i['last'], 12.9)
        self.assertFalse(any('관측별 출처' in a for a in i['alerts']))


if __name__ == '__main__':
    unittest.main()
