def test_public_info_returns_json(client):
    response = client.get("/api/info")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert isinstance(payload, dict)
    assert payload
    assert payload.get("name")
    assert payload.get("server")
    if "version" in payload:
        assert isinstance(payload["version"], str)
        assert payload["version"].strip()


def test_public_plans_returns_expected_plan_keys(client):
    response = client.get("/api/plans")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert isinstance(payload, dict)

    plans = payload.get("plans", payload)
    assert isinstance(plans, dict)
    assert {"free", "pro", "business"}.issubset(plans.keys())

    for plan_name in ("free", "pro", "business"):
        assert isinstance(plans[plan_name], dict)
        assert plans[plan_name]
