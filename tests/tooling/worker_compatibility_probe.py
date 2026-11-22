import json
import os
from datetime import timedelta

from sqlalchemy import text
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.runtime import Database, DatabaseError
from tabula_rasa.infrastructure.postgres.jobs import JobQueue, JobRegistry
from tabula_rasa.infrastructure.postgres.recurring_jobs import enqueue_reconciliation, recurring_registry
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services

db = Database(DatabaseSettings.from_environment(os.environ, required=True))
try:
    # Use the database clock explicitly; the host clock is not a test input.
    with db.transaction(read_only=True) as c:
        now = c.scalar(text('SELECT clock_timestamp()'))
        schema = c.scalar(text('SELECT version_num FROM alembic_version'))
    a = create_postgres_services(db, 'alpha')
    b = create_postgres_services(db, 'beta')
    qa = JobQueue(db, 'alpha', recurring_registry(), service_id='disposable-nas-probe')
    qb = JobQueue(db, 'beta', recurring_registry(), service_id='disposable-nas-probe')
    ids = enqueue_reconciliation(db, 'alpha', through_date='2026-02-01')
    assert len(ids) == 1
    assert enqueue_reconciliation(db, 'alpha', through_date='2026-02-01') == ids
    claim = qa.claim()
    assert claim, 'claim failed'
    assert qb.execute(*claim) is False, 'cross-business claim accepted'
    assert qa.execute(*claim) is True, 'recurring execution failed'
    assert qa.execute(*claim) is False, 'duplicate execution accepted'
    assert len(a.transactions.list()) == 2 and len(b.transactions.list()) == 0
    assert enqueue_reconciliation(db, 'alpha', through_date='2026-02-01') == []
    beta_ids = enqueue_reconciliation(db, 'beta', through_date='2026-02-01')
    assert len(beta_ids) == 1 and beta_ids != ids
    claim = qb.claim()
    assert claim and qb.execute(*claim) is True
    assert len(b.transactions.list()) == 2
    assert {r['amount'] for r in a.transactions.list()} == {'12.50'}
    assert {r['amount'] for r in b.transactions.list()} == {'99.00'}
    # Credible worker failure: rollback, bounded retry, durable attempt evidence.
    registry = JobRegistry()
    def failing(database, execution):
        raise DatabaseError('unavailable')
    registry.register('probe.failure', failing)
    q = JobQueue(db, 'alpha', registry, service_id='disposable-nas-failure')
    jid = q.enqueue('probe.failure', 'failure', due_at=now-timedelta(seconds=1), max_attempts=2)
    claim = q.claim()
    assert claim and q.execute(*claim) is False
    assert q.claim() is None, 'backoff ignored'
    with db.transaction() as c:
        c.execute(text('UPDATE jobs SET due_at=clock_timestamp()-interval \'1 second\' WHERE id=:id'), {'id':jid})
    claim = q.claim()
    assert claim and q.execute(*claim) is False
    row = next(r for r in q.diagnostics() if str(r['id']) == jid)
    assert row['state'] == 'dead' and row['attempts'] == 2
    assert [r['outcome'] for r in q.attempts(jid)] == ['transient','transient']
    assert len(a.transactions.list()) == len(b.transactions.list()) == 2
    print(json.dumps({'schema':schema,'success':True,'checks':['recurring-generation','deduplication','two-business-isolation','bounded-failure-backoff']}))
finally:
    db.dispose()
