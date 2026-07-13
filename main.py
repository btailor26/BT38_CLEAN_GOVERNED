from app import app

try:
    from governed_mcf_routes import governed_mcf_bp
    app.register_blueprint(governed_mcf_bp)
except Exception as exc:
    app.logger.error(f"Failed to register governed MCF routes: {exc}")

if __name__ == '__main__':
    # Legacy background sync workers are intentionally not started here.
    # Future runtime execution must enter through the governed command path.
    app.run(host='0.0.0.0', port=5000, debug=True)
