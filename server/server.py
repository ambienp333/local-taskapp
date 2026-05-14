import os
from flask import Flask, send_from_directory
import tasks as task_routes
import journal_routes
import config

app = Flask(__name__)
FRONTEND = os.path.join(os.path.dirname(__file__), 'frontend')

task_routes.register(app)
journal_routes.register(app)


@app.route('/')
def index():
    return send_from_directory(FRONTEND, 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(FRONTEND, filename)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=config.PORT, debug=False)
