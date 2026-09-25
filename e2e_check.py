import json, os, subprocess, sys, time, urllib.request, urllib.error

flask_proc = subprocess.Popen(
    [sys.executable, "run_flask.py"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
ready = False
for _ in range(60):
    try:
        urllib.request.urlopen("http://127.0.0.1:5001/api/v1/status", timeout=1)
        ready = True
        break
    except Exception:
        time.sleep(0.5)
if not ready or flask_proc.poll() is not None:
    flask_proc.terminate()
    print("FLASK DID NOT START (or exited early, poll=%s):" % flask_proc.poll())
    try:
        print(flask_proc.communicate(timeout=3)[0][:2000])
    except Exception as e:
        print(e)
    sys.exit(1)
print("1. Flask engine up on :5001 (pid %s)" % flask_proc.pid)

try:
    sys.path.insert(0, "parkflow")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django; django.setup()
    from django.test.utils import setup_test_environment
    setup_test_environment()
    from django.test import Client
    c = Client()



    r = c.post("/parking/entry/", data=json.dumps(
        {"registration_number": "KAA 123A", "vehicle_type": "CAR"}),
        content_type="application/json")
    b = r.json(); assert b["success"], b
    sid = b["data"]["session_id"]
    entry_time = b["data"]["entry_time"]
    print("2. ENTRY ok: session=%s slot=%s" % (sid, b["data"]["slot_number"]))

    from datetime import datetime, timedelta
    exit_at = (datetime.fromisoformat(entry_time) + timedelta(minutes=90)).isoformat()
    r = c.get("/dashboard/data/"); b = r.json()
    assert b["success"] and b["data"]["slots"]["total"] == 20, b
    assert any(s["session_id"] == sid for s in b["data"]["active_sessions"]), b["data"]["active_sessions"]
    assert b["data"]["slot_grid"], "slot_grid missing"
    print("3. DASHBOARD data ok: total=%s active=%s revenue=%s" % (
        b["data"]["slots"]["total"], len(b["data"]["active_sessions"]),
        b["data"]["stats_today"]["revenue"]))

    r = c.get("/dashboard/slots/grid/"); b = r.json()
    locs = list(b["data"]["locations"])
    assert b["success"] and any(k.startswith("Block A") for k in locs), b
    assert b["data"]["locations"]["Block A - Main"][0]["slot_number"] == "A01"
    print("4. SLOT GRID ok: locations=%s A01 loc synced" % locs)

    r = c.get("/vehicles/lookup/?registration_number=kaa123a"); b = r.json()
    assert b["success"] and b["data"]["has_active_session"], b
    print("5. LOOKUP ok: active_session id=%s" % b["data"]["active_session"]["session_id"])

    r = c.post("/parking/exit/", data=json.dumps(
        {"registration_number": "KAA 123A", "exit_time": exit_at}),
        content_type="application/json")
    b = r.json(); assert b["success"], b
    assert b["data"]["fee_ksh"] == 50 and b["data"]["payment_required"], b["data"]
    print("6. EXIT ok: duration=%s fee=%s" % (b["data"]["duration_minutes"], b["data"]["fee_ksh"]))

    r = c.post("/payments/process/", data=json.dumps(
        {"session_id": sid, "amount": 999, "payment_method": "CASH"}),
        content_type="application/json")
    assert not r.json()["success"]
    print("7. PAYMENT wrong amount rejected")

    r = c.post("/payments/process/", data=json.dumps(
        {"session_id": sid, "amount": 50, "payment_method": "CASH"}),
        content_type="application/json")
    b = r.json(); assert b["success"], b
    print("8. PAYMENT ok: ref=%s" % b["data"]["transaction_reference"])

    from parking.models import ParkingSession
    from payments.models import Payment
    ps = ParkingSession.objects.get(id=sid)
    assert ps.status == "COMPLETED", ps.status
    pay = Payment.objects.get(session=ps)
    assert float(pay.amount) == 50 and pay.payment_method == "CASH"
    print("9. MIRROR ok: session %s COMPLETED, payment %s present" % (sid, pay.transaction_reference))

    r = c.post("/vehicles/register/", data=json.dumps(
        {"registration_number": "KBB 222B", "vehicle_type": "MOTORCYCLE"}),
        content_type="application/json")
    assert r.json()["success"], r.json()
    r2 = c.post("/vehicles/register/", data=json.dumps(
        {"registration_number": "KBB 222B", "vehicle_type": "MOTORCYCLE"}),
        content_type="application/json")
    assert not r2.json()["success"] and "already" in r2.json()["message"].lower(), r2.json()
    from vehicles.models import Vehicle
    assert Vehicle.objects.filter(registration_number="KBB 222B").exists()
    print("10. REGISTER ok (+ duplicate rejected, mirrored)")

    r = c.get("/payments/")
    assert r.status_code == 200 and b"CASH" in r.content, (r.status_code, r.content[:300])
    print("11. PAYMENTS page renders from local replica")

    r = c.get("/parking/")
    assert r.status_code == 200, r.status_code
    print("12. PARKING page renders (HTTP %s)" % r.status_code)

    r = c.get("/parking/availability/"); b = r.json()
    assert b["success"] and b["data"]["total_slots"] == 20, b
    print("13. AVAILABILITY proxy ok: total=%s available=%s" % (
        b["data"]["total_slots"], b["data"]["available_slots"]))

    r = c.get("/"); assert r.status_code == 302 and "/dashboard/" in r["Location"], r["Location"]
    print("14. ROOT redirects to /dashboard/ (%s)" % r["Location"])

    r = c.get("/vehicles/lookup/?registration_number=KBB%20222B"); b = r.json()
    assert b["success"] and not b["data"]["has_active_session"], b
    print("15. LOOKUP for registered vehicle ok (active=%s)" % b["data"]["has_active_session"])

    r = c.get("/dashboard/")
    assert r.status_code == 200 and b"ParkFlow" in r.content, (r.status_code, r.content[:300])
    print("16. DASHBOARD page HTML renders")

    print("ALL E2E CHECKS PASSED")
finally:
    flask_proc.terminate()
    try:
        flask_proc.wait(timeout=5)
    except Exception:
        flask_proc.kill()
