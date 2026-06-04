import os

MACHINE_ID = "Laptop"
BASE_DIR    = os.path.expanduser("~/Documents/task-app")
ACTIVE_DIR  = os.path.join(BASE_DIR, "active")
JOURNAL_DIR = os.path.join(BASE_DIR, "journal")
FC_DIR          = os.path.join(BASE_DIR, "flashcards")
FC_REVIEWS_FILE = os.path.join(FC_DIR, "reviews.json")
FC_CONFIG_FILE  = os.path.join(FC_DIR, "config.json")
TEMP_FILE   = os.path.join(ACTIVE_DIR, "temporary.md")
DAILY_FILE  = os.path.join(ACTIVE_DIR, "daily.md")
PORT        = 5000
