"""Independent-station splitting, search and captured Basket provenance."""
from contextlib import ExitStack
import sqlite3
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from app import create_app
from backend.session_store import DATA_STORE
from backend.routes import data_transform
from backend.routes.basket_routes import _merged_item_rows
from backend.db import init_db, add_basket_items, get_basket_items
from backend.segment_search import split_into_segments


class SourceBoundaryContracts(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()
        self.sid = 'source-test'
        self.times = pd.date_range('2026-01-01', periods=12, freq='10min').astype(str).tolist()
        DATA_STORE[self.sid] = {
            'values': np.array(list(range(12)) + [np.nan] + list(range(100,112)), dtype=float),
            'labels': self.times + [''] + self.times,
            'source_spans': [
                {'start_idx': 0, 'end_idx': 12, 'source_key': 'A', 'source_label': 'A station',
                 'parent_segment_key': 'a0', 'timestamps': self.times},
                {'start_idx': 13, 'end_idx': 25, 'source_key': 'B', 'source_label': 'B station',
                 'parent_segment_key': 'b0', 'timestamps': self.times}],
            'gap_ranges': [[12,12]], 'dataset_key': 'stations',
        }

    def tearDown(self):
        DATA_STORE.pop(self.sid, None)

    def split(self, **options):
        return self.client.post('/api/split_continuous/'+self.sid, json=options)

    def test_same_hour_at_two_stations_never_merges(self):
        r=self.split(seg_len=6, boundary_minutes=60)
        self.assertEqual(r.status_code,200,r.get_json())
        segs=r.get_json()['segments']
        self.assertEqual([s['source_key'] for s in segs],['A','A','B','B'])
        self.assertEqual([s['length'] for s in segs],[6]*4)
        self.assertEqual(segs[2]['values'],list(range(100,106)))
        self.assertEqual(segs[2]['timestamps'],self.times[:6])
        self.assertEqual(len({s['key'] for s in segs}),4)
        self.assertIn('B station',segs[2]['label'])

    def test_missing_point_discards_whole_hour(self):
        DATA_STORE[self.sid]['values'][2]=np.nan
        segs=self.split(seg_len=6,boundary_minutes=60).get_json()['segments']
        self.assertEqual([(s['source_key'],s['start']) for s in segs],
                         [('A',self.times[6]),('B',self.times[0]),('B',self.times[6])])

    def test_complementary_partial_stations_are_not_combined(self):
        entry=DATA_STORE[self.sid]
        entry['values'][:3]=np.nan
        entry['values'][16:19]=np.nan
        segs=self.split(seg_len=6,boundary_minutes=60).get_json()['segments']
        self.assertEqual([(s['source_key'],s['start']) for s in segs],
                         [('A',self.times[6]),('B',self.times[6])])

    def test_fixed_split_restarts_at_each_source_and_discards_tails(self):
        segs=self.split(seg_len=5,stride=5).get_json()['segments']
        self.assertEqual([s['values'][0] for s in segs],[0,5,100,105])
        self.assertTrue(all(s['length']==5 for s in segs))

    def test_same_station_windows_keep_distinct_overlay_values(self):
        segments = [{'label': str(i), 'source_key': 'A', 'values': [1, 2, 3],
                     'timestamps': self.times[i:i+3],
                     'overlays': [{'col_name': 'y', 'name': 'Display Y', 'values': vals}]}
                    for i, vals in enumerate(([1, 2, 3], [30, 20, 10]))]
        response = self.client.post('/api/search_segments/stations/x', json={
            'segments': segments, 'query_index': 0, 'overlay_cols': ['y'],
            'normalize': False, 'method': 'euclidean'})
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()['results'][0]
        self.assertEqual(len(result['col_similarities']), 2)
        self.assertLess(result['col_similarities'][1], result['col_similarities'][0])

    def test_nonfinite_values_never_enter_fixed_windows(self):
        DATA_STORE[self.sid]['values'][2] = np.inf
        segs = self.split(seg_len=5, stride=5).get_json()['segments']
        self.assertTrue(all(np.isfinite(s['values']).all() for s in segs))

    def test_hourly_observations_allow_single_point_hours(self):
        times = pd.date_range('2026-01-01', periods=3, freq='h').astype(str).tolist()
        segs = split_into_segments(times, [1, 2, 3], 'hour', 'A', require_complete=True)
        self.assertEqual(len(segs), 3)
        self.assertTrue(all(len(s['values']) == 1 for s in segs))
        entry = DATA_STORE[self.sid]
        entry['values'] = np.array([1., 2., 3.])
        entry['labels'] = times
        entry['source_spans'] = [dict(entry['source_spans'][0], end_idx=3, timestamps=times)]
        response = self.split(seg_len=1, boundary_minutes=60)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['count'], 3)


    def test_invalid_split_size_does_not_loop(self):
        for body in ({'seg_len':0},{'stride':-1},{'boundary_minutes':'bad'}):
            self.assertEqual(self.split(**body).status_code,400)

    def test_search_windows_and_selected_query_cannot_cross_sources(self):
        r=self.client.post('/api/search/'+self.sid,json={
            'query_values':[0,1,2,3,4], 'stride':5,'top_k':20})
        self.assertEqual(r.status_code,200,r.get_json())
        results=r.get_json()['results']
        self.assertEqual({r['source_key'] for r in results},{'A','B'})
        self.assertTrue(all(len(r['timestamps'])==5 for r in results))
        self.assertTrue(any(r['start']==13 for r in results))
        bad=self.client.post('/api/search/'+self.sid,json={'query_start':10,'query_end':15})
        self.assertEqual(bad.status_code,400)

    def test_direct_collection_split_also_discards_partial_and_missing(self):
        values=list(range(12));values[2]=None
        segs=split_into_segments(self.times,values,'hour','A',require_complete=True)
        self.assertEqual(len(segs),1)
        self.assertEqual(segs[0]['start_ts'],self.times[6])

    def test_duplicates_do_not_replace_missing_timestamps(self):
        times=list(self.times);times[2]=times[1]
        segs=split_into_segments(times,list(range(12)),'hour','A',require_complete=True)
        self.assertEqual(len(segs),1)

    def test_basket_retains_two_stations_with_identical_times(self):
        segs=self.split(seg_len=6,boundary_minutes=60).get_json()['segments']
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row;init_db(conn)
        self.addCleanup(conn.close)
        items=[{'dataset_key':'stations','source_key':s['source_key'],'col_name':'x',
                'start_ts':s['start'],'end_ts':s['end'],'point_count':s['length'],
                'values':s['values'],'series':[{'col_name':'x','values':s['values'],
                                               'timestamps':s['timestamps']}]} for s in (segs[0],segs[2])]
        add_basket_items(conn,items)
        stored=get_basket_items(conn,include_values=True)
        self.assertEqual(len(stored),2)
        self.assertEqual({i['source_key'] for i in stored},{'A','B'})
        with patch('backend.routes.basket_routes.get_all_columns_for_segment',return_value=[]):
            for item in stored:
                rows=_merged_item_rows(conn,item)
                self.assertEqual([r['timestamp'] for r in rows],self.times[:6])
                self.assertEqual(rows[0]['value'],0 if item['source_key']=='A' else 100)

    def test_concat_aligns_overlays_by_source_and_timestamp(self):
        primary=[{'source_key':'A','description':'A station','timestamps':self.times,'values':list(range(12))},
                 {'source_key':'B','description':'B station','timestamps':self.times,'values':list(range(100,112))}]
        overlay=[{'source_key':'B','timestamps':self.times,'values':list(range(200,212))}]
        with ExitStack() as stack:
            for name,value in [('get_db',Mock()),('get_dataset',{'id':1,'type':'collection','index_type':'datetime'}),
                               ('get_column_info',{'label':'x','unit':''}),('has_preprocessed_data',False)]:
                stack.enter_context(patch.object(data_transform,name,return_value=value))
            stack.enter_context(patch.object(data_transform,'get_ts_column_all_sources',side_effect=[primary,overlay]))
            r=self.client.get('/api/concat_segments/stations/x?overlay_cols=y&gap_size=1')
        self.assertEqual(r.status_code,200,r.get_json())
        d=r.get_json();self.addCleanup(DATA_STORE.pop,d['sid'],None)
        self.assertEqual([s['source_key'] for s in d['source_spans']],['A','B'])
        self.assertEqual(d['overlays'][0]['values'][:13],[None]*13)
        self.assertEqual(d['overlays'][0]['values'][13:],list(range(200,212)))


if __name__=='__main__': unittest.main()
