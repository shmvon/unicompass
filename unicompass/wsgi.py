"""
WSGI entry point for Gunicorn.
Run with: gunicorn wsgi:app
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=8767)
