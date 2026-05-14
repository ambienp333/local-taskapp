import re
import os
from datetime import datetime
import config

ID_PATTERN = re.compile(r'\[(\d+/\d+/\d+\(\w+\)\(\d{4}\))\]$')


def parse_tasks(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as f:
        lines = [l.rstrip('\n') for l in f]
    tasks = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == '[':
            block = []
            i += 1
            while i < len(lines) and lines[i].strip() != ']':
                block.append(lines[i])
                i += 1
            task = _parse_block(block)
            if task:
                tasks.append(task)
        i += 1
    return tasks


def _parse_block(lines):
    if not lines:
        return None
    m = ID_PATTERN.search(lines[0].strip())
    if not m:
        return None
    task_id = m.group(1)
    name = lines[0].strip()[:m.start()].strip()
    modifiers = []
    notes = ''
    if len(lines) >= 2 and lines[1].strip():
        modifiers = [x.strip().lower() for x in lines[1].strip().split(',') if x.strip()]
    if len(lines) >= 3:
        notes = '\n'.join(lines[2:]).strip()
    return {'id': task_id, 'name': name, 'modifiers': modifiers, 'notes': notes}


def write_tasks(filepath, tasks):
    with open(filepath, 'w') as f:
        for task in tasks:
            f.write('[\n')
            f.write(f'{task["name"]}[{task["id"]}]\n')
            f.write(f'{",".join(task["modifiers"])}\n')
            if task.get('notes', '').strip():
                f.write(f'{task["notes"]}\n')
            f.write(']\n\n')


def get_all_tasks():
    return parse_tasks(config.TEMP_FILE), parse_tasks(config.DAILY_FILE)


def generate_id(all_tasks):
    now = datetime.now()
    date_str = f"{now.month}/{now.day}/{str(now.year)[2:]}"
    prefix = f"{date_str}({config.MACHINE_ID})"
    max_counter = 0
    for task in all_tasks:
        if task['id'].startswith(prefix + '('):
            try:
                counter = int(task['id'].split('(')[-1].rstrip(')'))
                if counter > max_counter:
                    max_counter = counter
            except (ValueError, IndexError):
                pass
    return f"{prefix}({max_counter + 1:04d})"
