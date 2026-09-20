#!/usr/bin/env python3
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
CONFIG = ROOT / "config"
NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
WINDOW_START = NOW - timedelta(days=30)
KEEP_FROM = NOW - timedelta(days=35)

RANK = {"unknown": 0, "operational": 1, "degraded": 2, "outage": 3}


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def http_check(url, timeout, degraded_after):
    started = time.perf_counter()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SNine-Status/1.0 (+https://status.s9lab.site)",
            "Accept": "application/json,text/plain,*/*",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            code = int(response.getcode() or 0)
            elapsed = round((time.perf_counter() - started) * 1000)
            if 200 <= code < 400:
                state = "degraded" if degraded_after and elapsed >= degraded_after else "operational"
                return state, elapsed, f"HTTP {code}"
            return "outage", elapsed, f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        return "outage", elapsed, f"HTTP {exc.code}"
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        return "outage", elapsed, exc.__class__.__name__


def tcp_check(host, port, timeout, degraded_after):
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            elapsed = round((time.perf_counter() - started) * 1000)
            state = "degraded" if degraded_after and elapsed >= degraded_after else "operational"
            return state, elapsed, "TCP connected"
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        return "outage", elapsed, exc.__class__.__name__


def prune_events(events):
    clean = []
    for event in events:
        dt = parse_time(event.get("at"))
        if dt:
            clean.append((dt, event))
    clean.sort(key=lambda x: x[0])
    before = [pair for pair in clean if pair[0] < KEEP_FROM]
    after = [pair for pair in clean if pair[0] >= KEEP_FROM]
    kept = ([before[-1]] if before else []) + after
    return [event for _, event in kept]


def build_intervals(events):
    parsed = []
    for event in events:
        dt = parse_time(event.get("at"))
        if dt:
            parsed.append((dt, event.get("status", "unknown")))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return [], None

    observed_start = max(WINDOW_START, parsed[0][0])
    status = None
    for at, st in parsed:
        if at <= observed_start:
            status = st
        else:
            break
    if status is None:
        first_after = next(((at, st) for at, st in parsed if at > observed_start), None)
        if not first_after:
            return [], None
        observed_start = first_after[0]
        status = first_after[1]

    points = [(observed_start, status)]
    for at, st in parsed:
        if observed_start < at <= NOW:
            points.append((at, st))

    intervals = []
    for idx, (start, st) in enumerate(points):
        end = points[idx + 1][0] if idx + 1 < len(points) else NOW
        if end > start:
            intervals.append((start, end, st))
    return intervals, observed_start


def uptime_data(events):
    intervals, observed_start = build_intervals(events)
    if not intervals or not observed_start:
        return None, [], None

    total = 0.0
    up = 0.0
    for start, end, state in intervals:
        seconds = max(0.0, (end - start).total_seconds())
        total += seconds
        if state in ("operational", "degraded"):
            up += seconds
    pct = round((up / total) * 100, 3) if total > 0 else None

    days = []
    today = NOW.date()
    for offset in range(29, -1, -1):
        date = today - timedelta(days=offset)
        day_start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
        day_end = min(day_start + timedelta(days=1), NOW)
        if day_end <= observed_start:
            state = "unknown"
        else:
            states = []
            for start, end, st in intervals:
                if end > day_start and start < day_end:
                    states.append(st)
            if not states:
                state = "unknown"
            elif "outage" in states:
                state = "outage"
            elif "degraded" in states:
                state = "degraded"
            elif "operational" in states:
                state = "operational"
            else:
                state = "unknown"
        days.append({"date": date.isoformat(), "status": state})

    return pct, days, observed_start.isoformat().replace("+00:00", "Z")


def make_incident_id(service_id):
    return f"auto-{service_id}-{NOW.strftime('%Y%m%d%H%M%S')}"


services_config = read_json(CONFIG / "services.json", {"groups": []})
history = read_json(API / "history.json", {"services": {}})
history.setdefault("services", {})
auto_incidents = read_json(API / "auto-incidents.json", [])
manual_incidents = read_json(CONFIG / "incidents.json", [])
maintenance = read_json(CONFIG / "maintenance.json", [])

flat = []
for group in services_config.get("groups", []):
    for service in group.get("services", []):
        item = dict(service)
        item["_group"] = group.get("name", "Services")
        flat.append(item)

results = {}
pending = []

for service in flat:
    service_id = service["id"]
    check = service.get("check", {})
    check_type = check.get("type", "manual")
    timeout = float(check.get("timeout", 10))
    degraded_after = int(check.get("degradedAfterMs", 0) or 0)
    state = None
    response_ms = None
    detail = None
    source = "automatic"

    if check_type == "http":
        state, response_ms, detail = http_check(check.get("url", ""), timeout, degraded_after)
    elif check_type == "http_env":
        url = os.environ.get(check.get("env", ""), "").strip()
        if url:
            state, response_ms, detail = http_check(url, timeout, degraded_after)
        else:
            pending.append(service_id)
    elif check_type == "tcp_env":
        host = os.environ.get(check.get("hostEnv", ""), "").strip()
        port = os.environ.get(check.get("portEnv", ""), "").strip()
        if host and port:
            state, response_ms, detail = tcp_check(host, port, timeout, degraded_after)
        else:
            pending.append(service_id)
    else:
        state = service.get("manualStatus", "unknown")
        detail = "Manual status"
        source = "manual"

    if state is not None:
        results[service_id] = {
            "status": state,
            "response_ms": response_ms,
            "detail": detail,
            "source": source,
        }

for _ in range(len(pending) + 1):
    changed = False
    for service_id in list(pending):
        service = next(s for s in flat if s["id"] == service_id)
        fallback = service.get("check", {}).get("fallbackService")
        if fallback and fallback in results:
            parent = results[fallback]
            results[service_id] = {
                "status": parent["status"],
                "response_ms": parent.get("response_ms"),
                "detail": f"Derived from {fallback}",
                "source": "derived",
            }
            pending.remove(service_id)
            changed = True
        elif not fallback:
            results[service_id] = {
                "status": service.get("manualStatus", "unknown"),
                "response_ms": None,
                "detail": "Manual fallback",
                "source": "manual",
            }
            pending.remove(service_id)
            changed = True
    if not changed:
        break

for service_id in pending:
    service = next(s for s in flat if s["id"] == service_id)
    results[service_id] = {
        "status": service.get("manualStatus", "unknown"),
        "response_ms": None,
        "detail": "Manual fallback",
        "source": "manual",
    }

changes = []
for service in flat:
    service_id = service["id"]
    events = prune_events(history["services"].get(service_id, []))
    previous = events[-1]["status"] if events else None
    current = results[service_id]["status"]
    if previous != current:
        events.append({"at": NOW_ISO, "status": current})
        changes.append((service, previous, current))
    history["services"][service_id] = prune_events(events)

for service, previous, current in changes:
    service_id = service["id"]
    active = next(
        (
            incident for incident in auto_incidents
            if incident.get("auto")
            and service_id in incident.get("components", [])
            and incident.get("status") != "resolved"
        ),
        None,
    )

    if current in ("outage", "degraded") and previous not in ("outage", "degraded"):
        if not active:
            impact = "major" if current == "outage" else "minor"
            auto_incidents.append({
                "id": make_incident_id(service_id),
                "auto": True,
                "title": f"{service['name']} service disruption",
                "status": "investigating",
                "impact": impact,
                "components": [service_id],
                "created_at": NOW_ISO,
                "resolved_at": None,
                "updates": [{
                    "at": NOW_ISO,
                    "status": "investigating",
                    "body": f"Automated monitoring detected that {service['name']} is {current}."
                }],
            })
    elif current == "operational" and previous in ("outage", "degraded") and active:
        active["status"] = "resolved"
        active["resolved_at"] = NOW_ISO
        active.setdefault("updates", []).append({
            "at": NOW_ISO,
            "status": "resolved",
            "body": f"{service['name']} has recovered and is operational again."
        })

auto_incidents = sorted(auto_incidents, key=lambda x: x.get("created_at", ""), reverse=True)[:100]

groups_out = []
all_states = []
for group in services_config.get("groups", []):
    service_rows = []
    group_states = []
    for service in group.get("services", []):
        result = results[service["id"]]
        events = history["services"].get(service["id"], [])
        uptime, days, observed_since = uptime_data(events)
        row = {
            "id": service["id"],
            "name": service["name"],
            "description": service.get("description", ""),
            "status": result["status"],
            "response_ms": result.get("response_ms"),
            "detail": result.get("detail"),
            "source": result.get("source", "automatic"),
            "uptime_30d": uptime,
            "observed_since": observed_since,
            "days": days,
        }
        service_rows.append(row)
        group_states.append(row["status"])
        all_states.append(row["status"])

    group_status = max(group_states, key=lambda s: RANK.get(s, 0)) if group_states else "unknown"
    groups_out.append({"name": group.get("name", "Services"), "status": group_status, "services": service_rows})

if "outage" in all_states:
    overall = "outage"
    message = "Some services are experiencing an outage"
elif "degraded" in all_states:
    overall = "degraded"
    message = "Some services are experiencing degraded performance"
elif all_states and all(state == "operational" for state in all_states):
    overall = "operational"
    message = "All services are online"
else:
    overall = "unknown"
    message = "Some service states are unknown"

active_incidents = [
    incident for incident in (auto_incidents + manual_incidents)
    if incident.get("status") != "resolved"
]

status_doc = {
    "name": "SNine Client",
    "overall": overall,
    "message": message,
    "updated_at": NOW_ISO,
    "active_incidents": len(active_incidents),
    "groups": groups_out,
}

merged_incidents = sorted(
    auto_incidents + manual_incidents,
    key=lambda x: x.get("created_at", ""),
    reverse=True,
)

write_json(API / "status.json", status_doc)
write_json(API / "history.json", history)
write_json(API / "auto-incidents.json", auto_incidents)
write_json(API / "incidents.json", merged_incidents)
write_json(API / "maintenance.json", maintenance)

print(json.dumps({
    "overall": overall,
    "updated_at": NOW_ISO,
    "changes": [{"service": s["id"], "from": old, "to": new} for s, old, new in changes],
}, indent=2))
