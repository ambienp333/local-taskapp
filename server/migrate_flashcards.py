"""
One-time migration: drain flashcard DB tables into local files.
Run from the server directory: python migrate_flashcards.py
"""
import os
import json
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
import psycopg2
import psycopg2.extras
import sys
sys.path.insert(0, os.path.dirname(__file__))
import config

USER_ID = 'abel_main_user'


def get_conn():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=5432,
        database='postgres',
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        sslmode='require'
    )


def main():
    print('[migrate] connecting to DB...')
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM public.flashcard_reviews WHERE user_id=%s", (USER_ID,))
            review_rows = cur.fetchall()

            cur.execute("SELECT * FROM public.flashcard_config WHERE user_id=%s", (USER_ID,))
            config_rows = cur.fetchall()

            cur.execute("SELECT * FROM public.flashcard_file_config WHERE user_id=%s", (USER_ID,))
            file_config_rows = cur.fetchall()

            cur.execute("SELECT subject, filename, content FROM public.flashcard_files WHERE user_id=%s", (USER_ID,))
            file_rows = cur.fetchall()
    finally:
        conn.close()

    os.makedirs(config.FC_DIR, exist_ok=True)

    # reviews.json
    reviews = {}
    for r in review_rows:
        due = r['due_date']
        if hasattr(due, 'isoformat'):
            due = due.isoformat()
        reviewed_at = r['reviewed_at']
        if hasattr(reviewed_at, 'isoformat'):
            reviewed_at = reviewed_at.replace(tzinfo=None).isoformat(timespec='seconds')
        reviews[r['card_id']] = {
            'subject':     r['subject'],
            'interval':    r['interval'],
            'ease_factor': float(r['ease_factor']),
            'reps':        r['reps'],
            'due_date':    str(due),
            'seen':        r['seen'],
            'reviewed_at': str(reviewed_at),
        }

    # Merge with existing reviews.json if it exists (don't overwrite local data)
    existing_reviews = {}
    if os.path.exists(config.FC_REVIEWS_FILE):
        with open(config.FC_REVIEWS_FILE, 'r') as f:
            try:
                existing_reviews = json.load(f)
            except Exception:
                pass
    for card_id, rec in reviews.items():
        if card_id not in existing_reviews:
            existing_reviews[card_id] = rec
    with open(config.FC_REVIEWS_FILE, 'w') as f:
        json.dump(existing_reviews, f, indent=2)
    print(f'[migrate] reviews.json: {len(reviews)} DB records, {len(existing_reviews)} total → {config.FC_REVIEWS_FILE}')

    # config.json
    cfg = {}
    for r in config_rows:
        cfg[r['subject']] = {
            'enabled':    r['enabled'],
            'daily_goal': r['daily_goal'],
            'files':      {},
        }
    for r in file_config_rows:
        subj = r['subject']
        cfg.setdefault(subj, {'enabled': True, 'daily_goal': 30, 'files': {}})
        cfg[subj].setdefault('files', {})[r['filename']] = r['enabled']

    # Merge with existing config.json
    existing_cfg = {}
    if os.path.exists(config.FC_CONFIG_FILE):
        with open(config.FC_CONFIG_FILE, 'r') as f:
            try:
                existing_cfg = json.load(f)
            except Exception:
                pass
    for subj, data in cfg.items():
        if subj not in existing_cfg:
            existing_cfg[subj] = data
        else:
            # Merge file entries only
            for fn, enabled in data.get('files', {}).items():
                existing_cfg[subj].setdefault('files', {})[fn] = enabled
    with open(config.FC_CONFIG_FILE, 'w') as f:
        json.dump(existing_cfg, f, indent=2)
    print(f'[migrate] config.json: {len(cfg)} subjects → {config.FC_CONFIG_FILE}')

    # Card files stored only in DB → write to filesystem
    written = skipped = 0
    for r in file_rows:
        subj_dir = os.path.join(config.FC_DIR, r['subject'])
        os.makedirs(subj_dir, exist_ok=True)
        dest = os.path.join(subj_dir, r['filename'])
        if os.path.exists(dest):
            print(f'  skip (exists on disk): {r["subject"]}/{r["filename"]}')
            skipped += 1
        else:
            with open(dest, 'w') as f:
                f.write(r['content'])
            print(f'  wrote: {r["subject"]}/{r["filename"]}')
            written += 1
    print(f'[migrate] card files: {written} written, {skipped} skipped')
    print('[migrate] done. Run the server, then verify the flashcard tab looks correct.')


if __name__ == '__main__':
    main()
