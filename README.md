Employee Attendance & Analytics API
A REST API built with Python, FastAPI, and MongoDB for
employee records, attendance punch-in/punch-out, attendance corrections,
and monthly/depart­ment analytics.
Features
- Create and list employees.
- Record attendance punch-in and punch-out.
- Correct attendance records with an audit history.
- Calculate late minutes, overtime, work hours, half-day status, and
  attendance percentages.
- Provide employee monthly summaries, department summaries, late-comer
  rankings, and department attendance trends.
- Use MongoDB indexes and an explain endpoint for query-plan
  inspection.
- Validate request data and return appropriate HTTP status codes.
Requirements
- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git (optional, for version control)
Project structure
candidate_kit/
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
├── DECISIONS.md
└── .env                 # Local only; do not commit
Setup
1. Create and activate a virtual environment
Windows PowerShell:
python -m venv .venv
.\.venv\Scripts\Activate.ps1
If PowerShell blocks activation, use the Python executable inside
.venv directly or adjust your local execution policy as appropriate.
2. Install dependencies
python -m pip install -r requirements.txt
3. Configure MongoDB
Start MongoDB locally or use a MongoDB Atlas connection string. Create a
local .env file in the project root:
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
Replace MONGO_URI with your own connection string if required. Keep
credentials private. Real environment variables take precedence over
values in .env.
4. Start the API
Run this command from the candidate_kit project root:
python -m uvicorn app.main:app --port 8000 --reload
The API will be available at:
- Base URL: http://127.0.0.1:8000
- Interactive Swagger documentation: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health
The application creates its required MongoDB indexes at startup. Make
sure MongoDB is running and reachable before starting the API.
Main endpoints
  Method                  Endpoint                                      Purpose
  GET                   /health                                     Check API and MongoDB
                                                                        readiness
  POST                  /employees                                  Create an employee
  GET                   /employees                                  List employees;
                                                                        supports pagination and
                                                                        department filtering
  POST                  /attendance/punch-in                        Record an employee
                                                                        punch-in
  POST                  /attendance/punch-out                       Record an employee
                                                                        punch-out
  GET                   /attendance                                 List attendance records
                                                                        with filters and
                                                                        pagination
  PATCH                 /attendance/{emp_code}/{date}               Correct an attendance
                                                                        record and append audit
                                                                        history
  GET                   /analytics/employees/{emp_code}/monthly     Get one employee's
                                                                        monthly attendance
                                                                        summary
  GET                   /analytics/departments/summary              Get department-level
                                                                        monthly metrics
  GET                   /analytics/leaderboard/late                 Get the monthly
                                                                        late-comer leaderboard
  GET                   /analytics/departments/{department}/trend   Get daily department
                                                                        attendance trends
  GET                   /admin/explain/{endpoint}                   Inspect MongoDB
                                                                        execution statistics
                                                                        for supported query
                                                                    types
Use /docs for the exact required request fields, query parameters, and
response schemas.
API conventions
- Employees are identified by emp_code.
- Calendar dates use YYYY-MM-DD; months use YYYY-MM.
- Shift times use 24-hour HH:MM format.
- Timestamp values in API requests and responses are epoch
  milliseconds.
- Attendance statuses include PRESENT, WFH, ON_DUTY, ABSENT,
  and LEAVE, subject to the rules for each endpoint.
- Derived attendance values such as work hours, late minutes, overtime
  minutes, and half-day status are calculated by the API.
- Validation errors generally return HTTP 422; unknown resources
  return 404; duplicate/conflicting operations may return 409.
Testing
1. Start MongoDB.
2. Start the FastAPI application using the command above.
3. Open http://127.0.0.1:8000/docs.
4. Try the endpoints using the schemas and required parameters shown in
   Swagger.
5. Use /health to check MongoDB connectivity and the admin explain
   endpoint to inspect query plans.
Security and repository hygiene
- Do not commit .env, MongoDB credentials, API keys, or other
  secrets.
- Do not include a Dockerfile for this assignment.
- Keep requirements.txt, app/main.py, REVIEW.md, DECISIONS.md,
  and this README in the repository.
- Before submission, verify that the repository contains no
  credentials or local virtual-environment files.
Known limitations
Document any unfinished requirements or known issues here before
submission. Do not describe a requirement as complete unless it has been
implemented and verified.