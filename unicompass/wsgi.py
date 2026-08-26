"""
WSGI (Web Server Gateway Interface) Entry Point for UNI Compass.

Purpose of this file:
---------------------
1. Standardized Server Interface:
   WSGI is the standard specification (PEP 3333) enabling web servers to
   communicate with Python web applications. This file provides the standardized
   `app` object required by WSGI-compliant production HTTP servers.

2. Application Lifecycle & Database Setup:
   Calling `create_app()` initializes the Flask application and ensures the SQLite
   database tables and migrations are initialized before handling any requests.

How to run:
-----------
- In Development (Local):
    python wsgi.py
  (Runs Flask's built-in development server on port 8767 with debug mode enabled)

- In Production (with Gunicorn):
    gunicorn wsgi:app
  (Gunicorn imports the `app` object from `wsgi.py` and manages multiple worker
   processes for concurrent, robust production serving)
"""

from app import app, create_app

# Instantiate and initialize the application (runs init_db())
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=8767)
