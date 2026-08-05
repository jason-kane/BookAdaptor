
# basic sanity check
def test_book_page(client):
    """Test that the book page loads."""
    response = client.get('/library/Ed Nather/The Story of Mel/')
    assert response.status_code == 200
    assert b"<h2>The Story of Mel</h2>" in response.data

# test for breakdown_chapters()
# test for set_book_metadata()
