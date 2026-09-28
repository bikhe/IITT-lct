from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


def test_api_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_datasets() -> None:
    response = client.get("/api/datasets")
    assert response.status_code == 200
    assert {d["id"] for d in response.json()} == {"east", "southeast", "southcenter"}


def test_api_solve_serializes_routes_for_the_map() -> None:
    response = client.post("/api/plan/solve", json={"region": "east", "use_local_search": True})
    assert response.status_code == 200
    data = response.json()
    opt = data["optimized"]
    assert opt["metrics"]["assigned_orders"] > data["baseline"]["metrics"]["assigned_orders"]
    assert data["diff"]["delta"]["crew_count"] <= 0
    active = [r for r in opt["routes"] if r["is_active"]]  # вычисляемые поля доходят до фронта
    assert active and active[0]["jobs"][0]["start_time"]
    assert set(data["route_explanations"]) == {e["id"] for e in data["engineers"]}
    assert all(isinstance(v, str) and v for v in opt["unassigned_orders"].values())


def test_api_explain() -> None:
    client.post("/api/plan/solve", json={"region": "east"})
    order_id = client.get("/api/plan/east").json()["orders"][0]["id"]
    exp = client.get(f"/api/explain/{order_id}?region=east")
    assert exp.status_code == 200
    assert len(exp.json()["explanation"]) > 10


def test_api_scenarios_events_and_reset() -> None:
    client.post("/api/plan/solve", json={"region": "southeast"})
    scenarios = client.get("/api/scenarios/southeast").json()
    kinds = [s["event_type"] for s in scenarios]
    assert kinds == ["urgent_order", "new_order", "cancel_order", "engineer_unavailable"]
    emergency = scenarios[0]["event"]["new_order"]
    assert emergency["duration_min"] == 80 and emergency["window"]["end"] == "23:59"

    total_before = client.get("/api/plan/southeast").json()["optimized"]["metrics"]["total_orders"]
    for sc in scenarios:
        resp = client.post("/api/plan/event", json={"region": "southeast", "event": sc["event"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plan_diff"]["summary_ru"]
    state = client.get("/api/plan/southeast").json()
    assert len(state["events"]) == 4
    assert state["optimized"]["metrics"]["total_orders"] == total_before + 2

    # событие раньше предыдущего — понятная ошибка 400
    late = dict(scenarios[2]["event"], event_time="09:00")
    resp = client.post("/api/plan/event", json={"region": "southeast", "event": late})
    assert resp.status_code == 400 and "раньше предыдущего" in resp.json()["detail"]

    reset = client.post("/api/plan/reset/southeast").json()
    assert reset["events"] == [] and reset["optimized"]["metrics"]["total_orders"] == total_before


def test_api_dataset_without_solving() -> None:
    data = client.get("/api/dataset/southcenter").json()
    assert data["region_name"] == "Югоцентр"
    assert len(data["orders"]) > 0 and len(data["engineers"]) > 0


def test_api_alternatives_and_manual_assign() -> None:
    client.post("/api/plan/solve", json={"region": "east"})
    plan = client.get("/api/plan/east").json()
    assert plan["morning"]["metrics"]["assigned_orders"] == plan["optimized"]["metrics"]["assigned_orders"]
    moved = None
    for route in plan["optimized"]["routes"]:
        for job in route["jobs"]:
            alt = client.get(f"/api/alternatives/east/{job['order_id']}").json()
            assert alt["current_engineer_id"] == route["engineer_id"]
            assert alt["candidates"][0]["is_current"]
            assert len(alt["candidates"]) == len(plan["engineers"])
            other = [c for c in alt["candidates"] if c["code"] is None and not c["is_current"]]
            if other:
                moved = (job["order_id"], other[0]["engineer_id"])
                break
        if moved:
            break
    assert moved, "в плане Востока есть заявка, которую может взять другая бригада"
    oid, eid = moved
    resp = client.post(
        "/api/plan/event",
        json={"region": "east", "event": {"event_type": "manual_assign", "event_time": "00:00",
                                          "order_id": oid, "engineer_id": eid}},
    )
    assert resp.status_code == 200, resp.text
    routes = resp.json()["optimized"]["routes"]
    assert any(r["engineer_id"] == eid and any(j["order_id"] == oid for j in r["jobs"]) for r in routes)
    skill_fail = [c for c in client.get(f"/api/alternatives/east/{oid}").json()["candidates"] if c["code"] == "skill"]
    if skill_fail:
        bad = client.post(
            "/api/plan/event",
            json={"region": "east", "event": {"event_type": "manual_assign", "event_time": "00:00",
                                              "order_id": oid, "engineer_id": skill_fail[0]["engineer_id"]}},
        )
        assert bad.status_code == 400 and "навык" in bad.json()["detail"]
    client.post("/api/plan/reset/east")


def test_api_upload_custom_dataset_and_solve() -> None:
    src = client.get("/api/dataset/southcenter").json()
    body = {"name": "Проверка загрузки", "orders": src["orders"][:20], "engineers": src["engineers"][:5]}
    meta = client.post("/api/datasets/upload", json=body)
    assert meta.status_code == 200, meta.text
    reg = meta.json()["id"]
    assert reg in {d["id"] for d in client.get("/api/datasets").json()}
    plan = client.post("/api/plan/solve", json={"region": reg}).json()
    assert plan["region_name"] == "Проверка загрузки"
    m = plan["optimized"]["metrics"]
    assert m["total_orders"] == 20 and m["assigned_orders"] + m["unassigned_orders"] == 20

    dup = dict(body, orders=[body["orders"][0], body["orders"][0]])
    bad = client.post("/api/datasets/upload", json=dup)
    assert bad.status_code == 400 and "Повторяются" in bad.json()["detail"]
    broken = client.post("/api/datasets/upload", json={"orders": [{"id": "1"}], "engineers": []})
    assert broken.status_code == 422
