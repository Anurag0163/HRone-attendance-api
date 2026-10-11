# Design Decisions

## 1. Framework and Database

I used FastAPI to expose REST endpoints and MongoDB as the persistence layer. FastAPI provides request validation and interactive API documentation, while MongoDB supports indexed queries and aggregation pipelines.

## 2. Configuration

MongoDB connection settings are read from `MONGO_URI` and `MONGO_DB`. A local `.env` file is supported for development, while environment variables take precedence. Credentials are kept out of source control.

## 3. Employee and Attendance Identity

Employees are identified by the business-provided `emp_code`. Attendance records are identified by the combination of `emp_code` and `date`. Unique indexes enforce these constraints at the database level.

## 4. Timestamp Representation

The API accepts and returns epoch timestamps in milliseconds. MongoDB stores timestamp values as BSON datetimes. IST is used when deriving attendance calendar dates and interpreting employee shifts.

## 5. Derived Attendance Fields

Work hours, late minutes, overtime minutes, and half-day status are calculated from attendance timestamps, employee shift timings, and the assignment's business rules. Clients cannot control these derived values directly.

## 6. Concurrency and Audit History

Database uniqueness constraints protect employee codes and daily attendance records against duplicate creation. Attendance corrections preserve an audit trail containing the correction time, responsible user, reason, and changed fields.

## 7. Analytics

Analytics calculations follow the assignment's definitions for working days, half-days, headcount, late arrivals, overtime, leaderboard ranking, and attendance rates. MongoDB indexes and aggregation pipelines are used where appropriate to support query performance.

## 8. Validation and Error Handling

Invalid inputs are rejected with validation errors. Missing employees or attendance records return not-found responses, while duplicate or conflicting operations return conflict responses where required by the contract.

## 9. Trade-offs and Limitations

The implementation prioritizes adherence to the assignment contract and clear business-rule handling. The local Swagger checks provide useful evidence for selected endpoints, but they do not prove every edge case or large-dataset performance requirement. Remaining limitations should be documented accurately rather than treated as verified successes.
