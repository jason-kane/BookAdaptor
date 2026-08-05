# conftest.py
import pytest
from artifact_editor.app import create_app

@pytest.fixture()
def app():
    app = create_app({"TESTING": True})
    
    # Setup: Create tables or initialize test data here
    # with app.app_context():
    #    db.create_all()
        
    yield app
    
    # Teardown: Clean up after the test completes
    # with app.app_context():
    #     db.drop_all()

@pytest.fixture()
def client(app):
    return app.test_client() # Allows simulated HTTP requests
