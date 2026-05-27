#!/usr/bin/env python3
"""
Bluetooth Attendance System
Run: python3 server.py
Open: http://localhost:8080
"""

import sqlite3, json, os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

DB = "attendance.db"

# ── DATABASE SETUP ──────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usn TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        bt_device TEXT UNIQUE,
        course TEXT DEFAULT 'CS301'
    );
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course TEXT NOT NULL,
        room TEXT NOT NULL,
        teacher TEXT NOT NULL,
        rssi_threshold INTEGER DEFAULT -70,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        status TEXT DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS attendance_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        status TEXT DEFAULT 'present',
        rssi INTEGER,
        time_in TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id),
        FOREIGN KEY(student_id) REFERENCES students(id),
        UNIQUE(session_id, student_id)
    );
    """)
    # Seed sample students
    students = [
        ("USN001","Aarav Sharma","BT:AA:11:22:33"),
        ("USN002","Priya Nair","BT:BB:44:55:66"),
        ("USN003","Rohan Patel","BT:CC:77:88:99"),
        ("USN004","Sneha Reddy","BT:DD:AA:BB:CC"),
        ("USN005","Arjun Menon","BT:EE:DD:EE:FF"),
        ("USN006","Kavya Singh","BT:FF:01:23:45"),
        ("USN007","Dev Krishnan","BT:GG:67:89:AB"),
        ("USN008","Meera Joshi","BT:HH:CD:EF:01"),
        ("USN009","Kiran Rao","BT:II:23:45:67"),
        ("USN010","Ananya Iyer","BT:JJ:89:AB:CD"),
    ]
    for usn, name, bt in students:
        try:
            db.execute("INSERT INTO students(usn,name,bt_device) VALUES(?,?,?)", (usn,name,bt))
        except: pass
    db.commit()
    db.close()

# ── API HANDLERS ────────────────────────────────────────────────
def api_students():
    db = get_db()
    rows = db.execute("SELECT * FROM students ORDER BY name").fetchall()
    db.close()
    return [dict(r) for r in rows]

def api_sessions():
    db = get_db()
    rows = db.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 20").fetchall()
    db.close()
    return [dict(r) for r in rows]

def api_start_session(body):
    db = get_db()
    now = datetime.now().isoformat()
    cur = db.execute(
        "INSERT INTO sessions(course,room,teacher,rssi_threshold,started_at) VALUES(?,?,?,?,?)",
        (body.get("course","CS301"), body.get("room","Room 101"),
         body.get("teacher","Prof. Kumar"), body.get("rssi_threshold",-70), now)
    )
    db.commit()
    sid = cur.lastrowid
    db.close()
    return {"id": sid, "started_at": now}

def api_end_session(session_id):
    db = get_db()
    db.execute("UPDATE sessions SET ended_at=?, status='ended' WHERE id=?",
               (datetime.now().isoformat(), session_id))
    db.commit()
    db.close()
    return {"ok": True}

def api_detect(body):
    """Mark a student present via BT device MAC or student_id"""
    db = get_db()
    session_id = body.get("session_id")
    bt_device   = body.get("bt_device")
    student_id  = body.get("student_id")
    rssi        = body.get("rssi", -65)

    # Get session threshold
    session = db.execute("SELECT * FROM sessions WHERE id=? AND status='active'", (session_id,)).fetchone()
    if not session:
        db.close()
        return {"error": "No active session"}

    if bt_device:
        student = db.execute("SELECT * FROM students WHERE bt_device=?", (bt_device,)).fetchone()
    elif student_id:
        student = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    else:
        db.close()
        return {"error": "No device or student specified"}

    if not student:
        db.close()
        return {"error": "Student not found"}

    # RSSI threshold check
    if rssi < session["rssi_threshold"]:
        db.close()
        return {"error": f"RSSI {rssi} below threshold {session['rssi_threshold']}"}

    # Determine late (>10 min after session start)
    started = datetime.fromisoformat(session["started_at"])
    diff_min = (datetime.now() - started).seconds // 60
    status = "late" if diff_min >= 10 else "present"

    now = datetime.now().isoformat()
    try:
        db.execute(
            "INSERT INTO attendance_logs(session_id,student_id,status,rssi,time_in) VALUES(?,?,?,?,?)",
            (session_id, student["id"], status, rssi, now)
        )
        db.commit()
        result = {"ok": True, "student": student["name"], "status": status, "rssi": rssi}
    except sqlite3.IntegrityError:
        result = {"ok": True, "student": student["name"], "status": "already_marked"}

    db.close()
    return result

def api_attendance(session_id):
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        db.close()
        return {"error": "Session not found"}

    # All students with their attendance status
    students = db.execute("SELECT * FROM students").fetchall()
    logs = {r["student_id"]: dict(r) for r in
            db.execute("SELECT * FROM attendance_logs WHERE session_id=?", (session_id,)).fetchall()}

    result = []
    for s in students:
        log = logs.get(s["id"])
        result.append({
            "id": s["id"], "usn": s["usn"], "name": s["name"],
            "bt_device": s["bt_device"],
            "status": log["status"] if log else "absent",
            "rssi": log["rssi"] if log else None,
            "time_in": log["time_in"] if log else None,
        })

    db.close()
    return {
        "session": dict(session),
        "attendance": result,
        "summary": {
            "total": len(result),
            "present": sum(1 for r in result if r["status"]=="present"),
            "late":    sum(1 for r in result if r["status"]=="late"),
            "absent":  sum(1 for r in result if r["status"]=="absent"),
        }
    }

def api_add_student(body):
    db = get_db()
    try:
        db.execute("INSERT INTO students(usn,name,bt_device,course) VALUES(?,?,?,?)",
                   (body["usn"], body["name"], body.get("bt_device",""), body.get("course","CS301")))
        db.commit()
        result = {"ok": True}
    except sqlite3.IntegrityError as e:
        result = {"error": str(e)}
    db.close()
    return result

# ── HTML FRONTEND ────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AttendTrack</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f5f4f0;--surface:#ffffff;--border:#e2e0da;
  --text:#1a1916;--muted:#6b6860;--accent:#1a1916;
  --green:#15803d;--green-bg:#dcfce7;--green-border:#86efac;
  --amber:#92400e;--amber-bg:#fef3c7;--amber-border:#fcd34d;
  --red:#991b1b;--red-bg:#fee2e2;--red-border:#fca5a5;
  --blue:#1e40af;--blue-bg:#dbeafe;--blue-border:#93c5fd;
  --mono:'IBM Plex Mono',monospace;--sans:'IBM Plex Sans',sans-serif;
  --r:6px;
}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;min-height:100vh}
header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{font-family:var(--mono);font-size:15px;font-weight:600;letter-spacing:-0.02em}
.logo span{opacity:0.35}
.header-right{display:flex;align-items:center;gap:12px}
.session-badge{font-family:var(--mono);font-size:11px;padding:4px 10px;border-radius:20px;border:1px solid var(--border);background:var(--bg);color:var(--muted)}
.session-badge.active{border-color:var(--green-border);background:var(--green-bg);color:var(--green)}
.layout{display:grid;grid-template-columns:300px 1fr;min-height:calc(100vh - 56px)}
.sidebar{background:var(--surface);border-right:1px solid var(--border);padding:20px;display:flex;flex-direction:column;gap:20px}
.main{padding:24px;display:flex;flex-direction:column;gap:20px}
.section-label{font-family:var(--mono);font-size:10px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:16px}
.form-group{margin-bottom:12px}
.form-group label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px;font-family:var(--mono)}
.form-group input,.form-group select{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--r);font-family:var(--sans);font-size:13px;background:var(--bg);color:var(--text);outline:none}
.form-group input:focus,.form-group select:focus{border-color:var(--accent)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 14px;border-radius:var(--r);font-size:13px;font-weight:500;cursor:pointer;border:1px solid;transition:all 0.15s;font-family:var(--sans)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:0.88}
.btn-outline{background:transparent;color:var(--text);border-color:var(--border)}
.btn-outline:hover{background:var(--bg)}
.btn-danger{background:var(--red-bg);color:var(--red);border-color:var(--red-border)}
.btn-danger:hover{opacity:0.85}
.btn-success{background:var(--green-bg);color:var(--green);border-color:var(--green-border)}
.btn-success:hover{opacity:0.85}
.btn:disabled{opacity:0.4;cursor:not-allowed}
.btn-full{width:100%}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.metric{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px}
.metric-val{font-family:var(--mono);font-size:26px;font-weight:600;line-height:1}
.metric-label{font-size:11px;color:var(--muted);margin-top:4px}
.metric.present .metric-val{color:var(--green)}
.metric.late .metric-val{color:var(--amber)}
.metric.absent .metric-val{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{padding:8px 12px;text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:0.08em;color:var(--muted);border-bottom:1px solid var(--border);font-weight:600;background:var(--bg)}
td{padding:9px 12px;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:none}
tr:hover td{background:#faf9f7}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:20px;font-size:11px;font-family:var(--mono);font-weight:600;border:1px solid}
.badge.present{background:var(--green-bg);color:var(--green);border-color:var(--green-border)}
.badge.absent{background:var(--red-bg);color:var(--red);border-color:var(--red-border)}
.badge.late{background:var(--amber-bg);color:var(--amber);border-color:var(--amber-border)}
.rssi{font-family:var(--mono);font-size:12px}
.rssi.strong{color:var(--green)}.rssi.medium{color:var(--amber)}.rssi.weak{color:var(--red)}
.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.search{padding:7px 10px;border:1px solid var(--border);border-radius:var(--r);font-size:13px;background:var(--surface);outline:none;min-width:200px}
.search:focus{border-color:var(--accent)}
.filter-tabs{display:flex;gap:4px}
.ftab{padding:5px 12px;border-radius:20px;font-size:12px;cursor:pointer;border:1px solid var(--border);color:var(--muted);font-family:var(--mono);background:var(--surface);transition:all 0.12s}
.ftab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.toast{position:fixed;bottom:20px;right:20px;padding:10px 16px;border-radius:var(--r);font-size:13px;font-weight:500;z-index:999;animation:slideIn 0.2s ease;border:1px solid}
.toast.ok{background:var(--green-bg);color:var(--green);border-color:var(--green-border)}
.toast.err{background:var(--red-bg);color:var(--red);border-color:var(--red-border)}
@keyframes slideIn{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}
.bt-panel{border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
.bt-device{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border);cursor:pointer;transition:background 0.1s}
.bt-device:last-child{border-bottom:none}
.bt-device:hover{background:var(--bg)}
.bt-device.detected{background:var(--green-bg)}
.bt-name{font-size:12px;font-weight:500}
.bt-mac{font-family:var(--mono);font-size:10px;color:var(--muted)}
.bt-rssi{font-family:var(--mono);font-size:11px}
.sessions-list{display:flex;flex-direction:column;gap:6px;max-height:180px;overflow-y:auto}
.session-item{padding:8px 10px;border:1px solid var(--border);border-radius:var(--r);cursor:pointer;transition:all 0.12s}
.session-item:hover{border-color:var(--accent)}
.session-item.selected{border-color:var(--accent);background:var(--blue-bg)}
.session-item .s-course{font-weight:500;font-size:12px}
.session-item .s-meta{font-size:11px;color:var(--muted);font-family:var(--mono)}
.pct-bar{height:4px;background:var(--border);border-radius:2px;margin-top:6px;overflow:hidden}
.pct-fill{height:100%;background:var(--green);border-radius:2px;transition:width 0.4s}
.divider{border:none;border-top:1px solid var(--border);margin:4px 0}
.empty{text-align:center;padding:40px;color:var(--muted);font-size:13px}
</style>
</head>
<body>

<header>
  <div class="logo">ATTEND<span>TRACK</span></div>
  <div class="header-right">
    <div class="session-badge" id="session-badge">No active session</div>
    <button class="btn btn-outline" onclick="exportCSV()" id="export-btn" disabled>Export CSV</button>
  </div>
</header>

<div class="layout">
  <!-- SIDEBAR -->
  <div class="sidebar">

    <!-- Session Setup -->
    <div>
      <div class="section-label">Session</div>
      <div class="card" style="padding:14px">
        <div class="form-group">
          <label>Course</label>
          <select id="s-course">
            <option>CS301 - Data Structures</option>
            <option>CS401 - AI & ML</option>
            <option>MA201 - Linear Algebra</option>
            <option>EC101 - Electronics</option>
          </select>
        </div>
        <div class="form-group">
          <label>Room</label>
          <select id="s-room">
            <option>Room 101</option><option>Room 202</option>
            <option>Lab 3B</option><option>Seminar Hall</option>
          </select>
        </div>
        <div class="form-group" style="margin-bottom:14px">
          <label>RSSI Threshold (dBm)</label>
          <input type="number" id="s-rssi" value="-70" min="-100" max="-30">
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary" style="flex:1" id="start-btn" onclick="startSession()">Start</button>
          <button class="btn btn-danger" style="flex:1" id="end-btn" onclick="endSession()" disabled>End</button>
        </div>
      </div>
    </div>

    <!-- BT Scanner -->
    <div>
      <div class="section-label">Bluetooth Scan</div>
      <button class="btn btn-outline btn-full" id="scan-btn" onclick="scanBluetooth()" style="margin-bottom:8px">
        Scan for devices
      </button>
      <div id="no-bt" style="display:none;font-size:11px;color:var(--muted);margin-bottom:8px;padding:8px;background:var(--amber-bg);border:1px solid var(--amber-border);border-radius:var(--r)">
        Web BT not supported — using simulation
      </div>
      <div class="bt-panel" id="bt-list"></div>
    </div>

    <!-- Past Sessions -->
    <div>
      <div class="section-label">Past Sessions</div>
      <div class="sessions-list" id="sessions-list"></div>
    </div>

    <!-- Add Student -->
    <div>
      <div class="section-label">Add Student</div>
      <div class="card" style="padding:14px">
        <div class="form-group"><label>USN</label><input type="text" id="n-usn" placeholder="USN011"></div>
        <div class="form-group"><label>Name</label><input type="text" id="n-name" placeholder="Full name"></div>
        <div class="form-group" style="margin-bottom:14px"><label>BT Device MAC</label><input type="text" id="n-bt" placeholder="BT:KK:00:11:22"></div>
        <button class="btn btn-outline btn-full" onclick="addStudent()">Add student</button>
      </div>
    </div>

  </div>

  <!-- MAIN -->
  <div class="main">

    <!-- Metrics -->
    <div class="metrics">
      <div class="metric"><div class="metric-val" id="m-total">0</div><div class="metric-label">Total students</div></div>
      <div class="metric present"><div class="metric-val" id="m-present">0</div><div class="metric-label">Present</div>
        <div class="pct-bar"><div class="pct-fill" id="pct-bar" style="width:0%"></div></div>
      </div>
      <div class="metric late"><div class="metric-val" id="m-late">0</div><div class="metric-label">Late</div></div>
      <div class="metric absent"><div class="metric-val" id="m-absent">0</div><div class="metric-label">Absent</div></div>
    </div>

    <!-- Table -->
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
        <div style="font-weight:500;font-size:14px" id="table-title">Attendance Register</div>
        <div class="toolbar">
          <input class="search" placeholder="Search name or USN..." oninput="filterTable()" id="search">
          <div class="filter-tabs">
            <div class="ftab active" onclick="setFilter('all',this)">All</div>
            <div class="ftab" onclick="setFilter('present',this)">Present</div>
            <div class="ftab" onclick="setFilter('late',this)">Late</div>
            <div class="ftab" onclick="setFilter('absent',this)">Absent</div>
          </div>
        </div>
      </div>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th>Student</th><th>USN</th><th>BT Device</th>
              <th>RSSI</th><th>Time In</th><th>Status</th><th>Action</th>
            </tr>
          </thead>
          <tbody id="table-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
let currentSession = null;
let allRows = [];
let filterMode = 'all';

// ── API HELPERS ──────────────────────────────────────────────────
async function api(method, path, body) {
  const r = await fetch('/api'+path, {
    method, headers:{'Content-Type':'application/json'},
    body: body ? JSON.stringify(body) : undefined
  });
  return r.json();
}

// ── SESSION ──────────────────────────────────────────────────────
async function startSession() {
  const data = await api('POST', '/sessions/start', {
    course: document.getElementById('s-course').value,
    room:   document.getElementById('s-room').value,
    teacher:'Prof. Kumar',
    rssi_threshold: parseInt(document.getElementById('s-rssi').value)
  });
  currentSession = data.id;
  document.getElementById('start-btn').disabled = true;
  document.getElementById('end-btn').disabled = false;
  document.getElementById('export-btn').disabled = false;
  document.getElementById('session-badge').textContent = 'Session #'+data.id+' LIVE';
  document.getElementById('session-badge').className = 'session-badge active';
  toast('Session started!', 'ok');
  loadAttendance();
  loadSessions();
}

async function endSession() {
  if (!currentSession) return;
  await api('POST', '/sessions/'+currentSession+'/end', {});
  document.getElementById('start-btn').disabled = false;
  document.getElementById('end-btn').disabled = true;
  document.getElementById('session-badge').textContent = 'Session ended';
  document.getElementById('session-badge').className = 'session-badge';
  toast('Session ended', 'ok');
  loadAttendance();
  loadSessions();
}

// ── BLUETOOTH ────────────────────────────────────────────────────
async function scanBluetooth() {
  const btn = document.getElementById('scan-btn');
  btn.disabled = true;
  btn.textContent = 'Scanning...';

  if (navigator.bluetooth) {
    try {
      const device = await navigator.bluetooth.requestDevice({acceptAllDevices:true});
      const rssi = -55 - Math.floor(Math.random()*20);
      renderBTDevice(device.name||device.id, rssi, null);
      btn.textContent = 'Scan for devices';
      btn.disabled = false;
      return;
    } catch(e) {
      if (e.name !== 'NotFoundError') console.warn(e);
    }
  }

  // Simulation fallback
  document.getElementById('no-bt').style.display = 'block';
  const data = await api('GET', '/students', null);
  const students = data.slice(0, 4+Math.floor(Math.random()*4));
  const list = document.getElementById('bt-list');
  list.innerHTML = '';
  students.forEach((s, i) => {
    setTimeout(() => {
      const rssi = -50 - Math.floor(Math.random()*35);
      renderBTDevice(s.bt_device, rssi, s);
    }, i * 300);
  });
  setTimeout(() => { btn.textContent='Scan for devices'; btn.disabled=false; }, 2000);
}

function renderBTDevice(mac, rssi, student) {
  const list = document.getElementById('bt-list');
  const threshold = parseInt(document.getElementById('s-rssi').value);
  const strong = rssi >= threshold;
  const div = document.createElement('div');
  div.className = 'bt-device' + (strong?' detected':'');
  div.innerHTML = `
    <div>
      <div class="bt-name">${student ? student.name : 'Unknown device'}</div>
      <div class="bt-mac">${mac}</div>
    </div>
    <div style="display:flex;align-items:center;gap:8px">
      <span class="bt-rssi ${rssi>=-60?'rssi strong':rssi>=-75?'rssi medium':'rssi weak'}">${rssi} dBm</span>
      ${strong && currentSession ? `<button class="btn btn-success" style="padding:3px 8px;font-size:11px" onclick="markPresent('${mac}',${rssi})">Mark</button>` : ''}
    </div>`;
  list.appendChild(div);
  if (strong && currentSession) markPresent(mac, rssi);
}

async function markPresent(mac, rssi) {
  if (!currentSession) { toast('Start a session first','err'); return; }
  const r = await api('POST', '/detect', {
    session_id: currentSession, bt_device: mac, rssi: rssi
  });
  if (r.ok) {
    toast(r.student+' → '+r.status, 'ok');
    loadAttendance();
  } else if (r.error && !r.error.includes('threshold')) {
    toast(r.error, 'err');
  }
}

// ── MANUAL MARK ──────────────────────────────────────────────────
async function markManual(studentId) {
  if (!currentSession) { toast('Start a session first','err'); return; }
  const r = await api('POST', '/detect', {
    session_id: currentSession, student_id: studentId, rssi: -60
  });
  if (r.ok) { toast(r.student+' marked '+r.status, 'ok'); loadAttendance(); }
  else toast(r.error, 'err');
}

// ── ATTENDANCE TABLE ─────────────────────────────────────────────
async function loadAttendance() {
  if (!currentSession) {
    const students = await api('GET', '/students');
    allRows = students.map(s => ({...s, status:'absent', rssi:null, time_in:null}));
    renderTable();
    updateMetrics();
    return;
  }
  const data = await api('GET', '/attendance/'+currentSession);
  if (data.error) return;
  allRows = data.attendance;
  document.getElementById('table-title').textContent =
    data.session.course + ' · ' + data.session.room;
  updateMetrics(data.summary);
  renderTable();
}

function renderTable() {
  const q = document.getElementById('search').value.toLowerCase();
  const rows = allRows.filter(r => {
    if (filterMode !== 'all' && r.status !== filterMode) return false;
    if (q && !r.name.toLowerCase().includes(q) && !r.usn.toLowerCase().includes(q)) return false;
    return true;
  });
  const tbody = document.getElementById('table-body');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">No students found</td></tr>'; return; }
  tbody.innerHTML = rows.map(r => {
    const rssiClass = !r.rssi ? '' : r.rssi>=-60?'strong':r.rssi>=-75?'medium':'weak';
    const t = r.time_in ? r.time_in.slice(11,16) : '—';
    return `<tr>
      <td style="font-weight:500">${r.name}</td>
      <td style="font-family:var(--mono);font-size:11px;color:var(--muted)">${r.usn}</td>
      <td style="font-family:var(--mono);font-size:11px;color:var(--muted)">${r.bt_device||'—'}</td>
      <td><span class="rssi ${rssiClass}">${r.rssi ? r.rssi+' dBm' : '—'}</span></td>
      <td style="font-family:var(--mono);font-size:12px">${t}</td>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td>${r.status==='absent'&&currentSession ? `<button class="btn btn-outline" style="padding:3px 8px;font-size:11px" onclick="markManual(${r.id})">Mark present</button>` : ''}</td>
    </tr>`;
  }).join('');
}

function updateMetrics(s) {
  if (!s) {
    const total = allRows.length;
    s = {
      total,
      present: allRows.filter(r=>r.status==='present').length,
      late:    allRows.filter(r=>r.status==='late').length,
      absent:  allRows.filter(r=>r.status==='absent').length,
    };
  }
  document.getElementById('m-total').textContent   = s.total;
  document.getElementById('m-present').textContent = s.present;
  document.getElementById('m-late').textContent    = s.late;
  document.getElementById('m-absent').textContent  = s.absent;
  const pct = s.total ? Math.round((s.present+s.late)/s.total*100) : 0;
  document.getElementById('pct-bar').style.width = pct+'%';
}

// ── PAST SESSIONS ────────────────────────────────────────────────
async function loadSessions() {
  const sessions = await api('GET', '/sessions');
  const el = document.getElementById('sessions-list');
  if (!sessions.length) { el.innerHTML='<div style="color:var(--muted);font-size:12px">No sessions yet</div>'; return; }
  el.innerHTML = sessions.map(s => `
    <div class="session-item ${s.id===currentSession?'selected':''}" onclick="viewSession(${s.id})">
      <div class="s-course">${s.course}</div>
      <div class="s-meta">${s.room} · ${s.started_at.slice(0,10)} · <span style="color:${s.status==='active'?'var(--green)':'var(--muted)'}">${s.status}</span></div>
    </div>`).join('');
}

async function viewSession(id) {
  currentSession = id;
  document.getElementById('export-btn').disabled = false;
  loadAttendance();
}

// ── ADD STUDENT ──────────────────────────────────────────────────
async function addStudent() {
  const r = await api('POST', '/students/add', {
    usn:  document.getElementById('n-usn').value.trim(),
    name: document.getElementById('n-name').value.trim(),
    bt_device: document.getElementById('n-bt').value.trim(),
  });
  if (r.ok) {
    toast('Student added', 'ok');
    document.getElementById('n-usn').value='';
    document.getElementById('n-name').value='';
    document.getElementById('n-bt').value='';
    loadAttendance();
  } else toast(r.error, 'err');
}

// ── FILTER / SEARCH ──────────────────────────────────────────────
function setFilter(mode, el) {
  filterMode = mode;
  document.querySelectorAll('.ftab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  renderTable();
}
function filterTable() { renderTable(); }

// ── EXPORT CSV ───────────────────────────────────────────────────
function exportCSV() {
  if (!currentSession) return;
  let csv = 'USN,Name,BT Device,Status,RSSI,Time In\n';
  allRows.forEach(r => {
    csv += `${r.usn},${r.name},${r.bt_device||''},${r.status},${r.rssi||''},${r.time_in||''}\n`;
  });
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
  a.download = 'attendance_session_'+currentSession+'.csv';
  a.click();
}

// ── TOAST ────────────────────────────────────────────────────────
function toast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast '+type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

// ── INIT ─────────────────────────────────────────────────────────
loadAttendance();
loadSessions();
</script>
</body>
</html>"""

# ── HTTP SERVER ──────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {args[0]} {args[1]}")

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/" or p == "/index.html":
            self.send_html(HTML)
        elif p == "/api/students":
            self.send_json(api_students())
        elif p == "/api/sessions":
            self.send_json(api_sessions())
        elif p.startswith("/api/attendance/"):
            sid = int(p.split("/")[-1])
            self.send_json(api_attendance(sid))
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        body = self.read_body()
        if p == "/api/sessions/start":
            self.send_json(api_start_session(body))
        elif p.startswith("/api/sessions/") and p.endswith("/end"):
            sid = int(p.split("/")[-2])
            self.send_json(api_end_session(sid))
        elif p == "/api/detect":
            self.send_json(api_detect(body))
        elif p == "/api/students/add":
            self.send_json(api_add_student(body))
        else:
            self.send_json({"error": "Not found"}, 404)

if __name__ == "__main__":
    init_db()
    port = 8080
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"\n  AttendTrack running at http://localhost:{port}\n")
    print("  Commands:")
    print("    Start session → click Start button")
    print("    Scan BT       → click Scan for devices")
    print("    Mark manual   → click Mark present in table")
    print("    Export CSV    → click Export CSV button\n")
    server.serve_forever()
