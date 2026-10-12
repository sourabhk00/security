# Chauhan Man Power Supply database and project map

## Project layers

| Layer | Files | Responsibility |
|---|---|---|
| Frontend | `templates/`, `static/style.css`, `static/app.js` | Manager and worker screens, responsive layout, geolocation prompts, API calls, live alert refresh |
| Backend | `app.py` | Login/roles, CSRF protection, geofence calculation, attendance rules, manager tools, alert API |
| Database | `schema.sql`, `attendance.db` | SQLite schema and runtime records; the database file is created on first launch |
| Setup | `requirements.txt`, `README.md`, `.env.example` | Dependencies, run steps, and configuration reference |

## Relationships

```text
users 1 ─── * attendance * ─── 1 sites
  │                 │
  ├── marked_by     └── 1 ─── * alerts
  ├── * alerts
  └── * location_pings * ─── 1 sites
users 1 ─── * biometric_credentials
users 1 ─── 0..1 employee_profiles
```

- `users` contains manager and worker accounts. Passwords are stored as Werkzeug password hashes.
- `sites` stores the site center and allowed radius in meters.
- `sites.map_url` keeps the client-provided Google Maps link beside its attendance pin.
- `attendance` stores each shift's check-in/out, coordinates, status, source, and manager attribution for local entries. A missing `check_out` means the shift is open.
- `location_pings` stores location samples received during an open shift.
- `alerts` stores a site exit event and its acknowledgement status and manager attribution.
- `biometric_credentials` stores WebAuthn credential IDs, public keys, and sign counters. The phone keeps its private key and biometric data; neither is stored in this database.
- `employee_profiles` stores employee ID, assigned work site, age, gender, addresses, work experience, post, a normalized profile photo, and the optional face-check consent flag. Photo bytes are served only to authenticated managers. Live verification frames are processed by the API and are not written to attendance or profile tables. Legacy worker accounts may not have a profile until re-registered; new registrations require a unique employee ID.
- For opted-in employees, attendance performs a 1:1 OpenCV LBPH comparison against that profile photo and then still requires the enrolled WebAuthn credential. The matcher keeps no generated face embedding between requests and does not perform liveness detection.

Timestamps are ISO-8601 UTC strings. Foreign keys and indexes are enabled for relationships and active-shift/alert queries. `app.py` applies additive compatibility migrations when an earlier Fieldguard database is present.

## Workforce operations tables

- shift_schedules stores one planned site and time window per worker per date.
- leave_requests and correction_requests preserve the request, decision, manager note (for leave), and reviewer.
- attendance_breaks records break intervals; reports calculate net time from these intervals.
- announcements and announcement_reads store team updates and each worker's read acknowledgement.
- audit_log records manager actions such as employee, site, schedule, announcement, and approval changes.
- handover_actions stores cross-shift site tasks, their source attendance, creator, optional assignee, severity, due time, status, resolution, and manager note. `app.py` calculates the live priority score from severity, overdue duration, age, and repeated open site/category volume rather than persisting a stale score.
- sites.capacity is zero for unlimited occupancy; positive values are enforced when recording new attendance.
- Nexaryon Tower and Office are retained as inactive site records so historical attendance foreign keys remain intact.

## Read-only safety analytics

`app.py` derives the manager's 90-day site safety ranking from `attendance`, `safety_incidents`, and `safety_checklists`; it does not persist a second copy of the score. Attendance rows provide the exposure denominator, incidents contribute severity- and age-weighted points, and failed checklist items contribute inspection risk. A program-wide baseline smooths estimates for sites with little recorded activity. The manager-only CSV is generated from the same calculation. Risk bands are operational prioritization signals and are not stored as safety certifications or personnel findings.

## Operational boundary

The backend checks coordinates against the site radius for GPS check-in and manager-entered local attendance. The browser supplies those coordinates, so this validates the reported device position, not the worker's identity or an unspoofable physical position. Browser tracking may stop when the operating system suspends the page. For payroll-grade auditability and dependable background alerts, deploy an authenticated mobile client with a server-side audit trail and push delivery.

Worker check-in and check-out APIs require a recent, one-use WebAuthn verification for the matching action. The authenticator must report user verification. Phones choose the available method (face unlock, fingerprint, or device PIN); the app does not receive biometric imagery. WebAuthn requires localhost for local development and HTTPS when accessed from another device or deployed.

Four sites are active for new attendance: Amba Shakti Udyog Ltd, B R Oil Industries Pvt Ltd, Vrindavan Industries, and Rajawat House Gwalior at 26.2726962, 78.1364943. Nexaryon Tower and Office remain inactive for historical rows. The three Banmore pins remain approximate starting points and should be checked at each entrance.
