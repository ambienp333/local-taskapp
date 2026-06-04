import os
import json
import threading
from flask import request, jsonify
from datetime import datetime
import config
import store


def _push_journal_async(date_slug, data):
    def _run():
        try:
            import sync_routes
            sync_routes.push_journal(date_slug, data)
        except Exception as e:
            print(f'[sync] journal push failed: {e}')
    threading.Thread(target=_run, daemon=True).start()


def load_journal(date_slug):
    """date_slug format: M-D-YY (e.g. 5-24-26)"""
    path = os.path.join(config.JOURNAL_DIR, f"{date_slug}.json")
    if not os.path.exists(path):
        return {'tasks': []}
    with open(path, 'r') as f:
        try:
            return json.load(f)
        except Exception:
            return {'tasks': []}


def save_journal(date_slug, data):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    path = os.path.join(config.JOURNAL_DIR, f"{date_slug}.json")
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def register(app):

    @app.route('/api/journal/calendar/<int:year>/<int:month>', methods=['GET'])
    def get_calendar(year, month):
        """Return days in the month where new tasks were created."""
        bright_days = set()

        # Check currently active tasks
        temp, daily = store.get_all_tasks()
        for task in temp + daily:
            try:
                date_part = task['id'].split('(')[0]
                mo, d, y = date_part.split('/')
                if 2000 + int(y) == year and int(mo) == month:
                    bright_days.add(int(d))
            except Exception:
                pass

        # Scan journal files for this month
        if os.path.exists(config.JOURNAL_DIR):
            for filename in os.listdir(config.JOURNAL_DIR):
                if not filename.endswith('.json'):
                    continue
                slug = filename[:-5]
                try:
                    parts = slug.split('-')
                    fm, fd, fy = int(parts[0]), int(parts[1]), 2000 + int(parts[2])
                    if fm != month or fy != year:
                        continue
                except Exception:
                    continue
                journal = load_journal(slug)
                for task in journal.get('tasks', []):
                    try:
                        date_part = task['id'].split('(')[0]
                        mo, d, y = date_part.split('/')
                        if 2000 + int(y) == year and int(mo) == month:
                            bright_days.add(int(d))
                    except Exception:
                        pass

        return jsonify({'bright_days': sorted(bright_days)})

    @app.route('/api/journal/<date>/snapshot', methods=['POST'])
    def update_snapshot(date):
        """Merge active task list into this date's journal. Preserves completion markers."""
        incoming = request.json.get('tasks', [])
        existing = load_journal(date)

        completed_ids = {t['id'] for t in existing.get('tasks', []) if t.get('completed')}
        incoming_ids  = {t['id'] for t in incoming}

        merged = []
        for t in incoming:
            merged.append({
                'id':        t['id'],
                'name':      t['name'],
                'modifiers': t.get('modifiers', []),
                'notes':     t.get('notes', ''),
                'completed': t['id'] in completed_ids,
            })
        for t in existing.get('tasks', []):
            if t.get('completed') and t['id'] not in incoming_ids:
                merged.append(t)

        save_journal(date, {'tasks': merged})
        _push_journal_async(date, {'tasks': merged})
        return jsonify({'ok': True})

    @app.route('/api/journal/<date>/complete', methods=['POST'])
    def mark_complete(date):
        """Mark a task as completed in this date's journal."""
        task_id   = request.json.get('id')
        task_data = request.json.get('task', {})
        existing  = load_journal(date)
        tasks     = existing.get('tasks', [])

        found = False
        for t in tasks:
            if t['id'] == task_id:
                t['completed'] = True
                t['name']      = task_data.get('name',      t['name'])
                t['modifiers'] = task_data.get('modifiers', t.get('modifiers', []))
                t['notes']     = task_data.get('notes',     t.get('notes', ''))
                found = True
                break

        if not found:
            tasks.append({
                'id':        task_id,
                'name':      task_data.get('name', ''),
                'modifiers': task_data.get('modifiers', []),
                'notes':     task_data.get('notes', ''),
                'completed': True,
            })

        save_journal(date, {'tasks': tasks})
        _push_journal_async(date, {'tasks': tasks})
        return jsonify({'ok': True})

    @app.route('/api/journal/<date>', methods=['GET'])
    def get_journal(date):
        return jsonify(load_journal(date))
