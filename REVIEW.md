# Code Review

## Overview

The starter application provides an initial implementation of the Employee Attendance & Analytics API. The review focuses on correctness, data integrity, input validation, concurrency, and compliance with the API contract.

## Issues Reviewed

### 1. Attendance calculations
- Late arrival must follow the specified 10-minute threshold precisely.
- Overtime must only be counted when the employee works at least 30 minutes beyond the scheduled shift end.
- Work hours must be rounded to two decimal places using half-up rounding.
- Half-day status must be derived from the rounded work-hours value.

### 2. Time and date handling
- API timestamps must use epoch milliseconds.
- Timestamp inputs must reject seconds, floats, strings, and out-of-range values.
- Attendance dates must be determined using Indian Standard Time (IST), including overnight shifts.
- Stored and returned timestamps must use consistent precision.

### 3. Data integrity and concurrency
- Employee codes must be unique and enforced by a MongoDB unique index.
- Each employee must have at most one attendance record per attendance date.
- Duplicate-key errors must be handled and returned as appropriate HTTP conflicts.
- Concurrent attendance corrections must not silently overwrite audit history.

### 4. Request validation and error handling
- Invalid status values and inconsistent punch times must be rejected.
- Unknown employees or attendance records must return appropriate not-found responses.
- Pagination parameters and date ranges must be validated.
- Health checks must verify MongoDB connectivity rather than only checking whether the API process is running.

### 5. Analytics correctness
- Present days must count only weekdays, with half-days contributing 0.5.
- Monthly headcount must include employees who joined by the end of the period, including employees without attendance records.
- Leaderboard ties must use standard competition ranking.
- Department trends must include dates with no attendance records and calculate moving averages over the specified window.

### 6. MongoDB query performance
- Required indexes should be created idempotently at application startup.
- Analytics and listing endpoints should use suitable MongoDB queries and aggregation pipelines.
- The explain endpoint should return actual MongoDB execution statistics so index usage can be inspected.

## Verification Notes

The health, attendance trend, attendance-list explain, and employee-monthly explain endpoints were exercised locally through Swagger during development. The observed explain plans for attendance listing and employee monthly analytics used `IXSCAN`.

This is a record of the checks performed locally, not a claim that every contract requirement or large-dataset performance scenario has passed. Further verification may be required before production use.
