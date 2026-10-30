"""Reproductions from the handoff code review; all writes use temporary stores."""
import importlib.util
import io
import json
from pathlib import Path
import signal
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from app import create_app
from backend import db, app_config, preprocess_service
from backend.helpers import handle_nan, compute_distance
from backend.segment_search import get_preset_shape
from backend.segment_cluster import cluster_segments
from backend.clustering.base import ClusterResult
from backend.startup import ModelServers, ready


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'test.db'
        self.patches = []
        for target, value in [('backend.db.DB_PATH', self.path),
                              ('backend.db.DATA_DIR', self.root / 'uploads'),
                              ('backend.db._db_initialized', False),
                              ('backend.app_config._CUSTOM_PRESETS_DIR', self.root / 'presets'),
                              ('backend.app_config._custom_presets_cache', {}),
                              ('backend.app_config._hidden_presets_cache', {})]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        self.client = create_app().test_client()

    def seed(self):
        conn = db.get_db()
        self.addCleanup(conn.close)
        did = db.insert_dataset(conn, 'sample')
        for col in ['x', 'y']:
            db.insert_dataset_column(conn, did, col, col)
        sid = db.insert_source(conn, did, 'sample/source', 'time')
        for i in range(4):
            db.insert_ts_bulk(conn, sid, f'2026-01-01 0{i}:00:00', {'x':i,'y':i})
        db.insert_ts_preprocessed_bulk(conn, sid, [('old', 'x', 999)])
        db.save_preprocess_config(conn, did, '{}', '{"old":true}')
        return conn, did, sid

    def test_preprocess_failure_preserves_previous_data_and_summary(self):
        conn, did, sid = self.seed()
        with patch.object(preprocess_service, '_preprocess_timeseries', side_effect=[
            (['new'], np.array([42.]), {}), RuntimeError('second column failed')]):
            result = preprocess_service.execute_preprocessing('sample', {})
        self.assertIn('error', result)
        self.assertEqual([tuple(r) for r in conn.execute('SELECT timestamp,col_name,value FROM ts_data_preprocessed')], [('old','x',999.)])
        self.assertEqual(json.loads(db.get_preprocess_config(conn,did)['summary']), {'old':True})
        self.assertEqual(conn.execute('SELECT count(*) FROM ts_data').fetchone()[0],8)

    def test_preprocess_success_replaces_whole_result(self):
        conn, did, sid = self.seed()
        with patch.object(preprocess_service, '_preprocess_timeseries', return_value=(['new'],np.array([42.]),{})):
            result = preprocess_service.execute_preprocessing('sample', {})
        self.assertTrue(result['ok'])
        self.assertEqual(conn.execute('SELECT count(*) FROM ts_data_preprocessed').fetchone()[0],2)
        self.assertNotIn('old', db.get_preprocess_config(conn,did)['summary'])

    def test_delete_commit_failure_keeps_original_bytes(self):
        conn,did,sid=self.seed()
        rel=db.save_uploaded_file(conn,did,'sample','original.csv',b'original')
        conn.commit()
        proxy=Mock(wraps=conn);proxy.commit.side_effect=sqlite3.OperationalError('disk full')
        with self.assertRaises(sqlite3.OperationalError):db.delete_dataset(proxy,'sample')
        conn.rollback()
        self.assertTrue(db.get_dataset(conn,'sample'))
        self.assertEqual((db.DATA_DIR/rel).read_bytes(),b'original')

    def test_delete_cleanup_failure_does_not_rollback_database(self):
        conn,did,sid=self.seed()
        db.save_uploaded_file(conn,did,'sample','original.csv',b'original');conn.commit()
        with patch('pathlib.Path.unlink',side_effect=PermissionError('readonly')):
            self.assertTrue(db.delete_dataset(conn,'sample'))
        self.assertIsNone(db.get_dataset(conn,'sample'))

    def test_request_closes_unclosed_connection_and_rolls_back(self):
        app=create_app();connections=[]
        @app.get('/api/test-review-rollback')
        def fail():
            conn=db.get_db();connections.append(conn)
            conn.execute("INSERT INTO datasets(key) VALUES ('unfinished')")
            raise RuntimeError('abort')
        self.assertEqual(app.test_client().get('/api/test-review-rollback').status_code,500)
        with self.assertRaises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')
        conn=db.get_db();self.addCleanup(conn.close)
        self.assertIsNone(db.get_dataset(conn,'unfinished'))

    def test_non_object_json_rejected_before_api_handler(self):
        for url in ['/api/import_labels','/api/preprocess/execute','/api/basket','/api/generate_pattern','/api/presets/custom']:
            for body in ['null','[]','"text"','5']:
                r=self.client.post(url,data=body,content_type='application/json')
                self.assertEqual(r.status_code,400,(url,body,r.json))

    def test_preset_aliases_do_not_share_files(self):
        for key in ['a.b','a b','a_b','x_hidden']:
            app_config.save_custom_preset('test',{'values':[0,1,0],'label':key},key)
        app_config.hide_preset('rise','x')
        app_config._custom_presets_cache.clear()
        for key in ['a.b','a b','a_b','x_hidden']:
            self.assertEqual(app_config.cfg_custom_presets(key)['test']['label'],key)
        self.assertEqual(app_config.load_hidden_presets('x'),{'rise'})

    def test_preset_cache_cannot_be_mutated_by_reader(self):
        app_config.save_custom_preset('test',{'values':[0,1,0]},'sample')
        copied=app_config.cfg_custom_presets('sample');copied['test']['values'][0]=99
        self.assertEqual(app_config.cfg_custom_presets('sample')['test']['values'],[0,1,0])
        app_config.hide_preset('rise','sample')
        app_config.load_hidden_presets('sample').clear()
        self.assertEqual(app_config.load_hidden_presets('sample'),{'rise'})

    def test_preset_concurrent_saves_keep_both_updates(self):
        import time
        write=app_config._write_yaml
        def slow_write(*args):time.sleep(.01);write(*args)
        with patch.object(app_config,'_write_yaml',side_effect=slow_write):
            threads=[threading.Thread(target=app_config.save_custom_preset,args=(str(i),{'values':[0,1,0]},'sample')) for i in range(8)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
        app_config._custom_presets_cache.clear()
        self.assertEqual(len(app_config.cfg_custom_presets('sample')),8)

    def test_invalid_preset_shapes_do_not_write(self):
        for values in ([[0,1],[1,2],[2,3]], [True,False,True], [0,float('inf'),2]):
            r=self.client.post('/api/presets/custom',json={'dataset_id':'sample','name':'p','values':values})
            self.assertEqual(r.status_code,400,r.json)
        self.assertFalse(list((self.root/'presets').rglob('*.yaml')))

    def test_custom_shape_is_normalized_after_resampling(self):
        app_config.save_custom_preset('ramp',{'values':[10,20,30]},'sample')
        shape=get_preset_shape('custom_ramp',7,'sample')
        self.assertAlmostEqual(float(shape.mean()),0)
        self.assertAlmostEqual(float(shape.std()),1)

    def test_infinite_values_do_not_become_nan_distances(self):
        self.assertIsNone(handle_nan(np.array([1,np.inf,3]),'skip'))
        np.testing.assert_allclose(handle_nan(np.array([1,np.inf,3])),[1,2,3])
        with self.assertRaises(ValueError):compute_distance(np.arange(3.),np.arange(3.),'typo')

    def test_initialization_never_drops_legacy_tables_or_duplicate_labels(self):
        conn=sqlite3.connect(self.root/'legacy.db')
        self.addCleanup(conn.close);conn.executescript(db.SCHEMA.replace(",\n    UNIQUE(source_key, col_name, start_ts, end_ts)", ""))
        conn.execute('CREATE TABLE scenarios (name TEXT)');conn.execute("INSERT INTO scenarios VALUES ('keep')")
        for label in ['review A','review B']:
            conn.execute("INSERT INTO basket_items(dataset_key,source_key,col_name,start_ts,end_ts,label) VALUES ('d','s','x','a','b',?)",(label,))
        conn.commit()
        with self.assertRaisesRegex(RuntimeError,'preserved'):db.init_db(conn)
        self.assertEqual(conn.execute('SELECT count(*) FROM basket_items').fetchone()[0],2)
        self.assertEqual(conn.execute('SELECT name FROM scenarios').fetchone()[0],'keep')

    def test_model_health_malformed_json_is_unavailable(self):
        service={'url':'http://unused','model_key':'verbalts-synth'}
        for body in [[],None,1,'oops']:
            with patch('backend.startup.requests.get',return_value=Mock(json=lambda:body)):
                self.assertFalse(ready(service))

    def test_model_cleanup_handles_exited_parent_and_signal_race(self):
        manager=ModelServers({});a=Mock(pid=11);b=Mock(pid=22)
        a.poll.return_value=0;b.poll.return_value=None
        handles=[io.BytesIO(),io.BytesIO()];manager.owned=[(a,handles[0]),(b,handles[1])]
        with patch('backend.startup.os.killpg',side_effect=ProcessLookupError) as kill:
            manager.close()
        self.assertTrue(all(handle.closed for handle in handles))
        self.assertEqual(manager.owned,[])
        kill.assert_any_call(11,signal.SIGTERM)
        kill.assert_any_call(22,signal.SIGTERM)


class ClusterSourceReview(unittest.TestCase):
    def test_joint_column_uses_correct_station_despite_reversed_order(self):
        times=[f'2026-01-01 0{i}:00:00' for i in range(4)]
        primary=[{'source_key':s,'timestamps':times,'values':[0,1,2,3]} for s in ['A','B']]
        overlay=[{'source_key':s,'timestamps':times,'values':v} for s,v in [('B',[30,20,10,0]),('A',[0,10,20,30])]]
        captured=[]
        def fit(X,**kwargs):
            captured.append(X)
            return ClusterResult(labels=np.array([0,1]),k=2,score=0,centers=X,is_auto_k=False)
        with patch('backend.segment_cluster.get_algorithm',return_value=Mock(fit_predict=fit)):
            result=cluster_segments(primary,unit='day',method='euclidean',normalize=False,overlay_sources_list=[overlay])
        X=captured[0]
        self.assertGreater(X[0,7],X[0,4])
        self.assertLess(X[1,7],X[1,4])
        self.assertEqual(result['k'],2)

    def test_missing_joint_station_is_excluded_not_borrowed(self):
        primary=[{'source_key':s,'values':[0,1,2,3]} for s in ['A','B','C']]
        overlay=[{'source_key':s,'values':[0,1,2,3]} for s in ['B','C']]
        def fit(X,**kwargs):return ClusterResult(labels=np.zeros(len(X),dtype=int),k=1,score=0,centers=np.mean(X,axis=0)[None],is_auto_k=False)
        with patch('backend.segment_cluster.get_algorithm',return_value=Mock(fit_predict=fit)):
            result=cluster_segments(primary,unit=None,overlay_sources_list=[overlay])
        self.assertEqual({x['source_key'] for g in result['groups'].values() for x in g},{'B','C'})

    def test_prepare_tokens_do_not_alias_two_tabs(self):
        from backend.routes.segment_search_routes import _make_cache_key
        self.assertNotEqual(_make_cache_key('d','x',['y','z']),_make_cache_key('d','x',['z','y']))


class MoreReviewTests(unittest.TestCase):
    setUp = ReviewTests.setUp
    seed = ReviewTests.seed

    def test_label_batch_rejects_invalid_last_row_without_partial_insert(self):
        conn,did,sid=self.seed()
        raw=b'start_ts,end_ts,label_type,confidence\na,b,event,0.9\nc,d,event,bad\n'
        response=self.client.post('/api/import_labels',data={'dataset_key':'sample','file':(io.BytesIO(raw),'labels.csv')})
        self.assertEqual(response.status_code,400,response.json)
        self.assertEqual(conn.execute('SELECT count(*) FROM ground_truth_labels').fetchone()[0],0)
        conn.execute("UPDATE datasets SET name='still writable'");conn.commit()

    def test_label_missing_optional_cells_do_not_become_nan_text(self):
        conn,did,sid=self.seed()
        raw=b'start_ts,end_ts,label_type,label_value,notes,confidence\na,b,event,,,\n'
        response=self.client.post('/api/import_labels',data={'dataset_key':'sample','file':(io.BytesIO(raw),'labels.csv')})
        self.assertEqual(response.status_code,200,response.json)
        row=conn.execute('SELECT description,metadata FROM ground_truth_labels').fetchone()
        self.assertEqual(row['description'],'')
        self.assertEqual(json.loads(row['metadata'])['confidence'],1.0)

    def test_column_limits_batch_is_all_or_nothing(self):
        conn,did,sid=self.seed()
        response=self.client.post('/api/column_limits/sample',json={'columns':[
            {'col_name':'x','val_min':7}, {'col_name':'y','val_min':'invalid'}]})
        self.assertEqual(response.status_code,400,response.json)
        self.assertIsNone(db.get_column_info(conn,'sample','x')['val_min'])
        for changes in [{'val_min':10,'val_max':2},{'val_min':float('inf')}]:
            self.assertEqual(self.client.post('/api/column_limits/sample',data=json.dumps({'columns':[{'col_name':'x',**changes}]}),content_type='application/json').status_code,400)

    def test_zero_preview_limit_is_rejected(self):
        self.assertEqual(self.client.get('/api/preprocess/column_preview/sample/x?max_pts=0').status_code,400)

    def test_basket_stream_keeps_connection_until_consumed(self):
        conn,did,sid=self.seed()
        saved=db.add_basket_items(conn,[dict(dataset_key='sample',source_key='sample/source',col_name='x',start_ts='2026-01-01 00:00:00',end_ts='2026-01-01 03:00:00',values=[0,1,2,3])])
        db.update_basket_item(conn,saved['ids'][0],status='confirmed')
        response=self.client.get('/api/basket/export')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'2026-01-01 03:00:00',response.data)
        response.close()
        self.assertEqual(self.client.put('/api/basket/'+str(saved['ids'][0]),json={'status':'missing'}).status_code,400)

    def test_basket_rejects_non_numeric_snapshot(self):
        conn,did,sid=self.seed()
        item=dict(dataset_key='sample',source_key='sample/source',col_name='x',start_ts='a',end_ts='b',values=[0,1,2],point_count='<img>')
        result=db.add_basket_items(conn,[item])
        self.assertTrue(result['errors'])
        self.assertEqual(conn.execute('SELECT count(*) FROM basket_items').fetchone()[0],0)

    def test_benchmark_second_file_failure_rolls_back_everything(self):
        from backend.routes import benchmark_import as bench
        def fake_clone(url,dest):
            folder=dest/'data'/'realKnownCause';folder.mkdir(parents=True)
            for name in bench.NAB_KNOWN_CAUSE_FILES[:2]:
                (folder/(name+'.csv')).write_text('timestamp,value\n2026-01-01 00:00:00,1\n2026-01-01 01:00:00,2\n')
            return True
        original=bench.save_uploaded_file;calls=0
        def fail_second(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('disk full')
            return original(*args)
        with patch.object(bench,'DATA_DIR',db.DATA_DIR),patch.object(bench,'_git_clone',side_effect=fake_clone),patch.object(bench,'save_uploaded_file',side_effect=fail_second):
            response=self.client.post('/api/benchmark/import_nab')
        self.assertEqual(response.status_code,500)
        conn=db.get_db();self.addCleanup(conn.close)
        self.assertIsNone(db.get_dataset(conn,'nab_known_cause'))
        self.assertEqual(list(db.DATA_DIR.rglob('*.csv')),[])

    @unittest.skipUnless(importlib.util.find_spec("torch"), "Optional torch runtime not installed")
    def test_failed_moment_initialization_can_retry(self):
        import sys
        from types import SimpleNamespace
        from backend import moment_service, moment_imputer
        from backend.clustering import moment_embed
        for module,loader,cache in [(moment_service,'load_moment_model','_moment_model'),(moment_imputer,'_load_model','_model'),(moment_embed,'_load_embed_model','_embed_model')]:
            first=Mock();first.init.side_effect=RuntimeError('incomplete load')
            second=Mock();second.to.return_value=second;second.float.return_value=second;second.eval.return_value=second
            factory=Mock(side_effect=[first,second])
            with patch.object(module,cache,None),patch.dict(sys.modules,{'momentfm':SimpleNamespace(MOMENTPipeline=SimpleNamespace(from_pretrained=factory))}),patch('backend.runtime_state.require_optional_models'),patch('backend.runtime_state.force_cpu',return_value=True):
                with self.assertRaises(RuntimeError):getattr(module,loader)()
                self.assertIsNone(getattr(module,cache))
                getattr(module,loader)()
                self.assertIs(getattr(module,cache),second)
                second.eval.assert_called_once()

    def test_stream_and_sync_share_selection_arguments(self):
        from backend.routes import segment_search_routes as routes
        body={'date_start':'2026-01-01','date_end':'2026-01-02','use_preprocessed':True,'inline_segments':[{'values':[0,1,2]}]}
        app=create_app()
        with app.app_context(),patch.object(routes,'_cluster_request',return_value={'k':2}) as run:
            self.assertEqual(routes._run_cluster_with_progress(body,'test'),{'k':2})
            self.assertEqual(run.call_args.args[0],body)
            self.assertTrue(callable(run.call_args.kwargs['progress_cb']))

    def test_stream_job_capacity_and_json_nan_contract(self):
        from queue import Queue
        import time
        from backend.routes import segment_search_routes as routes
        q=Queue();q.put(('done',{'score':float('nan')}))
        with patch.object(routes,'_progress_queues',{'one':q,'two':Queue()}),patch.object(routes,'_progress_finished',{}):
            self.assertEqual(self.client.post('/api/segment_search/segment_cluster_stream',json={}).status_code,429)
            routes._progress_finished['one']=time.monotonic()
            response=self.client.get('/api/segment_search/segment_cluster_progress/one')
            data=response.data.decode();response.close()
            parsed=json.loads(data.removeprefix('data: ').strip())
            self.assertIsNone(parsed['result']['score'])
            self.assertNotIn('one',routes._progress_queues)

    def test_waveform_algorithms_do_not_receive_feature_statistics(self):
        sources=[{'source_key':str(i),'values':[10,20,30,40]} for i in range(3)]
        for algorithm in ['moment','kshape']:
            def fit(X,**kwargs):
                self.assertEqual(X.shape,(3,4))
                return ClusterResult(labels=np.zeros(3,dtype=int),k=1,score=0,centers=None)
            with patch('backend.segment_cluster.get_algorithm',return_value=Mock(fit_predict=fit)):
                cluster_segments(sources,unit=None,method='preset',algorithm=algorithm)
            with self.assertRaisesRegex(ValueError,'kmeans'):
                cluster_segments(sources,unit=None,algorithm=algorithm,overlay_sources_list=[sources])

    def test_split_search_matches_overlay_names_not_list_order(self):
        from backend.session_store import DATA_STORE
        DATA_STORE['review-search']={'values':np.arange(4.),'labels':['a','b','c','d']}
        self.addCleanup(DATA_STORE.pop,'review-search',None)
        values=[0,1,2,3]
        segments=[{'label':'query','values':values,'overlays':[{'name':'up','values':values},{'name':'down','values':values[::-1]}]},
                  {'label':'same','values':values,'overlays':[{'name':'down','values':values[::-1]},{'name':'up','values':values}]}]
        r=self.client.post('/api/search_split_segments/review-search',json={'segments':segments})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['results'][0]['similarity'],1)
        segments[0]['overlays'][0]['values']=[None]*4
        self.assertEqual(self.client.post('/api/search_split_segments/review-search',json={'segments':segments}).status_code,400)

    def test_every_mutating_api_rejects_json_array_before_processing(self):
        import re
        app=create_app();client=app.test_client()
        for rule in app.url_map.iter_rules():
            if not rule.rule.startswith('/api/'):continue
            for method in rule.methods & {'POST','PUT','DELETE','PATCH'}:
                path=re.sub(r'<(?:[^:>]+:)?[^>]+>', '1',rule.rule)
                response=client.open(path,method=method,data='[]',content_type='application/json')
                self.assertEqual(response.status_code,400,(method,path,response.json))

    def test_owned_process_group_is_stopped_after_parent_exit(self):
        import os
        import subprocess
        import sys
        import time
        if os.name != 'posix':self.skipTest('POSIX process groups')
        pid_file=self.root/'child.pid'
        child="import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
        parent="import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); Path(sys.argv[1]).write_text(str(p.pid))"
        handle=(self.root/'child.log').open('wb')
        process=subprocess.Popen([sys.executable,'-c',parent,str(pid_file),child],start_new_session=True,stdout=handle,stderr=handle)
        manager=ModelServers({});manager.owned=[(process,handle)]
        self.addCleanup(manager.close)
        process.wait(timeout=5)
        child_pid=int(pid_file.read_text())
        manager.close()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            try:state=Path(f'/proc/{child_pid}/stat').read_text().split()[2]
            except FileNotFoundError:break
            if state=='Z':break
            time.sleep(.02)
        else:self.fail('Owned worker survived cleanup')
