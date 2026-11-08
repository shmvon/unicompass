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
  (Runs Flask's built-in development server on port 8767 bound to [::] with debug mode enabled)

- In Production (with Gunicorn inside Container):
    gunicorn --bind [::]:8767 wsgi:app
  (Gunicorn imports the `app` object from `wsgi.py` and listens on [::]:8767 inside the container.
   Port 8000 communicated to the server maps directly to container port 8767 via 8000:8767)
"""

from app import app, create_app

# Instantiate and initialize the application (runs init_db())
app = create_app()

if __name__ == "__main__":
    # Bound to [::] (IPv6/IPv4 dual-stack localhost) on port 8767 as per IT instructions
    app.run(debug=True, host="::", port=8767)
