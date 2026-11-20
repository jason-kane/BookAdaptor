"""Bounded, same-model correction requests retaining original review evidence."""
import json
import time
from . import database


def attempt_history(job):
    attempts=json.loads(job['attempts_json'])
    if not attempts and job['response_json']:
        response=json.loads(job['response_json'])
        attempts=[{'number':1,'state':'returned','proposal':response.get('proposal'),
                   'metadata':response.get('metadata',{}),'legacy':True}]
    return attempts


def queue_repair(entry_id, report, sources=None):
    with database() as connection:
        entry=connection.execute('SELECT * FROM entries WHERE id=?',(entry_id,)).fetchone()
        if entry['reaction'] in ('Bad','Trash'):
            return False
        connection.execute("UPDATE entries SET application_status='blocked',validation_json=?,application_message=? WHERE id=?",
            (json.dumps(report),'Validation failed. '+report['message'],entry_id))
        if not report.get('retryable',True):
            return False
        key=entry['proposal_key'] or ''
        if not key.startswith('goal-search:'):
            connection.execute("UPDATE entries SET application_message=application_message || ' No originating model job is available for correction.' WHERE id=?",(entry_id,))
            return False
        job_id=key.split(':')[1]
        job=connection.execute('SELECT * FROM polish_jobs WHERE id=?',(job_id,)).fetchone()
        if not job or len(attempt_history(job))>=2:
            connection.execute("UPDATE entries SET application_message=application_message || ' The second-try limit has been reached.' WHERE id=?",(entry_id,))
            return False
        existing=connection.execute('SELECT * FROM polish_repairs WHERE entry_id=?',(entry_id,)).fetchone()
        if existing:
            connection.execute('UPDATE entries SET application_message=?,correction_entry_id=? WHERE id=?',
                ('Validation failed. '+existing['message'],existing['result_entry_id'],entry_id))
            return False
        connection.execute('''INSERT INTO polish_repairs (entry_id,job_id,message,failure_json,source_json,created_at)
            VALUES (?,?,?,?,?,?)''',(entry_id,job_id,'One same-model correction is queued.',json.dumps(report),json.dumps(sources or {}),time.time()))
        connection.execute("UPDATE entries SET application_message=? WHERE id=?",
                           ('Validation failed. One same-model correction is queued. '+report['message'],entry_id))
    return True


def notify_worker():
    from .seeking import start_worker
    try:
        start_worker()
    except (OSError,RuntimeError):
        # Durable queued corrections can be picked up when the worker becomes available.
        pass
