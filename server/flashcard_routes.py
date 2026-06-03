import os
import re
import secrets
from flask import request, jsonify
from datetime import date, timedelta
import psycopg2
import psycopg2.extras
import config

SUBJECTS = ['genetics', 'math', 'biology']
USER_ID  = 'abel_main_user'


def get_conn():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=5432,
        database='postgres',
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        sslmode='require'
    )


def get_fs_files(subject):
    d = os.path.join(config.FC_DIR, subject)
    if not os.path.isdir(d):
        return []
    return [f for f in os.listdir(d) if f.endswith('.md')]


def is_card(line):
    return bool(re.match(r'^(\s*)-\s+.+(→|>>>)', line))


def has_id(line):
    return bool(re.search(r'<!--\s*id:\s*[a-f0-9]+\s*-->', line))


def add_ids(text):
    lines = text.split('\n')
    output = []
    added = 0
    for line in lines:
        if is_card(line):
            prev = output[-1] if output else ''
            if not has_id(prev):
                indent = re.match(r'^(\s*)', line).group(1)
                output.append(f'{indent}<!-- id: {secrets.token_hex(4)} -->')
                added += 1
        output.append(line)
    return '\n'.join(output), added


def parse_cards(content, subject, filename):
    lines = content.split('\n')
    cards = []
    pending_id = None
    for line in lines:
        m = re.search(r'<!--\s*id:\s*([a-f0-9]+)\s*-->', line)
        if m:
            pending_id = m.group(1)
        elif is_card(line) and pending_id:
            sep = '>>>' if '>>>' in line else '→'
            clean = re.sub(r'^(\s*)-\s+', '', line).strip()
            parts = clean.split(sep, 1)
            front = parts[0].strip()
            back  = parts[1].strip() if len(parts) > 1 else ''
            cards.append({
                'id': pending_id, 'front': front, 'back': back,
                'subject': subject, 'filename': filename,
            })
            pending_id = None
        elif line.strip() and not re.search(r'<!--.*-->', line):
            pending_id = None
    return cards


def sm2(rating, interval, ease_factor, reps):
    if rating < 3:
        new_reps     = 0
        new_interval = 1
        new_ease     = ease_factor
    else:
        new_reps = reps + 1
        if reps == 0:
            new_interval = 1
        elif reps == 1:
            new_interval = 6
        else:
            new_interval = round(interval * ease_factor)
        new_ease = ease_factor + (0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02))
        if new_ease < 1.3:
            new_ease = 1.3

    due = date.today() + timedelta(days=new_interval)
    return new_interval, new_ease, new_reps, due


def register(app):

    @app.route('/api/flashcards/config', methods=['GET'])
    def fc_config_get():
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM public.flashcard_config WHERE user_id = %s", (USER_ID,))
                config_rows = cur.fetchall()

                cur.execute("""
                    SELECT subject, COUNT(*) as count
                    FROM public.flashcard_reviews
                    WHERE user_id = %s
                      AND reviewed_at >= date_trunc('day', NOW() AT TIME ZONE 'America/Denver')
                                        AT TIME ZONE 'America/Denver'
                    GROUP BY subject
                """, (USER_ID,))
                today_rows = cur.fetchall()

                cur.execute("SELECT * FROM public.flashcard_file_config WHERE user_id = %s", (USER_ID,))
                file_config_rows = cur.fetchall()

                cur.execute("SELECT subject, filename FROM public.flashcard_files WHERE user_id = %s", (USER_ID,))
                db_files_rows = cur.fetchall()

            config_map = {r['subject']: r for r in config_rows}
            count_map  = {r['subject']: int(r['count']) for r in today_rows}

            file_config_map = {}
            for r in file_config_rows:
                file_config_map.setdefault(r['subject'], {})[r['filename']] = r['enabled']

            db_files_by_subject = {}
            for r in db_files_rows:
                db_files_by_subject.setdefault(r['subject'], []).append(r['filename'])

            result = []
            for subject in SUBJECTS:
                fs_names = get_fs_files(subject)
                db_names = db_files_by_subject.get(subject, [])
                all_names = list(dict.fromkeys(fs_names + db_names))

                fc_map = file_config_map.get(subject, {})
                files = [{
                    'filename': fn,
                    'enabled':  fc_map.get(fn, True),
                    'source':   'filesystem' if fn in fs_names else 'uploaded',
                } for fn in all_names]

                cfg = config_map.get(subject)
                result.append({
                    'subject':     subject,
                    'enabled':     cfg['enabled']    if cfg else True,
                    'daily_goal':  cfg['daily_goal'] if cfg else 30,
                    'today_count': count_map.get(subject, 0),
                    'files':       files,
                })

            return jsonify(result)
        finally:
            conn.close()

    @app.route('/api/flashcards/config', methods=['PATCH'])
    def fc_config_patch():
        data    = request.json
        subject = data.get('subject')
        if subject not in SUBJECTS:
            return jsonify({'error': 'Invalid subject'}), 400

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if 'filename' in data:
                    cur.execute("""
                        INSERT INTO public.flashcard_file_config (user_id, subject, filename, enabled)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id, subject, filename)
                        DO UPDATE SET enabled = EXCLUDED.enabled
                    """, (USER_ID, subject, data['filename'], data.get('file_enabled', True)))
                else:
                    cur.execute("""
                        INSERT INTO public.flashcard_config (user_id, subject, enabled, daily_goal)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id, subject)
                        DO UPDATE SET enabled = EXCLUDED.enabled, daily_goal = EXCLUDED.daily_goal
                    """, (USER_ID, subject, data.get('enabled', True), data.get('daily_goal', 30)))
            conn.commit()
            return jsonify({'ok': True})
        finally:
            conn.close()

    @app.route('/api/flashcards/config', methods=['DELETE'])
    def fc_config_delete():
        data     = request.json
        subject  = data.get('subject')
        filename = data.get('filename')
        if subject not in SUBJECTS:
            return jsonify({'error': 'Invalid subject'}), 400
        if not filename:
            return jsonify({'error': 'filename is required'}), 400

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.flashcard_files WHERE user_id=%s AND subject=%s AND filename=%s",
                    (USER_ID, subject, filename))
                cur.execute(
                    "DELETE FROM public.flashcard_file_config WHERE user_id=%s AND subject=%s AND filename=%s",
                    (USER_ID, subject, filename))
            conn.commit()
            return jsonify({'ok': True})
        finally:
            conn.close()

    @app.route('/api/flashcards/upload', methods=['POST'])
    def fc_upload():
        data     = request.json
        subject  = data.get('subject')
        filename = data.get('filename')
        content  = data.get('content')

        if subject not in SUBJECTS:
            return jsonify({'error': 'Invalid subject'}), 400
        if not filename:
            return jsonify({'error': 'filename is required'}), 400
        if not content:
            return jsonify({'error': 'content is required'}), 400

        processed, added = add_ids(content)

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.flashcard_files (user_id, subject, filename, content, uploaded_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id, subject, filename)
                    DO UPDATE SET content = EXCLUDED.content, uploaded_at = NOW()
                """, (USER_ID, subject, filename, processed))
            conn.commit()
            return jsonify({'ok': True, 'added': added, 'filename': filename})
        finally:
            conn.close()

    @app.route('/api/flashcards/review', methods=['POST'])
    def fc_review():
        data    = request.json
        card_id = data.get('card_id')
        subject = data.get('subject')
        rating  = data.get('rating')

        if not card_id or rating is None:
            return jsonify({'error': 'card_id and rating are required'}), 400
        if not (0 <= rating <= 5):
            return jsonify({'error': 'rating must be 0-5'}), 400

        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM public.flashcard_reviews WHERE user_id=%s AND card_id=%s",
                    (USER_ID, card_id))
                prev = cur.fetchone()

                prev_interval = prev['interval']    if prev else 0
                prev_ease     = float(prev['ease_factor']) if prev else 2.5
                prev_reps     = prev['reps']         if prev else 0
                prev_seen     = prev['seen']          if prev else 0

                interval, ease_factor, reps, due = sm2(rating, prev_interval, prev_ease, prev_reps)

                cur.execute("""
                    INSERT INTO public.flashcard_reviews
                      (user_id, card_id, subject, interval, ease_factor, reps, due_date, seen, reviewed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id, card_id) DO UPDATE SET
                      subject = EXCLUDED.subject,
                      interval = EXCLUDED.interval,
                      ease_factor = EXCLUDED.ease_factor,
                      reps = EXCLUDED.reps,
                      due_date = EXCLUDED.due_date,
                      seen = flashcard_reviews.seen + 1,
                      reviewed_at = NOW()
                """, (USER_ID, card_id, subject, interval, ease_factor, reps,
                      due.isoformat(), prev_seen + 1))
            conn.commit()
            return jsonify({'ok': True, 'interval': interval, 'ease_factor': ease_factor,
                            'reps': reps, 'due_date': due.isoformat()})
        finally:
            conn.close()

    @app.route('/api/flashcards/file', methods=['GET'])
    def fc_file_get():
        subject  = request.args.get('subject')
        filename = request.args.get('filename')
        if subject not in SUBJECTS:
            return jsonify({'error': 'Invalid subject'}), 400
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT content FROM public.flashcard_files WHERE user_id=%s AND subject=%s AND filename=%s",
                    (USER_ID, subject, filename))
                row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Not found'}), 404
            return jsonify({'content': row['content']})
        finally:
            conn.close()

    @app.route('/api/flashcards/cards', methods=['GET'])
    def fc_cards():
        subject = request.args.get('subject')
        if subject not in SUBJECTS:
            return jsonify({'error': 'Invalid subject'}), 400

        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT filename, enabled FROM public.flashcard_file_config
                    WHERE user_id=%s AND subject=%s
                """, (USER_ID, subject))
                file_cfg = {r['filename']: r['enabled'] for r in cur.fetchall()}

                cur.execute("""
                    SELECT f.filename, f.content
                    FROM public.flashcard_files f
                    LEFT JOIN public.flashcard_file_config fc
                      ON fc.user_id=f.user_id AND fc.subject=f.subject AND fc.filename=f.filename
                    WHERE f.user_id=%s AND f.subject=%s AND COALESCE(fc.enabled, true)=true
                """, (USER_ID, subject))
                db_files = list(cur.fetchall())

                cur.execute("""
                    SELECT card_id, interval, ease_factor, reps, due_date, seen
                    FROM public.flashcard_reviews WHERE user_id=%s AND subject=%s
                """, (USER_ID, subject))
                reviews = {r['card_id']: r for r in cur.fetchall()}

            today     = date.today()
            all_cards = []
            seen_ids  = set()

            def enrich(card):
                review = reviews.get(card['id'])
                if review:
                    due = review['due_date']
                    if hasattr(due, 'date'):
                        due = due.date()
                    card['due_date'] = due.isoformat()
                    card['reps']     = review['reps']
                    card['seen']     = review['seen']
                    card['is_due']   = due <= today
                else:
                    card['due_date'] = None
                    card['reps']     = 0
                    card['seen']     = 0
                    card['is_due']   = True
                return card

            for row in db_files:
                for card in parse_cards(row['content'], subject, row['filename']):
                    if card['id'] not in seen_ids:
                        seen_ids.add(card['id'])
                        all_cards.append(enrich(card))

            fs_dir = os.path.join(config.FC_DIR, subject)
            if os.path.isdir(fs_dir):
                for fname in os.listdir(fs_dir):
                    if not fname.endswith('.md') or not file_cfg.get(fname, True):
                        continue
                    with open(os.path.join(fs_dir, fname)) as f:
                        content_fs = f.read()
                    for card in parse_cards(content_fs, subject, fname):
                        if card['id'] not in seen_ids:
                            seen_ids.add(card['id'])
                            all_cards.append(enrich(card))

            due_cards = [c for c in all_cards if c['is_due']]
            return jsonify({
                'due':   due_cards,
                'all':   all_cards,
                'stats': {
                    'due':   len(due_cards),
                    'seen':  sum(1 for c in all_cards if c['seen'] > 0),
                    'total': len(all_cards),
                },
            })
        finally:
            conn.close()
