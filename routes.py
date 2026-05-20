"""
BT38 routes.py compatibility shell.

Runtime route instructions now live in governed_routes.py.
This module intentionally exposes only the Blueprint object required by app.py.
"""

from flask import Blueprint

bp = Blueprint("routes", __name__)
