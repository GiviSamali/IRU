"""Query semantics and deterministic SQLite work bounds, no timing assertions."""
import json
import sqlite3

from server.context_history import _PENDING_SOURCE_IDS_SQL


def history(size):
    c=sqlite3.connect(':memory:')
    c.executescript('''CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id INTEGER);
        CREATE INDEX idx_messages_chat ON messages(chat_id);
        CREATE TABLE context_summaries(id INTEGER PRIMARY KEY,owner_user_id INTEGER,chat_id INTEGER,source_ids TEXT);
        CREATE INDEX context_summaries_chat ON context_summaries(owner_user_id,chat_id);''')
    c.executemany('INSERT INTO messages VALUES(?,1)',[(n,) for n in range(1,size+1)])
    c.executemany('INSERT INTO context_summaries(owner_user_id,chat_id,source_ids) VALUES(1,1,?)',
        [(json.dumps(list(range(start,min(size+1,start+100)))),) for start in range(1,size+1,100)])
    return c


def pending(c,last,owner=1,chat=1):
    return [r[0] for r in c.execute(_PENDING_SOURCE_IDS_SQL,(chat,chat,last,owner,chat))]


def test_lookup_is_uncorrelated_and_work_grows_linearly():
    work=[]
    for size in (1000,2000,5000,10000):
        with history(size) as c:
            plan=' '.join(r[3] for r in c.execute('EXPLAIN QUERY PLAN '+_PENDING_SOURCE_IDS_SQL,(1,1,size,1,1)))
            assert 'CORRELATED' not in plan and 'LIST SUBQUERY' in plan
            steps=[0]
            def progress():
                steps[0]+=1000
                return int(steps[0]>1000000)
            c.set_progress_handler(progress,1000)
            assert pending(c,size)==[]
            work.append(steps[0]);c.set_progress_handler(None,0)
    # Ten times the history must not cause quadratic JSON expansion work.
    assert work[-1]<work[0]*12


def test_gaps_tail_limit_and_owner_chat_boundaries():
    with history(500) as c:
        c.execute('DELETE FROM context_summaries WHERE id=3')
        c.execute('INSERT INTO context_summaries(owner_user_id,chat_id,source_ids) VALUES(2,1,?)',
            (json.dumps(list(range(201,301))),))
        c.execute('INSERT INTO context_summaries(owner_user_id,chat_id,source_ids) VALUES(1,2,?)',
            (json.dumps(list(range(201,301))),))
        assert pending(c,500)==list(range(201,301))
        # NULL in a saved list must not hide unrelated uncovered messages.
        c.execute('INSERT INTO context_summaries(owner_user_id,chat_id,source_ids) VALUES(1,1,?)',('[null,250]',))
        assert pending(c,500)==[i for i in range(201,301) if i!=250]
        c.execute('DELETE FROM context_summaries WHERE owner_user_id=1 AND chat_id=1')
        assert pending(c,500)==list(range(1,129))
        assert pending(c,10)==[1,2,3,4]  # newest six and later turns stay out
        assert pending(c,500,chat=3)==[]


def test_new_turn_and_deleted_summary_restore_only_uncovered_ids():
    with history(1000) as c:
        assert pending(c,1000)==[]
        c.executemany('INSERT INTO messages VALUES(?,1)',[(n,) for n in range(1001,1011)])
        assert pending(c,1010)==[1001,1002,1003,1004]
        c.execute('DELETE FROM context_summaries WHERE id=3')
        assert pending(c,1010)==list(range(201,301))+[1001,1002,1003,1004]
