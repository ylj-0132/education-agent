"""Local versioned evidence and completed profiles, separate from checkpoints."""
import json
import sqlite3
from pathlib import Path
from java5_data import load_records
from .run_store import RunStore, fingerprint, stamp


def verified_records(root, manifest_path):
    records = load_records(root)
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    sources = {r['source']['file']: r['source']['sha256'] for r in records}
    if sources != manifest.get('source_files'):
        raise ValueError('Original source files differ from audited source manifest')
    return records


class DatasetStore:
    def __init__(self, path, records):
        self.records = records
        ids = [r['record_id'] for r in records]
        if len(ids) != len(set(ids)) or not records:
            raise ValueError('Invalid dataset evidence IDs')
        self.version = fingerprint(records)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA synchronous=FULL')
        try:
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS dataset (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, imported_at TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS evidence (record_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN (\'train\',\'test\')), problem_id TEXT NOT NULL, payload TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS profiles (run_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, sha256 TEXT NOT NULL, run_path TEXT NOT NULL, payload TEXT NOT NULL, registered_at TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS profile_heads (student_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES profiles(run_id))')
                row = self.db.execute('SELECT version FROM dataset WHERE id=1').fetchone()
                if row:
                    stored = [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM evidence ORDER BY record_id')]
                    if row[0] != self.version or fingerprint(stored) != fingerprint(sorted(records,key=lambda r:r['record_id'])):
                        raise ValueError('Existing dataset identity/content mismatch')
                else:
                    self.db.execute('INSERT INTO dataset VALUES (1,?,?)',(self.version,stamp()))
                    self.db.executemany('INSERT INTO evidence VALUES (?,?,?,?,?)',[
                        (r['record_id'],r['student_id'],r['split'],r['problem_id'],json.dumps(r,ensure_ascii=False)) for r in records])
        except BaseException:
            self.db.close()
            raise

    def __enter__(self): return self
    def __exit__(self,*args): self.db.close()

    def publish(self, profile, run_path):
        student = profile.get('student_id')
        expected = [r for r in self.records if r['student_id']==student and r['split']=='train']
        actual = profile.get('evidence',[])
        if (profile.get('status')!='completed' or not expected or
                fingerprint(sorted(expected,key=lambda r:r['record_id'])) != fingerprint(sorted(actual,key=lambda r:r['record_id']))):
            raise ValueError('Only completed full train profiles may be registered')
        ids = {r['record_id'] for r in expected}
        for observation in profile.get('observations',[]):
            if (observation.get('student_id')!=student or observation.get('evidence_id') not in ids
                    or observation.get('concept_id') not in profile['concepts']):
                raise ValueError('Profile observation is outside the student train evidence')
        for summary in profile.get('summaries',{}).values():
            if not set(summary.get('evidence_ids',[])) <= ids:
                raise ValueError('Profile summary cites non-training evidence')
        state = RunStore.inspect(run_path,full=True)
        if state['status']!='completed' or state['identity'].get('records_sha256')!=fingerprint(actual):
            raise ValueError('Completed checkpoint does not match profile evidence')
        exported = json.loads((Path(run_path)/'profile.json').read_text(encoding='utf-8'))
        if exported != profile:
            raise ValueError('Profile differs from completed output')
        digest = fingerprint(profile)
        with self.db:
            row = self.db.execute('SELECT sha256 FROM profiles WHERE run_id=?',(state['run_id'],)).fetchone()
            if row and row[0]!=digest:
                raise ValueError('Registered profile changed')
            self.db.execute('INSERT OR IGNORE INTO profiles VALUES (?,?,?,?,?,?)',(
                state['run_id'],student,digest,str(Path(run_path).resolve()),json.dumps(profile,ensure_ascii=False),stamp()))
            self.db.execute('INSERT OR REPLACE INTO profile_heads VALUES (?,?)',(student,state['run_id']))

    def snapshot(self):
        return {'dataset_sha256':self.version,
                'records':self.db.execute('SELECT count(*) FROM evidence').fetchone()[0],
                'splits':dict(self.db.execute('SELECT split,count(*) FROM evidence GROUP BY split')),
                'students':self.db.execute('SELECT count(distinct student_id) FROM evidence').fetchone()[0],
                'completed_students':[r[0] for r in self.db.execute('SELECT student_id FROM profile_heads ORDER BY student_id')]}
