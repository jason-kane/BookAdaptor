
# basic sanity check
def test_home_page(client):
    """Test that the home page loads."""
    response = client.get('/')
    assert response.status_code == 200
    assert b"<title>Home</title>" in response.data
