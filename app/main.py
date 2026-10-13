import os
import re
import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
ALL_STATUSES = PRESENCE_STATUSES | {"ABSENT", "LEAVE"}
EXPLAIN_ENDPOINTS = {
    "attendance_list": ("attendance_logs", "/attendance"),
    "employee_monthly": ("attendance_logs", "/analytics/employees/{emp_code}/monthly"),
    "department_summary": ("employees", "/analytics/departments/summary"),
    "late_leaderboard": ("attendance_logs", "/analytics/leaderboard/late"),
    "department_trend": ("employees", "/analytics/departments/{department}/trend"),
}
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@app.on_event("startup")
def ensure_indexes():
    # Index creation is idempotent. Duplicate data must be cleaned before creating unique indexes.
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="unique_employee_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined_on")
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True, name="unique_employee_attendance_date"
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="attendance_date_emp_code"
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING), ("late_minutes", DESCENDING)],
        name="employee_date_late"
    )
    db.attendance_logs.create_index(
        [("late_minutes", DESCENDING), ("emp_code", ASCENDING)],
        name="late_leaderboard_index"
    )
def http_date(value: str, label: str = "date") -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(422, f"{label} must use YYYY-MM-DD format")
def month_bounds(year: int, month: int) -> tuple[str, str, date, date]:
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(422, "Invalid year or month")
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return first.isoformat(), (last + timedelta(days=1)).isoformat(), first, last
def epoch_ms_to_datetime(value, field="timestamp") -> datetime:
    # Strictly require integer epoch milliseconds (no bools, floats, strings, or seconds).
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) < 100_000_000_000:
        raise HTTPException(422, f"{field} must be epoch milliseconds as an integer")
    try:
        instant = datetime.fromtimestamp(value / 1000, tz=UTC).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        raise HTTPException(422, f"{field} is outside the supported range")
    return instant.astimezone(IST)
def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)
def mongo_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0)
def as_ist(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST).replace(microsecond=0)
def to_epoch_ms(dt: Optional[datetime]):
    if dt is None:
        return None
    aware = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return int(aware.timestamp() * 1000)
def serialize_doc(doc: dict) -> dict:
    """Convert stored BSON datetimes to epoch milliseconds and omit Mongo _id and attendance id."""
    result = {}
    for key, value in doc.items():
        if key == "_id" or key == "id":
            continue
        if isinstance(value, datetime):
            result[key] = to_epoch_ms(value)
        elif key == "history" and isinstance(value, list):
            history = []
            for entry in value:
                item = {}
                for k, v in entry.items():
                    if isinstance(v, datetime):
                        item[k] = to_epoch_ms(v)
                    elif k == "changes" and isinstance(v, dict):
                        item[k] = v
                    else:
                        item[k] = v
                history.append(item)
            result[key] = history
        else:
            result[key] = value
    return result
def shift_datetime(day: date, hhmm: str) -> datetime:
    try:
        h, m = map(int, hhmm.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return datetime.combine(day, time(h, m), tzinfo=IST)
    except Exception:
        raise HTTPException(422, "Shift times must use valid HH:MM values")
def shift_start_for(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
    attendance_day: date,
) -> datetime:
    return shift_datetime(attendance_day, shift_start)
def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str, attendance_day: date) -> int:
    punch_in = as_ist(punch_in)
    start = shift_start_for(punch_in, shift_start, shift_end, attendance_day)
    elapsed = (punch_in - start).total_seconds()
    if elapsed <= 10 * 60:
        return 0
    return max(0, int(elapsed // 60))
def shift_end_for(attendance_day: date, shift_start: str, shift_end: str) -> datetime:
    start_clock = time(*map(int, shift_start.split(":")))
    end_clock = time(*map(int, shift_end.split(":")))
    end = shift_datetime(attendance_day, shift_end)
    if end_clock <= start_clock:
        end += timedelta(days=1)
    return end
def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, attendance_day: date) -> int:
    end = shift_end_for(attendance_day, shift_start, shift_end)
    seconds = (as_ist(punch_out) - end).total_seconds()
    if seconds < 30 * 60:
        return 0
    return max(0, int(seconds // 60))
def half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    return float((seconds / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
def working_days(start: date, end: date) -> int:
    if end < start:
        return 0
    count = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count
def attendance_day_for(punch_in: datetime, employee: dict) -> date:
    local = as_ist(punch_in)
    day = local.date()
    start_clock = time(*map(int, employee["shift_start"].split(":")))
    end_clock = time(*map(int, employee["shift_end"].split(":")))
    if end_clock <= start_clock and local.timetz().replace(tzinfo=None) < end_clock:
        day -= timedelta(days=1)
    return day
def valid_timestamp_fields(cls, value):
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Timestamp must be an integer in milliseconds")
    if abs(value) < 100_000_000_000:
        raise ValueError("Timestamp must be in milliseconds, not seconds")
    return value
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    department: str = Field(min_length=1)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str
    @field_validator("joined_on")
    @classmethod
    def check_joined_on(cls, value):
        http_date(value, "joined_on")
        return value
    @field_validator("shift_start", "shift_end")
    @classmethod
    def check_shift(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("shift times must use HH:MM")
        return value
class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"
    @field_validator("punched_at")
    @classmethod
    def check_time(cls, value):
        return valid_timestamp_fields(cls, value)
class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int] = None
    @field_validator("punched_at")
    @classmethod
    def check_time(cls, value):
        return valid_timestamp_fields(cls, value)
class AttendanceCorrectionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=1)
    regularized_by: str = Field(min_length=1)
    @field_validator("punch_in", "punch_out")
    @classmethod
    def check_time(cls, value):
        return valid_timestamp_fields(cls, value)
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start and shift_end must differ")
    doc = body.model_dump()
    doc["created_at"] = mongo_utc(now_ist())
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_doc(doc)
@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1),
                   page_size: int = Query(20, ge=1, le=100)):
    query = {"department": department} if department is not None else {}
    total = db.employees.count_documents(query)
    items = [serialize_doc(x) for x in db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    if body.status not in PRESENCE_STATUSES:
        raise HTTPException(422, "status must be PRESENT, WFH, or ON_DUTY")
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    punch = epoch_ms_to_datetime(body.punched_at, "punched_at") if body.punched_at is not None else now_ist()
    day = attendance_day_for(punch, employee)
    if day < date.fromisoformat(employee["joined_on"]):
        raise HTTPException(422, "Punch-in date cannot be before joined_on")
    doc = {
        "emp_code": body.emp_code, "date": day.isoformat(), "status": body.status,
        "punch_in": mongo_utc(punch), "punch_out": None, "work_hours": None,
        "late_minutes": compute_late_minutes(punch, employee["shift_start"], employee["shift_end"], day),
        "overtime_minutes": 0, "half_day": False, "history": []
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_doc(doc)
@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    punch = epoch_ms_to_datetime(body.punched_at, "punched_at") if body.punched_at is not None else now_ist()
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    # Match open shifts by actual punch-in instant; supports overnight shifts.
    candidates = list(db.attendance_logs.find({
        "emp_code": body.emp_code, "punch_in": {"$lte": mongo_utc(punch)}
    }).sort("punch_in", DESCENDING).limit(1))
    if not candidates:
        raise HTTPException(404, "Punch-in record not found")
    record = candidates[0]
    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")
    pin = as_ist(record["punch_in"])
    if punch <= pin:
        raise HTTPException(422, "punched_at must be after punch_in")
    if punch - pin > timedelta(hours=24):
        raise HTTPException(422, "punched_at must be within 24 hours of punch_in")
    day = date.fromisoformat(record["date"])
    hours = compute_work_hours(pin, punch)
    overtime = compute_overtime(punch, employee["shift_start"], employee["shift_end"], day)
    update = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {"punch_out": mongo_utc(punch), "work_hours": hours,
                  "overtime_minutes": overtime, "half_day": hours < 4.50}}
    )
    if update.modified_count != 1:
        raise HTTPException(409, "Already punched out")
    updated = db.attendance_logs.find_one({"_id": record["_id"]}, {"_id": 0})
    return serialize_doc(updated)
@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, status: Optional[str] = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if date_from:
        http_date(date_from, "date_from")
    if date_to:
        http_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if status is not None:
        if status not in ALL_STATUSES:
            raise HTTPException(422, "Invalid status")
        query["status"] = status
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(query)
    items = [serialize_doc(d) for d in db.attendance_logs.find(query, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page-1)*page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(emp_code: str, date: str, body: AttendanceCorrectionIn):
    day = http_date(date)
    if not body.reason.strip() or not body.regularized_by.strip():
        raise HTTPException(422, "reason and regularized_by are required")
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "Attendance record not found")
    supplied = body.model_fields_set
    status = body.status if "status" in supplied else record["status"]
    if status not in ALL_STATUSES:
        raise HTTPException(422, "Invalid attendance status")
    pin = epoch_ms_to_datetime(body.punch_in, "punch_in") if "punch_in" in supplied and body.punch_in is not None else as_ist(record.get("punch_in"))
    pout = epoch_ms_to_datetime(body.punch_out, "punch_out") if "punch_out" in supplied and body.punch_out is not None else as_ist(record.get("punch_out"))
    if status in {"ABSENT", "LEAVE"}:
        if ("punch_in" in supplied and body.punch_in is not None) or ("punch_out" in supplied and body.punch_out is not None):
            raise HTTPException(422, "ABSENT/LEAVE cannot include punch times")
        pin = pout = None
    else:
        if pin is None:
            raise HTTPException(422, "Presence status requires punch_in")
        if attendance_day_for(pin, employee) != day:
            raise HTTPException(422, "punch_in must belong to the attendance date")
        if pout is not None:
            if pout <= pin:
                raise HTTPException(422, "punch_out must be after punch_in")
            if pout - pin > timedelta(hours=24):
                raise HTTPException(422, "punch_out must be within 24 hours of punch_in")
    updates = {}
    changes = {}
    def change(field, old, new):
        if isinstance(old, datetime) and isinstance(new, datetime):
            old_value = to_epoch_ms(as_ist(old))
            new_value = to_epoch_ms(as_ist(new))
            changed = old_value != new_value
        else:
            old_value = old
            new_value = new
            changed = old_value != new_value

        if changed:
            changes[field] = {
                "old": old_value,
                "new": new_value,
            }
            updates[field] = (
                mongo_utc(new) if isinstance(new, datetime) else new
            )
    change("status", record.get("status"), status)
    change("punch_in", record.get("punch_in"), pin)
    change("punch_out", record.get("punch_out"), pout)
    if not changes:
        raise HTTPException(422, "No changes supplied")
    if status in PRESENCE_STATUSES and pin is not None:
        updates["late_minutes"] = compute_late_minutes(pin, employee["shift_start"], employee["shift_end"], day)
    else:
        updates["late_minutes"] = 0
    if pin is not None and pout is not None and status in PRESENCE_STATUSES:
        updates["work_hours"] = compute_work_hours(pin, pout)
        updates["half_day"] = updates["work_hours"] < 4.50
        updates["overtime_minutes"] = compute_overtime(pout, employee["shift_start"], employee["shift_end"], day)
    else:
        updates["work_hours"] = None
        updates["half_day"] = False
        updates["overtime_minutes"] = 0
    audit = {"at": mongo_utc(now_ist()), "by": body.regularized_by.strip(),
             "reason": body.reason.strip(), "changes": changes}
    # Optimistic concurrency: only update if source values are still unchanged.
    guard = {"_id": record["_id"], "status": record.get("status"),
             "punch_in": record.get("punch_in"), "punch_out": record.get("punch_out")}
    result = db.attendance_logs.update_one(guard, {"$set": updates, "$push": {"history": audit}})
    if result.modified_count != 1:
        raise HTTPException(409, "Attendance record changed concurrently; retry")
    return serialize_doc(db.attendance_logs.find_one({"_id": record["_id"]}, {"_id": 0}))
@app.get("/analytics/employees/{emp_code}/monthly")
def monthly_employee_analytics(emp_code: str, year: int, month: int):
    start_s, end_s, first, last = month_bounds(year, month)
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    joined = max(first, date.fromisoformat(employee["joined_on"]))
    wd = working_days(joined, last) if joined <= last else 0
    rows = list(db.attendance_logs.find({"emp_code": emp_code, "date": {"$gte": start_s, "$lt": end_s}}, {"_id": 0}))
    present_days = 0.0
    leave_days = 0
    late_count = total_late = total_ot = 0
    for r in rows:
        d = date.fromisoformat(r["date"])
        if r["status"] in PRESENCE_STATUSES:
            if d.weekday() < 5:
                present_days += 0.5 if r.get("half_day") else 1.0
            if r.get("late_minutes", 0) > 0:
                late_count += 1
                total_late += r["late_minutes"]
            total_ot += r.get("overtime_minutes", 0)
        elif r["status"] == "LEAVE":
            leave_days += 1
    return {
        "emp_code": emp_code, "name": employee["name"], "department": employee["department"],
        "year": year, "month": month, "working_days": wd, "present_days": float(present_days),
        "leave_days": leave_days, "late_count": late_count, "total_late_minutes": total_late,
        "total_overtime_minutes": total_ot,
        "attendance_pct": float(half_up(present_days / wd * 100, 4)) if wd else None,
    }
@app.get("/analytics/departments/summary")
def department_summary(year: int, month: int):
    start_s, end_s, _, last = month_bounds(year, month)
    employees = list(db.employees.find({"joined_on": {"$lt": end_s}}, {"_id": 0}).sort("department", ASCENDING))
    grouped = {}
    for employee in employees:
        department = employee["department"]
        bucket = grouped.setdefault(department, {"department": department, "headcount": 0, "present_days": 0,
            "late_count": 0, "total_late_minutes": 0, "leave_count": 0, "on_duty_count": 0, "work_hours": []})
        bucket["headcount"] += 1
        logs = db.attendance_logs.find({"emp_code": employee["emp_code"], "date": {"$gte": start_s, "$lt": end_s}}, {"_id": 0})
        for log in logs:
            if log.get("status") in PRESENCE_STATUSES:
                bucket["present_days"] += 0.5 if log.get("half_day") else 1
                if log.get("work_hours") is not None:
                    bucket["work_hours"].append(log["work_hours"])
                if log.get("late_minutes", 0) > 0:
                    bucket["late_count"] += 1
                    bucket["total_late_minutes"] += log["late_minutes"]
                if log.get("status") == "ON_DUTY":
                    bucket["on_duty_count"] += 1
            elif log.get("status") == "LEAVE":
                bucket["leave_count"] += 1
    departments = []
    for bucket in grouped.values():
        hours = bucket.pop("work_hours")
        bucket["avg_work_hours"] = float(half_up(sum(hours) / len(hours), 2)) if hours else None
        departments.append(bucket)
    departments.sort(key=lambda x: x["department"])
    return {"year": year, "month": month, "departments": departments}
@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard(year: int, month: int, department: Optional[str] = None,
                             limit: int = Query(10, ge=1, le=100)):
    start_s, end_s, _, _ = month_bounds(year, month)
    pipeline = [
        {"$match": {"date": {"$gte": start_s, "$lt": end_s}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "late_days": {"$sum": 1}, "total_late_minutes": {"$sum": "$late_minutes"}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {"_id": 0, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department",
                      "late_days": 1, "total_late_minutes": 1, "rank": 1}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ]
    return {"year": year, "month": month, "leaderboard": list(db.attendance_logs.aggregate(pipeline))}
@app.get("/analytics/departments/{department}/trend")
def department_attendance_trend(department: str, date_from: str, date_to: str):
    start = http_date(date_from, "date_from")
    end = http_date(date_to, "date_to")
    if end < start:
        raise HTTPException(422, "date_to cannot be before date_from")
    if (end - start).days + 1 > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(404, "Department not found")

    # Use BSON dates for $densify; stored attendance dates are YYYY-MM-DD strings.
    start_dt = datetime.combine(start, time.min)
    end_exclusive_dt = datetime.combine(end + timedelta(days=1), time.min)
    attendance_pipeline = [
        {"$match": {"date": {"$gte": date_from, "$lte": date_to}}},
        {"$lookup": {"from": "employees", "localField": "emp_code",
                     "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {"$set": {"date": {"$dateFromString": {"dateString": "$date"}}}},
        {"$group": {
            "_id": "$date",
            "present_count": {"$sum": {"$cond": [
                {"$in": ["$status", list(PRESENCE_STATUSES)]},
                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                0.0
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}}
        }},
        {"$project": {"_id": 0, "date": "$_id", "present_count": 1, "late_count": 1}}
    ]
    # Seed the requested start date at the database level, so $densify still
    # returns a complete date series when the department has no attendance logs.
    pipeline = [
        {"$documents": [{"date": start_dt, "present_count": 0.0, "late_count": 0}]},
        {"$unionWith": {"coll": "attendance_logs", "pipeline": attendance_pipeline}},
        {"$group": {
            "_id": "$date",
            "present_count": {"$max": {"$ifNull": ["$present_count", 0]}},
            "late_count": {"$max": {"$ifNull": ["$late_count", 0]}}
        }},
        {"$project": {"_id": 0, "date": "$_id", "present_count": 1, "late_count": 1}},
        {"$densify": {"field": "date", "range": {
            "step": 1, "unit": "day", "bounds": [start_dt, end_exclusive_dt]
        }}},
        {"$set": {
            "date_string": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
            "is_working_day": {"$and": [
                {"$gte": [{"$dayOfWeek": "$date"}, 2]},
                {"$lte": [{"$dayOfWeek": "$date"}, 6]}
            ]}
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"day_string": "$date_string"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$department", department]},
                    {"$lte": ["$joined_on", "$$day_string"]}
                ]}}},
                {"$project": {"_id": 1}}
            ],
            "as": "eligible_employees"
        }},
        {"$set": {
            "headcount": {"$size": "$eligible_employees"},
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]}
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
            None
        ]}}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, "current"]}
            }}
        }},
        {"$project": {
            "_id": 0, "date": "$date_string", "is_working_day": 1,
            "headcount": 1, "present_count": 1, "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}
        }},
        {"$sort": {"date": 1}}
    ]
    items = list(db.aggregate(pipeline, allowDiskUse=True))
    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, date_from: Optional[str] = None,
                     date_to: Optional[str] = None, year: Optional[int] = None,
                     month: Optional[int] = None, department: Optional[str] = None,
                     limit: Optional[int] = None):
    if endpoint not in EXPLAIN_ENDPOINTS:
        raise HTTPException(422, f"endpoint must be one of: {', '.join(EXPLAIN_ENDPOINTS)}")
    required = {
        "attendance_list": [],
        "employee_monthly": ["emp_code", "year", "month"],
        "department_summary": ["year", "month"],
        "late_leaderboard": ["year", "month"],
        "department_trend": ["department", "date_from", "date_to"],
    }[endpoint]
    values = {"emp_code": emp_code, "year": year, "month": month,
              "department": department, "date_from": date_from, "date_to": date_to}
    missing = [k for k in required if values[k] is None]
    if missing:
        raise HTTPException(422, f"Missing required query parameters: {', '.join(missing)}")
    collection, _ = EXPLAIN_ENDPOINTS[endpoint]
    if endpoint == "attendance_list":
        query = {}
        if date_from:
            http_date(date_from, "date_from")
            query.setdefault("date", {})["$gte"] = date_from
        if date_to:
            http_date(date_to, "date_to")
            query.setdefault("date", {})["$lte"] = date_to
        if emp_code:
            query["emp_code"] = emp_code
        explanation = db.command("explain", {"find": collection, "filter": query,
            "sort": {"date": -1, "emp_code": 1}, "limit": 100}, verbosity="executionStats")
    elif endpoint == "employee_monthly":
        start_s, end_s, _, _ = month_bounds(year, month)
        explanation = db.command("explain", {"find": collection, "filter": {
            "emp_code": emp_code, "date": {"$gte": start_s, "$lt": end_s}}}, verbosity="executionStats")
    elif endpoint == "department_summary":
        month_bounds(year, month)
        explanation = db.command("explain", {"aggregate": "employees", "pipeline": [
            {"$match": {"joined_on": {"$lte": f"{year:04d}-{month:02d}-31"}}}], "cursor": {}}, verbosity="executionStats")
        collection = "employees"
    elif endpoint == "late_leaderboard":
        start_s, end_s, _, _ = month_bounds(year, month)
        explanation = db.command("explain", {"find": collection, "filter": {
            "date": {"$gte": start_s, "$lt": end_s}, "late_minutes": {"$gt": 0}}}, verbosity="executionStats")
    else:
        http_date(date_from, "date_from")
        http_date(date_to, "date_to")
        explanation = db.command("explain", {"find": "employees", "filter": {
            "department": department, "joined_on": {"$lte": date_to}}}, verbosity="executionStats")
        collection = "employees"
    return {"endpoint": endpoint, "collection": collection, "explain": explanation}