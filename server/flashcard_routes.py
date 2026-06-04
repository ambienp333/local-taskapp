import os
import re
import json
import shutil
import secrets
import threading
from flask import request, jsonify
from datetime import date, datetime, timedelta
import config

SUBJECT_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,49}$')


# ---- Local storage helpers ----

def load_reviews():
    if not os.path.exists(config.FC_REVIEWS_FILE):
        return {}
    with open(config.FC_REVIEWS_FILE, 'r') as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def save_reviews(data):
    os.makedirs(config.FC_DIR, exist_ok=True)
    with open(config.FC_REVIEWS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def load_fc_config():
    if not os.path.exists(config.FC_CONFIG_FILE):
        return {}
    with open(config.FC_CONFIG_FILE, 'r') as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def save_fc_config(cfg):
    os.makedirs(config.FC_DIR, exist_ok=True)
    with open(config.FC_CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)


# ---- Async sync pushes ----

def _push_review_async(card_id, record):
    def _run():
        try:
            import sync_routes
            sync_routes.push_fc_review(card_id, record)
        except Exception as e:
            print(f'[sync] fc review push failed: {e}')
    threading.Thread(target=_run, daemon=True).start()


def _push_file_async(subject, filename, content):
    def _run():
        try:
            import sync_routes
            sync_routes.push_fc_file(subject, filename, content)
        except Exception as e:
            print(f'[sync] fc file push failed: {e}')
    threading.Thread(target=_run, daemon=True).start()


def _push_config_async(subject, cfg):
    def _run():
        try:
            import sync_routes
            sync_routes.push_fc_config(subject, cfg)
        except Exception as e:
            print(f'[sync] fc config push failed: {e}')
    threading.Thread(target=_run, daemon=True).start()


# ---- Filesystem helpers ----

def _fs_subjects():
    if not os.path.isdir(config.FC_DIR):
        return []
    return sorted([d for d in os.listdir(config.FC_DIR)
                   if os.path.isdir(os.path.join(config.FC_DIR, d))])


def get_fs_files(subject):
    d = os.path.join(config.FC_DIR, subject)
    if not os.path.isdir(d):
        return []
    return sorted([f for f in os.listdir(d) if f.endswith('.md')])


# ---- Card parsing / ID stamping ----

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


# ---- SM-2 ----

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

    @app.route('/api/flashcards/resend', methods=['POST'])
    def fc_resend():
        import sync_routes
        cfg     = load_fc_config()
        reviews = load_reviews()
        counts  = {'config': 0, 'files': 0, 'reviews': 0, 'errors': 0}

        for subject, sub_cfg in cfg.items():
            try:
                sync_routes.push_fc_config(subject, sub_cfg)
                counts['config'] += 1
            except Exception as e:
                print(f'[resend] config {subject}: {e}')
                counts['errors'] += 1

        if os.path.isdir(config.FC_DIR):
            for subject in os.listdir(config.FC_DIR):
                subj_dir = os.path.join(config.FC_DIR, subject)
                if not os.path.isdir(subj_dir):
                    continue
                for filename in os.listdir(subj_dir):
                    if not filename.endswith('.md'):
                        continue
                    try:
                        with open(os.path.join(subj_dir, filename), 'r') as f:
                            content = f.read()
                        sync_routes.push_fc_file(subject, filename, content)
                        counts['files'] += 1
                    except Exception as e:
                        print(f'[resend] file {subject}/{filename}: {e}')
                        counts['errors'] += 1

        for card_id, record in reviews.items():
            try:
                sync_routes.push_fc_review(card_id, record)
                counts['reviews'] += 1
            except Exception as e:
                print(f'[resend] review {card_id}: {e}')
                counts['errors'] += 1

        return jsonify({'ok': True, **counts})

    @app.route('/api/flashcards/card', methods=['POST'])
    def fc_card_add():
        data     = request.json
        subject  = data.get('subject')
        filename = data.get('filename')
        front    = (data.get('front') or '').strip()
        back     = (data.get('back') or '').strip()

        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject'}), 400
        if not filename:
            return jsonify({'error': 'filename required'}), 400
        if not front:
            return jsonify({'error': 'front required'}), 400

        card_id  = secrets.token_hex(4)
        new_card = f'\n<!-- id: {card_id} -->\n- {front} → {back}\n'
        fs_path  = os.path.join(config.FC_DIR, subject, filename)
        if not os.path.isfile(fs_path):
            return jsonify({'error': 'File not found'}), 404
        with open(fs_path, 'r') as f:
            content = f.read()
        updated = content.rstrip('\n') + new_card
        with open(fs_path, 'w') as f:
            f.write(updated)

        _push_file_async(subject, filename, updated)
        return jsonify({'ok': True, 'id': card_id})

    @app.route('/api/flashcards/subjects', methods=['POST'])
    def fc_subject_create():
        subject = (request.json.get('subject') or '').strip()
        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject name (alphanumeric, _ and - only)'}), 400
        os.makedirs(os.path.join(config.FC_DIR, subject), exist_ok=True)
        cfg = load_fc_config()
        if subject not in cfg:
            cfg[subject] = {'enabled': True, 'daily_goal': 30, 'files': {}}
            save_fc_config(cfg)
        _push_config_async(subject, cfg[subject])
        return jsonify({'ok': True, 'subject': subject}), 201

    @app.route('/api/flashcards/subjects/<subject>', methods=['PATCH'])
    def fc_subject_rename(subject):
        if not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject name'}), 400
        new_name = (request.json.get('name') or '').strip()
        if not new_name or not SUBJECT_RE.match(new_name):
            return jsonify({'error': 'Invalid new subject name (alphanumeric, _ and - only)'}), 400
        if new_name == subject:
            return jsonify({'ok': True})

        old_dir = os.path.join(config.FC_DIR, subject)
        new_dir = os.path.join(config.FC_DIR, new_name)
        if os.path.isdir(old_dir):
            os.rename(old_dir, new_dir)

        cfg = load_fc_config()
        cfg[new_name] = cfg.pop(subject, {'enabled': True, 'daily_goal': 30, 'files': {}})
        save_fc_config(cfg)

        reviews = load_reviews()
        for r in reviews.values():
            if r.get('subject') == subject:
                r['subject'] = new_name
        save_reviews(reviews)

        _push_config_async(new_name, cfg[new_name])
        return jsonify({'ok': True, 'subject': new_name})

    @app.route('/api/flashcards/subjects/<subject>', methods=['DELETE'])
    def fc_subject_delete(subject):
        if not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject name'}), 400
        subj_dir = os.path.join(config.FC_DIR, subject)
        if os.path.isdir(subj_dir):
            shutil.rmtree(subj_dir)
        cfg = load_fc_config()
        cfg.pop(subject, None)
        save_fc_config(cfg)
        reviews = load_reviews()
        pruned = {k: v for k, v in reviews.items() if v.get('subject') != subject}
        save_reviews(pruned)
        return jsonify({'ok': True})

    @app.route('/api/flashcards/config', methods=['GET'])
    def fc_config_get():
        cfg      = load_fc_config()
        reviews  = load_reviews()
        subjects = set(_fs_subjects()) | set(cfg.keys())

        today_str = date.today().isoformat()
        count_map = {}
        for r in reviews.values():
            subj = r.get('subject', '')
            if (r.get('reviewed_at') or '')[:10] == today_str:
                count_map[subj] = count_map.get(subj, 0) + 1

        result = []
        for subject in sorted(subjects):
            fs_names = get_fs_files(subject)
            sub_cfg  = cfg.get(subject, {})
            file_cfg = sub_cfg.get('files', {})
            files    = [{'filename': fn, 'enabled': file_cfg.get(fn, True)} for fn in fs_names]
            result.append({
                'subject':     subject,
                'enabled':     sub_cfg.get('enabled', True),
                'daily_goal':  sub_cfg.get('daily_goal', 30),
                'today_count': count_map.get(subject, 0),
                'files':       files,
            })
        return jsonify(result)

    @app.route('/api/flashcards/config', methods=['PATCH'])
    def fc_config_patch():
        data    = request.json
        subject = data.get('subject')
        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject'}), 400

        cfg = load_fc_config()
        sub = cfg.setdefault(subject, {'enabled': True, 'daily_goal': 30, 'files': {}})

        if 'filename' in data:
            sub.setdefault('files', {})[data['filename']] = data.get('file_enabled', True)
        else:
            if 'enabled'    in data: sub['enabled']    = data['enabled']
            if 'daily_goal' in data: sub['daily_goal'] = data['daily_goal']

        save_fc_config(cfg)
        _push_config_async(subject, sub)
        return jsonify({'ok': True})

    @app.route('/api/flashcards/config', methods=['DELETE'])
    def fc_config_delete():
        data     = request.json
        subject  = data.get('subject')
        filename = data.get('filename')
        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject'}), 400
        if not filename:
            return jsonify({'error': 'filename is required'}), 400

        fs_path = os.path.join(config.FC_DIR, subject, filename)
        if os.path.isfile(fs_path):
            os.remove(fs_path)

        cfg = load_fc_config()
        cfg.get(subject, {}).get('files', {}).pop(filename, None)
        save_fc_config(cfg)
        return jsonify({'ok': True})

    @app.route('/api/flashcards/upload', methods=['POST'])
    def fc_upload():
        data     = request.json
        subject  = data.get('subject')
        filename = data.get('filename')
        content  = data.get('content')

        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject'}), 400
        if not filename:
            return jsonify({'error': 'filename is required'}), 400
        if not content:
            return jsonify({'error': 'content is required'}), 400

        processed, added = add_ids(content)
        subj_dir = os.path.join(config.FC_DIR, subject)
        os.makedirs(subj_dir, exist_ok=True)
        with open(os.path.join(subj_dir, filename), 'w') as f:
            f.write(processed)

        _push_file_async(subject, filename, processed)
        return jsonify({'ok': True, 'added': added, 'filename': filename})

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

        reviews  = load_reviews()
        prev     = reviews.get(card_id, {})

        interval, ease_factor, reps, due = sm2(
            rating,
            prev.get('interval',    0),
            float(prev.get('ease_factor', 2.5)),
            prev.get('reps',        0),
        )

        record = {
            'subject':     subject,
            'interval':    interval,
            'ease_factor': ease_factor,
            'reps':        reps,
            'due_date':    due.isoformat(),
            'seen':        prev.get('seen', 0) + 1,
            'reviewed_at': datetime.now().isoformat(timespec='seconds'),
        }
        reviews[card_id] = record
        save_reviews(reviews)

        _push_review_async(card_id, record)
        return jsonify({'ok': True, 'interval': interval, 'ease_factor': ease_factor,
                        'reps': reps, 'due_date': due.isoformat()})

    @app.route('/api/flashcards/file', methods=['GET'])
    def fc_file_get():
        subject  = request.args.get('subject')
        filename = request.args.get('filename')
        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject'}), 400
        fs_path = os.path.join(config.FC_DIR, subject, filename)
        if not os.path.isfile(fs_path):
            return jsonify({'error': 'Not found'}), 404
        with open(fs_path, 'r') as f:
            content = f.read()
        return jsonify({'content': content})

    @app.route('/api/flashcards/cards', methods=['GET'])
    def fc_cards():
        subject = request.args.get('subject')
        if not subject or not SUBJECT_RE.match(subject):
            return jsonify({'error': 'Invalid subject'}), 400

        cfg      = load_fc_config()
        reviews  = load_reviews()
        file_cfg = cfg.get(subject, {}).get('files', {})
        today    = date.today()

        all_cards = []
        seen_ids  = set()

        def enrich(card):
            rev = reviews.get(card['id'])
            if rev:
                due = date.fromisoformat(rev['due_date'])
                card['due_date'] = rev['due_date']
                card['reps']     = rev['reps']
                card['seen']     = rev['seen']
                card['is_due']   = due <= today
            else:
                card['due_date'] = None
                card['reps']     = 0
                card['seen']     = 0
                card['is_due']   = True
            return card

        fs_dir = os.path.join(config.FC_DIR, subject)
        if os.path.isdir(fs_dir):
            for fname in sorted(os.listdir(fs_dir)):
                if not fname.endswith('.md') or not file_cfg.get(fname, True):
                    continue
                with open(os.path.join(fs_dir, fname)) as f:
                    content = f.read()
                for card in parse_cards(content, subject, fname):
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
