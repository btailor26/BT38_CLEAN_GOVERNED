from app import app

if __name__ == '__main__':
    # Legacy background sync workers are intentionally not started here.
    # Future runtime execution must enter through the governed command path.
    app.run(host='0.0.0.0', port=5000, debug=True)
