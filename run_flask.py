"""
Run the ParkFlow Kenya Flask parking engine.

Usage (from the project root, with the virtualenv active):

    python run_flask.py

Serves the API on http://localhost:5001 - the same URL Django uses
by default (FLASK_API_URL in parkflow/config/settings.py).
"""

from flask_api.app import create_app

app = create_app()

if __name__ == '__main__':
    # use_reloader=False: the reloader forks a child process that survives
    # terminate() of the parent, leaving an orphan holding port 5001 with
    # stale in-memory state (caused duplicate-registration e2e failures).
    app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)
