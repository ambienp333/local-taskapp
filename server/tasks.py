from flask import request, jsonify
from datetime import datetime, timedelta
import threading
import time
import store
import config
import journal_routes


def _push_async(task):
    def _run():
        try:
            import sync_routes
            sync_routes.push_task(task)
        except Exception as e:
            print(f'[sync] push failed: {e}')
    threading.Thread(target=_run, daemon=True).start()


def _push_completion_async(task, date_slug):
    def _run():
        try:
            import sync_routes
            sync_routes.push_completion(task, date_slug)
        except Exception as e:
            print(f'[sync] completion push failed: {e}')
    threading.Thread(target=_run, daemon=True).start()


def _fail_at(task):
    """Returns the datetime at which a da task should auto-fail (4am next day)."""
    try:
        date_part = task['id'].split('(')[0]
        mo, d, y  = date_part.split('/')
        created   = datetime(2000 + int(y), int(mo), int(d))
        return created + timedelta(days=1, hours=4)
    except Exception:
        return None


def _run_auto_fail():
    while True:
        time.sleep(5 * 60)
        try:
            _do_auto_fail()
        except Exception as e:
            print(f'[auto-fail] error: {e}')


def _do_auto_fail():
    _, daily = store.get_all_tasks()
    now      = datetime.now()
    failed   = []
    surviving = []

    for task in daily:
        if 'fc' in task.get('modifiers', []):
            surviving.append(task)
            continue
        threshold = _fail_at(task)
        if threshold and now >= threshold:
            failed.append(task)
        else:
            surviving.append(task)

    if not failed:
        return

    for task in failed:
        date_part = task['id'].split('(')[0]
        mo, d, y  = date_part.split('/')
        date_slug = f"{mo}-{d}-{y}"

        existing  = journal_routes.load_journal(date_slug)
        jtasks    = existing.get('tasks', [])
        known_ids = {t['id'] for t in jtasks}

        if task['id'] not in known_ids:
            jtasks.append({
                'id':        task['id'],
                'name':      task['name'] + ' (failed)',
                'modifiers': task.get('modifiers', []),
                'notes':     task.get('notes', ''),
                'completed': True,
            })
            journal_routes.save_journal(date_slug, {'tasks': jtasks})

    store.write_tasks(config.DAILY_FILE, surviving)
    print(f'[auto-fail] failed {len(failed)} task(s)')


def is_expired(task):
    for mod in task['modifiers']:
        if mod.startswith('te'):
            try:
                days = int(mod[2:])
                date_part = task['id'].split('(')[0]
                mo, d, y = date_part.split('/')
                created = datetime(2000 + int(y), int(mo), int(d))
                if datetime.now() >= created + timedelta(days=days):
                    return True
            except (ValueError, IndexError):
                pass
    return False


def register(app):
    threading.Thread(target=_run_auto_fail, daemon=True).start()

    @app.route('/api/tasks', methods=['GET'])
    def get_tasks():
        temp, daily = store.get_all_tasks()
        return jsonify({
            'active': [t for t in temp  if not is_expired(t)],
            'daily':  [t for t in daily if not is_expired(t)],
        })

    @app.route('/api/tasks', methods=['POST'])
    def create_task():
        data = request.json
        name = data.get('name', '').strip()
        modifiers = [m.lower().strip() for m in data.get('modifiers', [])]
        if not name:
            return jsonify({'error': 'name required'}), 400

        temp, daily = store.get_all_tasks()
        task = {
            'id': store.generate_id(temp + daily),
            'name': name,
            'modifiers': modifiers,
            'notes': ''
        }

        if 'da' in modifiers:
            daily.append(task)
            store.write_tasks(config.DAILY_FILE, daily)
        else:
            temp.append(task)
            store.write_tasks(config.TEMP_FILE, temp)

        _push_async(task)
        return jsonify(task), 201

    @app.route('/api/tasks', methods=['PATCH'])
    def update_task():
        data = request.json
        task_id = data.get('id')
        if not task_id:
            return jsonify({'error': 'id required'}), 400

        temp, daily = store.get_all_tasks()

        target, in_daily = None, False
        for t in temp:
            if t['id'] == task_id:
                target, in_daily = t, False
                break
        if not target:
            for t in daily:
                if t['id'] == task_id:
                    target, in_daily = t, True
                    break

        if not target:
            return jsonify({'error': 'not found'}), 404

        if 'name'      in data: target['name']      = data['name']
        if 'modifiers' in data: target['modifiers'] = data['modifiers']
        if 'notes'     in data: target['notes']     = data['notes']

        # Move between files if da modifier changed
        should_be_daily = 'da' in target['modifiers']
        if in_daily and not should_be_daily:
            daily = [t for t in daily if t['id'] != task_id]
            temp.append(target)
        elif not in_daily and should_be_daily:
            temp = [t for t in temp if t['id'] != task_id]
            daily.append(target)

        store.write_tasks(config.TEMP_FILE, temp)
        store.write_tasks(config.DAILY_FILE, daily)
        _push_async(target)
        return jsonify(target)

    @app.route('/api/tasks', methods=['DELETE'])
    def delete_task():
        data = request.json
        task_id = data.get('id')
        if not task_id:
            return jsonify({'error': 'id required'}), 400

        temp, daily = store.get_all_tasks()
        task = next((t for t in temp + daily if t['id'] == task_id), None)
        store.write_tasks(config.TEMP_FILE,  [t for t in temp  if t['id'] != task_id])
        store.write_tasks(config.DAILY_FILE, [t for t in daily if t['id'] != task_id])
        if task:
            now       = datetime.now()
            date_slug = f"{now.month}-{now.day}-{str(now.year)[2:]}"
            _push_completion_async(task, date_slug)
        return '', 204

    @app.route('/api/tasks/reorder', methods=['POST'])
    def reorder_tasks():
        data = request.json
        ids = data.get('ids', [])
        list_type = data.get('list', 'active')

        temp, daily = store.get_all_tasks()

        if list_type == 'active':
            task_map = {t['id']: t for t in temp}
            store.write_tasks(config.TEMP_FILE, [task_map[i] for i in ids if i in task_map])
        else:
            task_map = {t['id']: t for t in daily}
            store.write_tasks(config.DAILY_FILE, [task_map[i] for i in ids if i in task_map])

        return jsonify({'ok': True})
