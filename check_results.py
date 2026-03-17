import os, sys
sys.path.insert(0, r'C:\Users\skand\Downloads\Meeting_summarizer')
os.environ.setdefault('DATABASE_URL', open(r'C:\Users\skand\Downloads\Meeting_summarizer\.env').read().split('DATABASE_URL=')[1].split()[0])

from database import SessionLocal
from models import Result

db = SessionLocal()
results = db.query(Result).all()
for r in results:
    print('JobID:', r.job_id[:8])
    print('Transcript length:', len(r.transcript or ''))
    print('Summary JSON length:', len(r.summary_json or ''))
    print('Summary preview:', (r.summary_json or '')[:200])
    print('---')
