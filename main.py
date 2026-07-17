from app import app

try:
    import services.governed_mcf_compat  # noqa: F401
    from governed_mcf_routes import governed_mcf_bp
    app.register_blueprint(governed_mcf_bp)
except Exception as exc:
    app.logger.error(f"Failed to register governed MCF routes: {exc}")


@app.teardown_appcontext
def close_sqlalchemy_session(exception=None):
    """Never leave pooled Neon connections idle inside a transaction."""
    from extensions import db

    try:
        # Read-only SELECTs also open a PostgreSQL transaction. Roll it back at
        # context teardown unless the route/service already committed its write.
        db.session.rollback()
    except Exception:
        app.logger.exception("SQLAlchemy rollback failed during app teardown")
    finally:
        db.session.remove()


if __name__ == '__main__':
    # Legacy background sync workers are intentionally not started here.
    # Future runtime execution must enter through the governed command path.
    app.run(host='0.0.0.0', port=5000, debug=True)
