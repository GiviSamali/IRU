import base64
from urllib.parse import quote


def _create_test_user():
    from server.database import create_user

    return create_user("download-test-user")


def test_valid_download_token_allows_file_download_with_unicode_filename(client, tmp_path, monkeypatch):
    import server.runtime_state as runtime_state
    import server.routers.tasks as tasks_router

    user = _create_test_user()
    file_path = tmp_path / "временный-файл.txt"
    file_bytes = "Привет, ИРУ!\n".encode("utf-8")
    file_path.write_bytes(file_bytes)

    token = runtime_state.create_download_token("device-1", str(file_path), user_id=user["id"])

    async def fake_send_command_to_agent(device_key, action, params):
        assert device_key == f"{user['id']}:device-1"
        assert action == "get_file_content"
        assert params["path"] == str(file_path)
        return {
            "data_b64": base64.b64encode(file_bytes).decode("ascii"),
            "filename": file_path.name,
        }

    monkeypatch.setattr(tasks_router, "send_command_to_agent", fake_send_command_to_agent)

    response = client.get(f"/api/download/{token}")

    assert response.status_code == 200
    assert response.content == file_bytes
    assert response.headers["content-type"] == "application/octet-stream"

    content_disposition = response.headers["content-disposition"]
    assert content_disposition.startswith("attachment; ")
    assert "filename*=" in content_disposition
    assert f"filename*=UTF-8''{quote(file_path.name)}" in content_disposition


def test_invalid_download_token_returns_not_found(client):
    response = client.get("/api/download/not-a-real-token")

    assert response.status_code == 404
    payload = response.json()
    assert payload["detail"]
