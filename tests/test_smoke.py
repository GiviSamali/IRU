def test_app_starts(client):
    response = client.get("/")

    assert response.status_code == 200
    assert client.app is not None
