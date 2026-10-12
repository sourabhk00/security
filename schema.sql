-- Fieldguard SQLite schema. All timestamps are stored as ISO-8601 UTC strings.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('manager', 'worker')),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
  longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
  radius_m INTEGER NOT NULL DEFAULT 150 CHECK(radius_m BETWEEN 25 AND 5000),
  map_url TEXT,
  capacity INTEGER NOT NULL DEFAULT 0 CHECK(capacity >= 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  site_id INTEGER NOT NULL REFERENCES sites(id),
  check_in TEXT NOT NULL,
  check_out TEXT,
  in_lat REAL,
  in_lng REAL,
  out_lat REAL,
  out_lng REAL,
  source TEXT NOT NULL DEFAULT 'gps' CHECK(source IN ('gps', 'manager_local')),
  status TEXT NOT NULL DEFAULT 'on_site' CHECK(status IN ('on_site', 'outside_site', 'checked_out')),
  marked_by INTEGER REFERENCES users(id),
  CHECK((source = 'gps' AND marked_by IS NULL) OR (source = 'manager_local' AND marked_by IS NOT NULL)),
  CHECK(check_out IS NULL OR check_out >= check_in)
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  site_id INTEGER NOT NULL REFERENCES sites(id),
  attendance_id INTEGER REFERENCES attendance(id),
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  latitude REAL,
  longitude REAL,
  created_at TEXT NOT NULL,
  acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0, 1)),
  acknowledged_by INTEGER REFERENCES users(id),
  acknowledged_at TEXT
);

CREATE TABLE IF NOT EXISTS location_pings (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  site_id INTEGER NOT NULL REFERENCES sites(id),
  latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
  longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
  accuracy REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS biometric_credentials (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_id TEXT NOT NULL UNIQUE,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL DEFAULT 0,
  device_name TEXT NOT NULL DEFAULT 'Phone authenticator',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employee_profiles (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  employee_id TEXT NOT NULL UNIQUE,
  site_id INTEGER REFERENCES sites(id),
  age INTEGER NOT NULL CHECK(age BETWEEN 18 AND 100),
  gender TEXT NOT NULL CHECK(gender IN ('female','male','non_binary','other','prefer_not_to_say')),
  permanent_address TEXT NOT NULL,
  communication_address TEXT NOT NULL,
  work_experience TEXT NOT NULL,
  post TEXT NOT NULL,
  photo_jpeg BLOB,
  face_consent INTEGER NOT NULL DEFAULT 0 CHECK(face_consent IN (0,1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shift_schedules (
  id INTEGER PRIMARY KEY,
  worker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  site_id INTEGER NOT NULL REFERENCES sites(id),
  work_date TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  confirmation_status TEXT NOT NULL DEFAULT 'pending' CHECK(confirmation_status IN ('pending','confirmed','declined')),
  worker_response_at TEXT,
  worker_note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(worker_id, work_date)
);

CREATE TABLE IF NOT EXISTS shift_templates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL CHECK(length(name) BETWEEN 2 AND 80),
  site_id INTEGER NOT NULL REFERENCES sites(id),
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS worker_availability (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id,weekday)
);

CREATE TABLE IF NOT EXISTS site_staffing_targets (
  id INTEGER PRIMARY KEY,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
  minimum_workers INTEGER NOT NULL DEFAULT 0 CHECK(minimum_workers BETWEEN 0 AND 10000),
  start_time TEXT NOT NULL DEFAULT '08:00',
  end_time TEXT NOT NULL DEFAULT '16:00',
  required_credential TEXT NOT NULL DEFAULT '',
  updated_by INTEGER NOT NULL REFERENCES users(id),
  updated_at TEXT NOT NULL,
  UNIQUE(site_id,weekday)
);

CREATE TABLE IF NOT EXISTS leave_requests (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined')),
  manager_note TEXT,
  decided_by INTEGER REFERENCES users(id),
  decided_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS correction_requests (
  id INTEGER PRIMARY KEY,
  attendance_id INTEGER NOT NULL REFERENCES attendance(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  requested_check_in TEXT NOT NULL,
  requested_check_out TEXT,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined')),
  decided_by INTEGER REFERENCES users(id),
  decided_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance_breaks (
  id INTEGER PRIMARY KEY,
  attendance_id INTEGER NOT NULL REFERENCES attendance(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY,
  manager_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);

CREATE TABLE IF NOT EXISTS announcement_reads (
  announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  read_at TEXT NOT NULL,
  PRIMARY KEY(announcement_id,user_id)
);

CREATE TABLE IF NOT EXISTS shift_handover_notes (
  id INTEGER PRIMARY KEY,
  attendance_id INTEGER NOT NULL REFERENCES attendance(id) ON DELETE CASCADE,
  worker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  site_id INTEGER NOT NULL REFERENCES sites(id),
  note TEXT NOT NULL CHECK(length(note) BETWEEN 1 AND 1500),
  created_at TEXT NOT NULL,
  UNIQUE(attendance_id)
);

CREATE TABLE IF NOT EXISTS handover_actions (
  id INTEGER PRIMARY KEY,
  site_id INTEGER NOT NULL REFERENCES sites(id),
  source_attendance_id INTEGER REFERENCES attendance(id),
  created_by INTEGER NOT NULL REFERENCES users(id),
  assigned_to INTEGER REFERENCES users(id),
  title TEXT NOT NULL CHECK(length(title) BETWEEN 3 AND 120),
  category TEXT NOT NULL CHECK(category IN ('safety','equipment','client_request','access','coverage','other')),
  severity TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN ('low','medium','high','critical')),
  details TEXT NOT NULL DEFAULT '' CHECK(length(details)<=1000),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','in_progress','resolved')),
  due_at TEXT NOT NULL,
  manager_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS safety_incidents (
  id INTEGER PRIMARY KEY,
  attendance_id INTEGER REFERENCES attendance(id),
  worker_id INTEGER NOT NULL REFERENCES users(id),
  site_id INTEGER NOT NULL REFERENCES sites(id),
  category TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
  details TEXT NOT NULL CHECK(length(details) BETWEEN 1 AND 2000),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','reviewing','resolved')),
  manager_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shift_transfer_requests (
  id INTEGER PRIMARY KEY,
  schedule_id INTEGER NOT NULL REFERENCES shift_schedules(id) ON DELETE CASCADE,
  requester_id INTEGER NOT NULL REFERENCES users(id),
  target_worker_id INTEGER NOT NULL REFERENCES users(id),
  reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined')),
  manager_note TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS worker_certifications (
  id INTEGER PRIMARY KEY,
  worker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_name TEXT NOT NULL CHECK(length(credential_name) BETWEEN 2 AND 120),
  issuer TEXT NOT NULL CHECK(length(issuer) BETWEEN 2 AND 120),
  issued_on TEXT,
  expires_on TEXT,
  reference_code TEXT,
  notes TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS safety_checklists (
  id INTEGER PRIMARY KEY,
  attendance_id INTEGER NOT NULL UNIQUE REFERENCES attendance(id) ON DELETE CASCADE,
  worker_id INTEGER NOT NULL REFERENCES users(id),
  site_id INTEGER NOT NULL REFERENCES sites(id),
  ppe_ok INTEGER NOT NULL CHECK(ppe_ok IN (0,1)),
  hazards_ok INTEGER NOT NULL CHECK(hazards_ok IN (0,1)),
  equipment_ok INTEGER NOT NULL CHECK(equipment_ok IN (0,1)),
  emergency_ready INTEGER NOT NULL CHECK(emergency_ready IN (0,1)),
  observations TEXT NOT NULL DEFAULT '',
  completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  actor_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER,
  details TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attendance_worker_open ON attendance(user_id, check_out);
CREATE INDEX IF NOT EXISTS idx_attendance_site_open ON attendance(site_id, check_out);
CREATE INDEX IF NOT EXISTS idx_alerts_open_created ON alerts(acknowledged, created_at);
CREATE INDEX IF NOT EXISTS idx_pings_worker_created ON location_pings(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_biometric_credentials_user ON biometric_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_employee_profiles_employee_id ON employee_profiles(employee_id);
CREATE INDEX IF NOT EXISTS idx_schedule_date_site ON shift_schedules(work_date,site_id);
CREATE INDEX IF NOT EXISTS idx_shift_template_site ON shift_templates(site_id);
CREATE INDEX IF NOT EXISTS idx_availability_weekday ON worker_availability(weekday,user_id);
CREATE INDEX IF NOT EXISTS idx_staffing_target_site_weekday ON site_staffing_targets(site_id,weekday);
CREATE INDEX IF NOT EXISTS idx_leave_status ON leave_requests(status,created_at);
CREATE INDEX IF NOT EXISTS idx_correction_status ON correction_requests(status,created_at);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_handover_created ON shift_handover_notes(created_at);
CREATE INDEX IF NOT EXISTS idx_handover_action_site_status_due ON handover_actions(site_id,status,due_at);
CREATE INDEX IF NOT EXISTS idx_handover_action_assignee_status ON handover_actions(assigned_to,status,created_at);
CREATE INDEX IF NOT EXISTS idx_incidents_status_created ON safety_incidents(status,created_at);
CREATE INDEX IF NOT EXISTS idx_shift_transfer_status ON shift_transfer_requests(status,created_at);
CREATE INDEX IF NOT EXISTS idx_certification_expiry ON worker_certifications(expires_on,worker_id);
CREATE INDEX IF NOT EXISTS idx_safety_checklist_completed ON safety_checklists(completed_at,site_id);
