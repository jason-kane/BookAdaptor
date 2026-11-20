"""Code gates fail closed and feed exactly one same-model correction."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from home.app import create_app
from home.polish import database
from home.polish.proposals import submit_proposal
from home.polish.seeking import enqueue_search, state_put
from home.polish.validation import ValidationFailure, syntax_check, relevant_tests
from home.polish.worker import run_once, run_repair_once

MODEL={'id':'ollama:test','provider':'ollama','model':'test','label':'Ollama · test','version':'abc'}
PASS={'passed':True,'tests':['tests/test_calculate.py'],
      'baseline':{'passed':True,'checks':[]},'candidate':{'passed':True,'checks':[]},'message':'Checks passed.'}
FAIL={'passed':False,'retryable':True,'message':'Candidate regression: expected 3, got 2',
      'baseline':{'passed':True,'checks':[]},'candidate':{'passed':False,'checks':[{'command':['pytest'],'passed':False,'output':'expected 3, got 2'}]}}

@pytest.fixture
def app(tmp_path):
    root=tmp_path/'repo';root.mkdir();(root/'tests').mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    (root/'calculate.py').write_text('def value():\n    return 1\n')
    (root/'tests/test_calculate.py').write_text('from calculate import value\ndef test_value():\n    assert value() > 0\n')
    app=create_app({'TESTING':True,'TODO_DIR':str(tmp_path/'data'),'POLISH_WORKSPACE':str(root)})
    with app.app_context(),database() as c:
        state_put(c,'models',{'models':[MODEL]})
    return app


def output(replacement='return 2'):
    return dict(kind='file',description='A small improvement.',path='calculate.py',search='return 1',
                replacement=replacement,goal_id='',title='',evidence='')


def queued(app):
    with app.app_context(),patch('home.polish.seeking.start_worker'):
        return enqueue_search('G001',MODEL['id'])


def existing_model_entry(app):
    job=queued(app)
    with app.app_context(),patch('home.polish.worker.generate',return_value=(output(),{})),patch('home.polish.validation.validate_change',return_value=PASS):
        run_once()
        with database() as c:
            return c.execute('SELECT id FROM entries').fetchone()[0]


def test_leaked_thinking_and_broken_code_are_rejected_before_submission(app):
    malformed='def value():\n    return 2"}]} /think</think>{\n'
    with pytest.raises(ValueError,match='syntax validation failed'):
        syntax_check('calculate.py',malformed)
    with app.app_context(),pytest.raises(ValueError,match='syntax validation failed'):
        submit_proposal(goal_id='G001',model_label='Model',description='Broken',before_text='return 1',after_text=malformed,
                        proposal_key='broken',target={'kind':'file','path':'calculate.py','before':'return 1','after':malformed})


def test_relevant_test_selection_prefers_matching_unit_suite(app):
    root=Path(app.config['POLISH_WORKSPACE'])
    assert relevant_tests(root,'calculate.py')==['tests/test_calculate.py']


def test_syntax_failure_gets_one_same_model_retry_with_error_context(app):
    queued(app)
    with app.app_context(),patch('home.polish.worker.generate',side_effect=[(output('return 2"}]} /think</think>{'),{}),(output('return 3'),{})]) as generate,patch('home.polish.validation.docker_validation',return_value=PASS):
        run_once()
        assert generate.call_count==2
        assert generate.call_args_list[0].args[0]==generate.call_args_list[1].args[0]
        correction=generate.call_args_list[1].args[1]
        assert 'SECOND AND FINAL' in correction and 'syntax validation failed' in correction
        with database() as c:
            job=c.execute('SELECT * FROM polish_jobs').fetchone()
            attempts=json.loads(job['attempts_json'])
            assert [attempt['state'] for attempt in attempts]==['failed','passed']
            entry=c.execute('SELECT * FROM entries').fetchone()
            assert entry['after_text']=='def value():\n    return 3\n'
            assert entry['reaction'] is None and entry['proposal_key'].endswith(':2')
    assert (Path(app.config['POLISH_WORKSPACE'])/'calculate.py').read_text()=='def value():\n    return 1\n'


def test_python_syntax_correction_preserves_existing_function_bodies():
    from home.polish.worker import correction_prompt
    context={'prompt':'Improve the selected goal.'}
    prompt=correction_prompt(context,{'path':'src/example.py','replacement':'def f():\n    pass'},
        {'stage':'syntax','message':'Python syntax validation failed in src/example.py:22: expected an indented block'})
    assert 'Preserve every existing function, class, and control-flow body' in prompt
    assert 'Do not end a replacement with a function or class header' in prompt


def test_unchanged_edit_correction_calls_out_identical_source_spans():
    from home.polish.worker import correction_prompt
    prompt=correction_prompt({'prompt':'Improve storage reliability.'},None,
        {'stage':'proposal','message':'Model edit must change a non-empty source span.'})
    assert 'search and replacement were identical' in prompt
    assert 'demonstrably different' in prompt


def test_truncated_model_correction_requests_a_shorter_proposal():
    from home.polish.worker import correction_prompt
    prompt=correction_prompt({'prompt':'Improve storage reliability.'},None,
        {'stage':'proposal','message':'Model output was truncated. No review entry was created.'})
    assert 'substantially shorter' in prompt


def test_second_failure_stops_without_a_third_call_or_an_entry(app):
    queued(app)
    with app.app_context(),patch('home.polish.worker.generate',return_value=(output('return 2"}]} /think</think>{'),{})) as generate:
        run_once();assert run_once() is False
        assert generate.call_count==2
        with database() as c:
            assert c.execute('SELECT COUNT(*) FROM entries').fetchone()[0]==0
            assert c.execute('SELECT status FROM polish_jobs').fetchone()[0]=='failed'


def test_baseline_failures_do_not_charge_model_for_an_unrelated_broken_environment(app):
    queued(app)
    report={**FAIL,'retryable':False,'message':'Baseline test failed before changes.','baseline':{'passed':False,'checks':[]}}
    with app.app_context(),patch('home.polish.worker.generate',return_value=(output(),{})) as generate,patch('home.polish.validation.docker_validation',return_value=report):
        run_once()
        assert generate.call_count==1
        with database() as c: assert c.execute('SELECT COUNT(*) FROM entries').fetchone()[0]==0


def test_live_application_checks_outside_database_transaction_and_rechecks_current_source(app):
    entry=existing_model_entry(app)
    def check(path,before,after):
        with app.app_context(),database() as c:
            assert c.execute('SELECT application_status FROM entries WHERE id=?',(entry,)).fetchone()[0]=='validating'
            state_put(c,'validation-test-lock',True)  # Must not block on another held writer.
        assert before=='def value():\n    return 1\n'
        assert after=='def value():\n    return 2\n'
        return PASS
    with patch('home.polish.validation.validate_change',side_effect=check):
        response=app.test_client().post(f'/polish/entries/{entry}/reaction',json={'reaction':'Good'})
    assert response.json['application_status']=='applied'
    assert (Path(app.config['POLISH_WORKSPACE'])/'calculate.py').read_text()=='def value():\n    return 2\n'


def test_failed_application_queues_one_correction_but_never_applies_it_automatically(app):
    entry=existing_model_entry(app)
    with patch('home.polish.validation.validate_change',side_effect=ValidationFailure(FAIL)),patch('home.polish.repairs.notify_worker'):
        response=app.test_client().post(f'/polish/entries/{entry}/reaction',json={'reaction':'Good'})
    assert response.json['application_status']=='blocked'
    source=Path(app.config['POLISH_WORKSPACE'])/'calculate.py'
    assert 'return 1' in source.read_text()
    with app.app_context(),patch('home.polish.worker.generate',return_value=(output('return 3'),{})) as generate,patch('home.polish.validation.validate_change',return_value=PASS):
        assert run_repair_once()
        assert not run_repair_once()
        assert generate.call_count==1
        with database() as c:
            original=c.execute('SELECT * FROM entries WHERE id=?',(entry,)).fetchone()
            corrected=c.execute('SELECT * FROM entries WHERE id=?',(original['correction_entry_id'],)).fetchone()
            assert corrected['reaction'] is None and corrected['id']!=entry
            assert corrected['after_text'].endswith('return 3\n')
    assert 'return 1' in source.read_text()


def test_rejecting_an_entry_cancels_pending_correction_without_model_work(app):
    entry=existing_model_entry(app)
    with patch('home.polish.validation.validate_change',side_effect=ValidationFailure(FAIL)),patch('home.polish.repairs.notify_worker'):
        app.test_client().post(f'/polish/entries/{entry}/reaction',json={'reaction':'Good'})
    app.test_client().post(f'/polish/entries/{entry}/reaction',json={'reaction':'Trash'})
    with app.app_context(),patch('home.polish.worker.generate',side_effect=AssertionError('Rejected work must not trigger correction')):
        run_repair_once()
        with database() as c: assert c.execute('SELECT status FROM polish_repairs').fetchone()[0]=='cancelled'


def test_source_changed_during_tests_is_not_overwritten(app):
    entry=existing_model_entry(app)
    source=Path(app.config['POLISH_WORKSPACE'])/'calculate.py'
    def change_during_check(*args):
        source.write_text('def value():\n    return 99\n')
        return PASS
    with patch('home.polish.validation.validate_change',side_effect=change_during_check):
        response=app.test_client().post(f'/polish/entries/{entry}/reaction',json={'reaction':'Good'})
    assert response.json['application_status']=='conflict'
    assert 'return 99' in source.read_text()


def test_python_fragment_indentation_preserves_relative_blocks_but_not_string_contents():
    from home.polish.worker import relative_python_fragment
    before='def f():\n    old = 1\n    return old\n'
    fragment='if True:\n    new = 2\nelse:\n    new = 3'
    normalized=relative_python_fragment(before,'old = 1',fragment)
    assert normalized=='if True:\n        new = 2\n    else:\n        new = 3'
    assert relative_python_fragment(before,'old = 1','value = """first\nsecond"""') is None
    assert relative_python_fragment('x = old + 1\n','old','a = 1\nb = 2') is None


def test_dedented_complete_python_fragment_is_validated_before_review(app):
    queued(app)
    replacement='updated = 3\nreturn updated'
    with app.app_context(),patch('home.polish.worker.generate',return_value=(output(replacement),{})),patch('home.polish.validation.docker_validation',return_value=PASS):
        run_once()
        with database() as c:
            entry=c.execute('SELECT * FROM entries').fetchone()
            assert entry['after_text']=='def value():\n    updated = 3\n    return updated\n'
            assert entry['reaction'] is None


def test_unclean_response_is_returned_as_context_for_the_single_correction(app):
    from home.polish.providers import ModelOutputError
    queued(app)
    with app.app_context(),patch('home.polish.worker.generate',side_effect=[ModelOutputError('Invalid JSON','/think</think>{broken'),(output(),{})]) as generate,patch('home.polish.validation.docker_validation',return_value=PASS):
        run_once()
        assert generate.call_count==2
        assert '/think</think>{broken' in generate.call_args_list[1].args[1]
