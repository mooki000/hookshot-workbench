"""SQLite storage: metrics are unique per night/scenario/name, confirmation is separate evidence."""
import json
import sqlite3
from common import put


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS nights (
                id TEXT PRIMARY KEY, commit_sha TEXT NOT NULL, started TEXT NOT NULL,
                ended TEXT, status TEXT NOT NULL, build_json TEXT NOT NULL, tests_json TEXT);
            CREATE TABLE IF NOT EXISTS scenarios (
                night TEXT, name TEXT, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                disturbed INTEGER NOT NULL, accepted INTEGER NOT NULL DEFAULT 0, result_json TEXT NOT NULL,
                PRIMARY KEY(night,name));
            CREATE TABLE IF NOT EXISTS metrics (
                night TEXT, scenario TEXT, metric TEXT, value REAL NOT NULL, kind TEXT NOT NULL,
                direction TEXT NOT NULL, PRIMARY KEY(night,scenario,metric));
            CREATE TABLE IF NOT EXISTS findings (
                night TEXT, scenario TEXT, kind TEXT, evidence_json TEXT NOT NULL);
        ''')

    def begin(self, night, build):
        self.db.execute('INSERT INTO nights VALUES (?,?,?,?,?,?,?)',
                        (night, build['commit'], build['started'], None, 'running', json.dumps(build), None))
        self.db.commit()

    def suite(self, night, tests):
        self.db.execute('UPDATE nights SET tests_json=? WHERE id=?', (json.dumps(tests), night))
        self.db.commit()

    def previous_suite(self, night):
        row = self.db.execute('SELECT tests_json FROM nights WHERE id<? AND tests_json IS NOT NULL ORDER BY id DESC LIMIT 1',
                              (night,)).fetchone()
        return json.loads(row[0]) if row else {}

    def previous(self, night, result):
        query = '''SELECT s.result_json,n.commit_sha,s.night FROM scenarios s
            JOIN nights n ON n.id=s.night WHERE s.name=? AND s.fingerprint=? AND s.night<? AND s.accepted=1 '''
        args = (result['id'], result['scenario_sha256'], night)
        rows = self.db.execute(query + 'ORDER BY s.night DESC LIMIT 7', args).fetchall()
        quiet = self.db.execute(query + 'AND s.disturbed=0 ORDER BY s.night DESC LIMIT 7', args).fetchall()
        by_night = {n: {**json.loads(r), 'commit': c, 'night': n} for r, c, n in rows + quiet}
        return [by_night[n] for n in sorted(by_night)]

    def record(self, night, result, accepted):
        self.db.execute('INSERT OR REPLACE INTO scenarios VALUES (?,?,?,?,?,?,?)',
                        (night, result['id'], result['scenario_sha256'], result['status'],
                         int(result['disturbed']), int(accepted), json.dumps(result)))
        for name, metric in result['metrics'].items():
            self.db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?)',
                            (night, result['id'], name, metric['value'], metric['kind'], metric['direction']))
        self.db.commit()

    def finding(self, night, scenario, kind, evidence):
        self.db.execute('INSERT INTO findings VALUES (?,?,?,?)', (night, scenario, kind, json.dumps(evidence)))
        self.db.commit()

    def end(self, night, status, ended):
        self.db.execute('UPDATE nights SET status=?,ended=? WHERE id=?', (status, ended, night))
        self.db.commit()

    def export_baseline(self, path):
        rows = self.db.execute('''WITH recent AS (
            SELECT night,name,row_number() OVER (PARTITION BY name,fingerprint ORDER BY night DESC) AS rank
            FROM scenarios WHERE accepted=1), quiet AS (
            SELECT night,name,row_number() OVER (PARTITION BY name,fingerprint ORDER BY night DESC) AS rank
            FROM scenarios WHERE accepted=1 AND disturbed=0)
            SELECT night,name,fingerprint,result_json FROM scenarios WHERE (night,name) IN (
                SELECT night,name FROM recent WHERE rank<=7 UNION SELECT night,name FROM quiet WHERE rank<=7)
            ORDER BY night DESC''').fetchall()
        records = {}
        for night, name, fingerprint, body in rows:
            key = name + ':' + fingerprint
            bucket = records.setdefault(key, {'accepted': [], 'undisturbed': []})
            r = json.loads(body)
            value = {'night': night, 'disturbed': r['disturbed'], 'metrics': r['metrics'],
                     'status': r['status'], 'signatures': r['signatures'],
                     'signature_version': r.get('signature_version', 1)}
            if len(bucket['accepted']) < 7:
                bucket['accepted'].append(value)
            if not r['disturbed'] and len(bucket['undisturbed']) < 7:
                bucket['undisturbed'].append(value)
        put(path, {'minimum_nights': 3, 'maximum_nights': 7, 'scenarios': records})
