
# basic sanity check
def test_library_page(client):
    """Test that the library page loads."""
    response = client.get('/library/')
    assert response.status_code == 200
    assert b"<title>Your Library</title>" in response.data

def test_book_cover(client):
    """Test that the book cover endpoint returns a valid response."""
    response = client.get('/library/Ed Nather/The Story of Mel/cover.png')
    assert response.status_code == 200
    assert response.content_type == 'image/png'

def test_chapter_cover(client):
    """Test that the chapter cover endpoint returns a valid response."""
    response = client.get('/library/Ed Nather/The Story of Mel/1/cover.png')
    assert response.status_code == 200
    assert response.content_type == 'image/png'

def test_sounds_src(client):
    """Test that the sounds source endpoint returns a valid response."""
    response = client.get('/library/sounds/page_0_0.wav')
    assert response.status_code == 200
    assert response.content_type == 'audio/x-wav'
