import os
import time
import threading
from flask import request, jsonify
import psycopg2
import psycopg2.extras
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import keyring
import config
import store
import journal_routes

USER_ID        = 'abel_main_user'
KEYRING_SVC    = 'taskapp'


def get_conn():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=5432,
        database='postgres',
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        sslmode='require',
        connect_timeout=10
    )


# ---- Key management ----

def _get_key(name):
    key_hex = keyring.get_password(KEYRING_SVC, name)
    if not key_hex:
        raise RuntimeError(
            f'[sync] key "{name}" not found in keyring. '
            'Run generate_keys.py (M1) or load_keys.py (M2) first.'
        )
    return key_hex


def _encrypt_key():
    return _get_key('encrypt_key')


def _decrypt_key():
    return _get_key('decrypt_key')


# ---- Crypto ----

def encrypt_payload(plaintext):
    key   = bytes.fromhex(_encrypt_key())
    nonce = os.urandom(12)
    ct    = AESGCM(key).encrypt(nonce, plaintext.encode('utf-8'), None)
    return nonce.hex(), ct.hex()


def decrypt_payload(ciphertext_hex, nonce_hex):
    key    = bytes.fromhex(_decrypt_key())
    nonce  = bytes.fromhex(nonce_hex)
    ct     = bytes.fromhex(ciphertext_hex)
    return AESGCM(key).decrypt(nonce, ct, None).decode('utf-8')


# ---- Helpers ----

def task_to_block(task):
    lines = [f'{task["name"]}[{task["id"]}]']
    lines.append(','.join(task['modifiers']))
    if task.get('notes', '').strip():
        lines.append(task['notes'])
    return '\n'.join(lines)


def _log_number_from_id(task_id):
    try:
        return int(task_id.split('(')[-1].rstrip(')'))
    except (ValueError, IndexError):
        return 0


def _date_from_id(task_id):
    date_part = task_id.split('(')[0]
    parts = date_part.split('/')
    if len(parts) == 3:
        mo, d, y = parts
        return f"{mo}/{d}/20{y}"
    return date_part


# ---- Core sync ----

def push_completion(task, date_slug):
    """Push a completion event. date_slug format: M-D-YY"""
    block     = f'COMPLETION\n{date_slug}\n{task_to_block(task)}'
    nonce_hex, ct_hex = encrypt_payload(block)
    task_id   = task['id']
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ledger_upsert(cur, task_id, ct_hex, nonce_hex,
                           _date_from_id(task_id), _log_number_from_id(task_id))
        conn.commit()
    finally:
        conn.close()


def push_task(task):
    if 'fc' in task.get('modifiers', []):
        return
    nonce_hex, ct_hex = encrypt_payload(task_to_block(task))
    task_id = task['id']
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ledger_upsert(cur, task_id, ct_hex, nonce_hex,
                           _date_from_id(task_id), _log_number_from_id(task_id))
        conn.commit()
    finally:
        conn.close()


def _ledger_upsert(cur, task_id, ct_hex, nonce_hex, task_date='', log_number=0):
    cur.execute("""
        INSERT INTO public.sync_temp_ledger
            (user_id, task_id, machine_id, payload, nonce, sent_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id, task_id) DO UPDATE SET
            payload = EXCLUDED.payload,
            nonce   = EXCLUDED.nonce,
            sent_at = NOW()
    """, (USER_ID, task_id, config.MACHINE_ID, ct_hex, nonce_hex))
    cur.execute("""
        INSERT INTO public.sync_permanent_ledger
            (user_id, task_id, machine_id, task_date, log_number, sent_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id, task_id) DO NOTHING
    """, (USER_ID, task_id, config.MACHINE_ID, task_date, log_number))


def push_fc_review(card_id, record):
    payload = (
        f'FC_REVIEW\n{card_id}\n{record["subject"]}\n{record["interval"]}\n'
        f'{record["ease_factor"]}\n{record["reps"]}\n{record["due_date"]}\n'
        f'{record["seen"]}\n{record["reviewed_at"]}'
    )
    nonce_hex, ct_hex = encrypt_payload(payload)
    task_date = (record.get('reviewed_at') or '')[:10]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ledger_upsert(cur, f'fc_review:{card_id}', ct_hex, nonce_hex, task_date)
        conn.commit()
    finally:
        conn.close()


def push_fc_file(subject, filename, content):
    payload   = f'FC_FILE\n{subject}\n{filename}\n{content}'
    nonce_hex, ct_hex = encrypt_payload(payload)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ledger_upsert(cur, f'fc_file:{subject}:{filename}', ct_hex, nonce_hex)
        conn.commit()
    finally:
        conn.close()


def push_fc_config(subject, cfg):
    import json as _json
    payload   = f'FC_CONFIG\n{subject}\n{_json.dumps(cfg)}'
    nonce_hex, ct_hex = encrypt_payload(payload)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ledger_upsert(cur, f'fc_config:{subject}', ct_hex, nonce_hex)
        conn.commit()
    finally:
        conn.close()


def _apply_incoming(rows):
    if not rows:
        return {'added': [], 'updated': []}

    temp, daily  = store.get_all_tasks()
    existing_ids = {t['id'] for t in temp + daily}
    new_temp     = list(temp)
    new_daily    = list(daily)
    added        = []
    updated      = []

    for row in rows:
        nonce_hex = row.get('nonce')
        try:
            plaintext = decrypt_payload(row['payload'], nonce_hex) if nonce_hex else row['payload']
        except Exception as e:
            print(f'[sync] decrypt failed for {row["task_id"]}: {e}')
            continue

        lines = plaintext.split('\n')
        print(f'[sync] row {row["task_id"]} machine={row["machine_id"]} line0={lines[0]!r} nlines={len(lines)}')

        if lines[0] == 'FC_REVIEW' and len(lines) >= 9:
            try:
                import flashcard_routes as _fc
                card_id = lines[1]
                record  = {
                    'subject':     lines[2],
                    'interval':    int(lines[3]),
                    'ease_factor': float(lines[4]),
                    'reps':        int(lines[5]),
                    'due_date':    lines[6],
                    'seen':        int(lines[7]),
                    'reviewed_at': lines[8],
                }
                reviews  = _fc.load_reviews()
                existing = reviews.get(card_id)
                if not existing or record['reviewed_at'] > existing.get('reviewed_at', ''):
                    reviews[card_id] = record
                    _fc.save_reviews(reviews)
            except Exception as e:
                print(f'[sync] FC_REVIEW apply failed: {e}')
            updated.append(row['task_id'])
            continue

        if lines[0] == 'FC_FILE' and len(lines) >= 3:
            try:
                import flashcard_routes as _fc
                subject  = lines[1]
                filename = lines[2]
                content  = '\n'.join(lines[3:])
                subj_dir = os.path.join(config.FC_DIR, subject)
                os.makedirs(subj_dir, exist_ok=True)
                with open(os.path.join(subj_dir, filename), 'w') as _f:
                    _f.write(content)
            except Exception as e:
                print(f'[sync] FC_FILE apply failed: {e}')
            added.append(row['task_id'])
            continue

        if lines[0] == 'FC_CONFIG' and len(lines) >= 3:
            try:
                import json as _json, flashcard_routes as _fc
                subject  = lines[1]
                incoming = _json.loads(lines[2])
                cfg      = _fc.load_fc_config()
                cfg[subject] = incoming
                _fc.save_fc_config(cfg)
            except Exception as e:
                print(f'[sync] FC_CONFIG apply failed: {e}')
            updated.append(row['task_id'])
            continue

        if lines[0] == 'COMPLETION' and len(lines) >= 3:
            date_slug = lines[1].strip()
            task      = store._parse_block(lines[2:])
            if not task:
                continue
            new_temp  = [t for t in new_temp  if t['id'] != task['id']]
            new_daily = [t for t in new_daily if t['id'] != task['id']]
            existing_ids.discard(task['id'])
            try:
                jdata  = journal_routes.load_journal(date_slug)
                jtasks = jdata.get('tasks', [])
                if not any(t['id'] == task['id'] for t in jtasks):
                    jtasks.append({
                        'id':        task['id'],
                        'name':      task['name'],
                        'modifiers': task.get('modifiers', []),
                        'notes':     task.get('notes', ''),
                        'completed': True,
                    })
                    journal_routes.save_journal(date_slug, {'tasks': jtasks})
            except Exception as e:
                print(f'[sync] journal write failed for {task["id"]}: {e}')
            updated.append(task['id'])
            continue

        task = store._parse_block(lines)
        if not task:
            continue

        if task['id'] in existing_ids:
            new_temp  = [t for t in new_temp  if t['id'] != task['id']]
            new_daily = [t for t in new_daily if t['id'] != task['id']]
            if 'da' in task.get('modifiers', []):
                new_daily.append(task)
            else:
                new_temp.append(task)
            updated.append(task['id'])
        else:
            existing_ids.add(task['id'])
            added.append(task['id'])
            if 'da' in task.get('modifiers', []):
                new_daily.append(task)
            else:
                new_temp.append(task)

    if added or updated:
        store.write_tasks(config.TEMP_FILE,  new_temp)
        store.write_tasks(config.DAILY_FILE, new_daily)

    return {'added': added, 'updated': updated}


def push_journal(date_slug, data):
    import json
    plaintext         = json.dumps(data, ensure_ascii=False)
    nonce_hex, ct_hex = encrypt_payload(plaintext)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.sync_journal_ledger
                    (user_id, date_slug, machine_id, payload, nonce, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id, date_slug, machine_id) DO UPDATE SET
                    payload    = EXCLUDED.payload,
                    nonce      = EXCLUDED.nonce,
                    updated_at = NOW()
            """, (USER_ID, date_slug, config.MACHINE_ID, ct_hex, nonce_hex))
        conn.commit()
    finally:
        conn.close()


def _merge_journal(local, remote):
    by_id = {t['id']: dict(t) for t in local.get('tasks', [])}
    for t in remote.get('tasks', []):
        if t['id'] in by_id:
            if t.get('completed'):
                by_id[t['id']]['completed'] = True
            if not by_id[t['id']].get('notes') and t.get('notes'):
                by_id[t['id']]['notes'] = t['notes']
        else:
            by_id[t['id']] = dict(t)
    return {'tasks': list(by_id.values())}


def _sync_journals():
    import json
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT date_slug, payload, nonce FROM public.sync_journal_ledger
                WHERE user_id = %s AND machine_id != %s
            """, (USER_ID, config.MACHINE_ID))
            rows = list(cur.fetchall())
    finally:
        conn.close()

    for row in rows:
        try:
            plaintext   = decrypt_payload(row['payload'], row['nonce'])
            remote_data = json.loads(plaintext)
        except Exception as e:
            print(f'[sync] journal decrypt failed for {row["date_slug"]}: {e}')
            continue

        date_slug  = row['date_slug']
        local_data = journal_routes.load_journal(date_slug)
        merged     = _merge_journal(local_data, remote_data)

        if merged['tasks'] != local_data.get('tasks', []):
            journal_routes.save_journal(date_slug, merged)
            try:
                push_journal(date_slug, merged)
            except Exception as e:
                print(f'[sync] journal push-back failed for {date_slug}: {e}')


def do_sync():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT task_id, machine_id, payload, nonce FROM public.sync_temp_ledger
                WHERE user_id = %s AND machine_id != %s
            """, (USER_ID, config.MACHINE_ID))
            rows = list(cur.fetchall())

        task_result = {'synced': 0, 'skipped': 0}
        if rows:
            result    = _apply_incoming(rows)
            added_ids = result['added']
            upd_ids   = result['updated']

            task_ids = [r['task_id'] for r in rows]
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.sync_temp_ledger
                    WHERE user_id = %s AND task_id = ANY(%s) AND machine_id != %s
                """, (USER_ID, task_ids, config.MACHINE_ID))
            conn.commit()

            total_changed = len(added_ids) + len(upd_ids)
            task_result = {'synced': total_changed, 'skipped': len(rows) - total_changed}
    finally:
        conn.close()

    try:
        _sync_journals()
    except Exception as e:
        print(f'[sync] journal sync failed: {e}')

    return task_result


def _check_tor():
    if os.environ.get('TORSOCKS_CONF_FILE'):
        print('[sync] Tor routing active via torsocks')
    else:
        print('[sync] WARNING: not running via start.sh — Supabase connections are NOT anonymised')


def _bg_loop():
    _check_tor()
    try:
        do_sync()
    except Exception as e:
        print(f'[sync] startup sync failed: {e}')
    while True:
        time.sleep(30 * 60)
        try:
            do_sync()
        except Exception as e:
            print(f'[sync] background sync failed: {e}')


def register(app):

    @app.route('/api/sync/push', methods=['POST'])
    def sync_push():
        task = request.json.get('task')
        if not task:
            return jsonify({'error': 'task required'}), 400
        try:
            push_task(task)
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/sync/check', methods=['POST'])
    def sync_check():
        try:
            result = do_sync()
            return jsonify({'ok': True, **result})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/sync/status', methods=['GET'])
    def sync_status():
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT COUNT(*) as count FROM public.sync_temp_ledger
                    WHERE user_id = %s AND machine_id != %s
                """, (USER_ID, config.MACHINE_ID))
                pending = cur.fetchone()['count']

                cur.execute("""
                    SELECT COUNT(*) as count FROM public.sync_permanent_ledger
                    WHERE user_id = %s
                """, (USER_ID,))
                total_logged = cur.fetchone()['count']

            return jsonify({'pending': int(pending), 'total_logged': int(total_logged)})
        finally:
            conn.close()

    @app.route('/api/sync/resend', methods=['POST'])
    def sync_resend():
        date_str = request.json.get('date')
        if not date_str:
            return jsonify({'error': 'date required'}), 400

        temp, daily = store.get_all_tasks()
        prefix      = f"{date_str}({config.MACHINE_ID})("
        day_tasks   = [t for t in temp + daily if t['id'].startswith(prefix)]

        if not day_tasks:
            return jsonify({'ok': True, 'sent': 0})

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                for task in day_tasks:
                    nonce_hex, ct_hex = encrypt_payload(task_to_block(task))
                    cur.execute("""
                        INSERT INTO public.sync_temp_ledger
                            (user_id, task_id, machine_id, payload, nonce, sent_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (user_id, task_id) DO UPDATE SET
                            payload = EXCLUDED.payload,
                            nonce   = EXCLUDED.nonce,
                            sent_at = NOW()
                    """, (USER_ID, task['id'], config.MACHINE_ID, ct_hex, nonce_hex))
            conn.commit()
            return jsonify({'ok': True, 'sent': len(day_tasks)})
        finally:
            conn.close()

    if os.environ.get('WERKZEUG_RUN_MAIN', 'true') == 'true':
        t = threading.Thread(target=_bg_loop, daemon=True)
        t.start()
