import os

MACHINE_ID = os.environ.get('MACHINE_ID', 'Laptop')
BASE_DIR    = os.path.expanduser("~/Documents/task-app")
ACTIVE_DIR  = os.path.join(BASE_DIR, "active")
JOURNAL_DIR = os.path.join(BASE_DIR, "journal")
FC_DIR      = os.path.join(BASE_DIR, "flashcards")
TEMP_FILE   = os.path.join(ACTIVE_DIR, "temporary.md")
DAILY_FILE  = os.path.join(ACTIVE_DIR, "daily.md")
PORT        = 5000
