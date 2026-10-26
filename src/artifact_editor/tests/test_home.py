def test_home_redirects_to_independent_service(client):
    response = client.get('/')
    assert response.status_code == 302
    assert response.location == 'http://localhost:8080/'
