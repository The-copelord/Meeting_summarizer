import os, sys
sys.path.insert(0, r'C:\Users\skand\Downloads\Meeting_summarizer')
os.environ.setdefault('DATABASE_URL', open(r'C:\Users\skand\Downloads\Meeting_summarizer\.env').read().split('DATABASE_URL=')[1].split()[0])

from database import SessionLocal
from models import Job, Result, JobStatus
db = SessionLocal()
jobs = db.query(Job).order_by(Job.created_at.desc()).limit(5).all()
for j in jobs:
    r = db.query(Result).filter(Result.job_id == j.id).first()
    has_result = 'YES' if r else 'NO'
    print('Job:', j.id[:8], '| Status:', j.status.value, '| Error:', j.error_msg, '| Result:', has_result)
