import os
from flask import request, jsonify
import config


def register(app):
    @app.route('/api/journal/<date>', methods=['GET'])
    def get_journal(date):
        path = os.path.join(config.JOURNAL_DIR, f"{date}.md")
        if not os.path.exists(path):
            return jsonify({'content': ''})
        with open(path, 'r') as f:
            return jsonify({'content': f.read()})

    @app.route('/api/journal/<date>', methods=['POST'])
    def save_journal(date):
        path = os.path.join(config.JOURNAL_DIR, f"{date}.md")
        with open(path, 'w') as f:
            f.write(request.json.get('content', ''))
        return jsonify({'ok': True})
