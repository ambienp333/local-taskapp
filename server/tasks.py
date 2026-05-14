from flask import request, jsonify
from datetime import datetime, timedelta
import store
import config


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
        return jsonify(target)

    @app.route('/api/tasks', methods=['DELETE'])
    def delete_task():
        data = request.json
        task_id = data.get('id')
        if not task_id:
            return jsonify({'error': 'id required'}), 400

        temp, daily = store.get_all_tasks()
        store.write_tasks(config.TEMP_FILE,  [t for t in temp  if t['id'] != task_id])
        store.write_tasks(config.DAILY_FILE, [t for t in daily if t['id'] != task_id])
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
