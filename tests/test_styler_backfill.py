from contextlib import nullcontext
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from styler.backfill import start,next_sample,STATE


class RedisMemory:
    def __init__(self):
        self.values={};self.sets={'tags':{'current','other'},'current/tags':{'first','shared'},'other/tags':{'shared','last'}}
        self.busy=False
    def lock(self,*args,**kwargs):return nullcontext()
    def get(self,key):return self.values.get(key)
    def set(self,key,value,nx=False,**kwargs):
        if nx and key in self.values:return False
        self.values[key]=value;return True
    def delete(self,*keys):
        for key in keys:self.values.pop(key,None);self.sets.pop(key,None)
    def smembers(self,key):return self.sets.get(key,set())
    def sscan(self,key,**kwargs):return 0,list(self.smembers(key))
    def sadd(self,key,value):self.sets.setdefault(key,set()).add(value)
    def sismember(self,key,value):return value in self.smembers(key)
    def expire(self,*args):pass
    def llen(self,key):return int(self.busy)
    def type(self,key):return 'hash'


class BackfillTests(unittest.TestCase):
    def test_tagging_finishes_each_style_before_advancing(self):
        from styler.backfill import next_tag_sample
        client = RedisMemory()
        start(client, 'current')
        state = json.loads(client.get(STATE))
        state.update(phase='tags', engine=1, members=['obsolete'], scanned=True)
        client.set(STATE, json.dumps(state))
        queued = {}
        def schedule(_client, task, path):
            queued[task['job_id']] = json.dumps(task)
        client.eval = lambda script, n, queue, job: queued.pop(job, None)
        with TemporaryDirectory() as tmp, patch('styler.image_tags.schedule', side_effect=schedule):
            folder = Path(tmp) / 'style_exploration'
            folder.mkdir()
            for tag in ('first', 'shared'):
                for engine in ('zit', 'krea2', 'qwen'):
                    (folder / f'{tag}_{engine}.png').write_bytes(b'image')
            # A missing image should not prevent finishing the same style.
            (folder / 'first_krea2.png').unlink()
            jobs = []
            for _ in range(30):
                job = next_tag_sample(client, 'queue', tmp, budget=1)
                if job:
                    jobs.append((job['style_tag'], job['engine']))
                if json.loads(client.get(STATE))['phase'] == 'images':
                    break
            self.assertEqual(len(jobs), 5)
            tags = [tag for tag, engine in jobs]
            self.assertEqual(len([i for i in range(1, len(tags)) if tags[i] != tags[i-1]]), 1)
            self.assertEqual([engine for tag, engine in jobs if tag == 'shared'], ['zit', 'krea2', 'qwen'])
            self.assertEqual([engine for tag, engine in jobs if tag == 'first'], ['zit', 'qwen'])
            self.assertEqual(json.loads(client.get(STATE))['category'], 1)

    def test_current_category_first_then_other_categories_without_duplicates(self):
        client=RedisMemory();start(client,'current')
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)/'style_exploration';folder.mkdir()
            (folder/'first_zit.png').write_bytes(b'existing')
            jobs=[]
            while STATE in client.values:
                # Each invocation resumes the persisted planner state.
                task=next_sample(client,'queue',tmp,budget=4)
                if task:jobs.append(task)
                else:
                    state=json.loads(client.get(STATE) or '{}')
                    if state.get('phase')=='tags':
                        state.update(phase='images',category=state['category']+1)
                        client.set(STATE,json.dumps(state))
            self.assertEqual(len(jobs),8)
            self.assertEqual(len({job['job_id'] for job in jobs}),8)
            categories=[job['background_category'] for job in jobs]
            self.assertEqual(categories,sorted(categories,key=lambda c:c!='current'))
            self.assertNotIn(('first','zit'),[(job['style_tag'],job['engine']) for job in jobs])
            self.assertEqual([job['engine'] for job in jobs[:5]],['zit','krea2','krea2','qwen','qwen'])

    def test_interactive_jobs_pause_background_without_advancing(self):
        client=RedisMemory();start(client,'current');before=client.get(STATE);client.busy=True
        self.assertIsNone(next_sample(client,'queue','/unused'))
        self.assertEqual(client.get(STATE),before)

    def test_loaded_engine_is_preferred(self):
        client=RedisMemory();start(client,'current')
        with TemporaryDirectory() as tmp:
            self.assertEqual(next_sample(client,'queue',tmp,'qwen')['engine'],'qwen')

    def test_category_boundary_pauses_images_for_tagging(self):
        client=RedisMemory();start(client,'current')
        with TemporaryDirectory() as tmp:
            for _ in range(20):
                next_sample(client,'queue',tmp)
                state=json.loads(client.get(STATE))
                if state.get('phase')=='tags':break
            self.assertEqual(state['phase'],'tags')
            self.assertEqual(state['category'],0)
            self.assertIsNone(next_sample(client,'queue',tmp))
            from styler.backfill import next_tag_sample
            self.assertIsNone(next_tag_sample(client,'queue',tmp))
            state=json.loads(client.get(STATE))
            self.assertEqual(state['phase'],'images')
            self.assertEqual(state['category'],1)
