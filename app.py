#!/usr/bin/env python3
# app.py - fixed, minimal changes, includes in-memory Mongo mock when real Mongo isn't available

import os
import time
import base64
import io
import uuid
from datetime import datetime
from functools import wraps

# Optional imports (use real DB if available)
mongo = PyMongo(app)  # requires MONGO_URI in config
ObjectId = RealObjectId


from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, login_user, logout_user, current_user, login_required, UserMixin
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
import qrcode

# --- App Initialization ---
app = Flask(__name__)
app.config.from_object(Config)

# SocketIO: keep default async_mode (will work in dev). For production with Gunicorn, see note below.
socketio = SocketIO(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Serializer for tokens
serializer = URLSafeTimedSerializer(app.config.get('SECRET_KEY', 'dev-secret-key'))

# --- Constants ---
CHECKPOINTS = ["Morning", "Lunch", "Afternoon", "Evening"]

# -------------------------
# In-memory mock for Mongo
# -------------------------
class MockCollection:
    def __init__(self):
        self._docs = []  # list of dicts

    def find_one(self, query):
        # Very small query handler supporting {"_id": id} or {"date": date}
        for d in self._docs:
            match = True
            for key, val in query.items():
                # support nested query like {"records": {"$elemMatch": {...}}} handled elsewhere
                if key == "records":
                    match = False
                    break
                if key not in d:
                    match = False
                    break
                # compare by string representation so both real ObjectId and mock ids match
                if str(d[key]) != str(val):
                    match = False
                    break
            if match:
                return d
        return None

    def find(self, query=None):
        # Very naive: return all or those matching top-level simple query
        if not query:
            return list(self._docs)
        results = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if k not in d or str(d[k]) != str(v):
                    ok = False
                    break
            if ok:
                results.append(d)
        return results

    def insert_many(self, docs):
        for d in docs:
            if "_id" not in d:
                d["_id"] = MockObjectId()
            self._docs.append(d)
        return True

    def update_one(self, filter_q, update_q, upsert=False):
        # support filter {"date": date} and update like {"$push": {"records": new_record}}
        doc = self.find_one(filter_q)
        if doc:
            # handle $push
            if "$push" in update_q:
                for k, v in update_q["$push"].items():
                    if k not in doc:
                        doc[k] = []
                    doc[k].append(v)
            return {"matched_count": 1, "modified_count": 1}
        elif upsert:
            # create doc from filter and apply $push
            new_doc = {}
            for k, v in filter_q.items():
                new_doc[k] = v
            if "$push" in update_q:
                for k, val in update_q["$push"].items():
                    new_doc[k] = [val]
            if "_id" not in new_doc:
                new_doc["_id"] = MockObjectId()
            self._docs.append(new_doc)
            return {"matched_count": 0, "modified_count": 0, "upserted": True}
        return {"matched_count": 0, "modified_count": 0}

    def count_documents(self, query):
        if not query:
            return len(self._docs)
        # support counting attendance by checking records.user_id
        count = 0
        for d in self._docs:
            # if query is {"records.user_id": some_id}
            if "records.user_id" in query:
                uid = query["records.user_id"]
                for rec in d.get("records", []):
                    if str(rec.get("user_id")) == str(uid):
                        count += 1
                        break
            else:
                # simple top-level match
                ok = True
                for k, v in query.items():
                    if k not in d or str(d[k]) != str(v):
                        ok = False
                        break
                if ok:
                    count += 1
        return count

class MockDB:
    def __init__(self):
        self.users = MockCollection()
        self.attendance = MockCollection()

class MockObjectId:
    def __init__(self, val=None):
        if val is None:
            self._id = str(uuid.uuid4())
        else:
            # allow constructing from string id
            self._id = str(val)

    def __str__(self):
        return self._id

    def __repr__(self):
        return f"MockObjectId('{self._id}')"

    # equality compares by id string
    def __eq__(self, other):
        if isinstance(other, MockObjectId):
            return self._id == other._id
        return str(other) == self._id

# Choose real or mock
if REAL_MONGO_AVAILABLE and PyMongo is not None:
    mongo = PyMongo(app)  # expects MONGO_URI in Config
    ObjectId = RealObjectId
    print("Using real PyMongo.")
else:
    mongo = type("X", (), {"db": MockDB()})()
    ObjectId = lambda x=None: MockObjectId(x)
    print("PyMongo not available - using in-memory mock DB (demo mode).")

# -------------------------
# Helper / small utilities
# -------------------------
def ensure_demo_users():
    """Seed demo users when using mock DB and no users exist."""
    if REAL_MONGO_AVAILABLE:
        return
    # check count
    if mongo.db.users.count_documents({}) == 0:
        print("Seeding demo users into in-memory mock DB...")
        hashed_password = generate_password_hash("password", method='pbkdf2:sha256')
        demo_users = [
            {"_id": MockObjectId(), "username": "teacher", "password": hashed_password, "role": "teacher", "section": "A"},
            {"_id": MockObjectId(), "username": "student1", "password": hashed_password, "role": "student", "section": "A"},
            {"_id": MockObjectId(), "username": "student2", "password": hashed_password, "role": "student", "section": "B"},
        ]
        mongo.db.users.insert_many(demo_users)
        print("Demo users created.")

# --- Custom User Class for Flask-Login ---
# --- Constants ---
CHECKPOINTS = ["Period 1", "Period 2", "Period 3", "Period 4"]

# --- Custom User Class for Flask-Login ---
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data["_id"])
        self.username = user_data["username"]
        self.password_hash = user_data["password"]
        self.role = user_data.get("role", "student")
        self.section = user_data.get("section", "Unassigned")
        self.student_name = user_data.get("student_name", user_data["username"])  # fallback to username



    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    user_data = mongo.db.users.find_one({"_id": ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None

# --- Helper Functions ---
def generate_qr_code_image(token):
    """Generates a base64 encoded QR code image from a token."""
    img = qrcode.make(token)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')

# --- Authentication Routes ---

# main index

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        redirect_url = url_for('teacher_dashboard') if current_user.role == 'teacher' else url_for('student_scan')
        return redirect(redirect_url)
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user_data = mongo.db.users.find_one({"username": username})
        
        if not user_data:
            flash(f"No user found with username '{username}'.")
        elif not check_password_hash(user_data['password'], password):
            flash('Incorrect password, please try again.')
        else:
            user = User(user_data)
            login_user(user)
            redirect_url = url_for('teacher_dashboard') if user.role == 'teacher' else url_for('student_dashboard')
            return redirect(redirect_url)
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Core Application Routes ---
@app.route('/api/student_stats')
@login_required
def student_stats():
    if current_user.role == 'teacher':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    student_id = ObjectId(current_user.id)
    total_classes = mongo.db.attendance.count_documents({})
    attended_classes = mongo.db.attendance.count_documents({"records.user_id": student_id})
    percentage = round((attended_classes / total_classes) * 100, 2) if total_classes > 0 else 0

    today_date = datetime.utcnow().strftime('%Y-%d-%m')
    today_doc = mongo.db.attendance.find_one({"date": today_date})
    classes_today = 0
    if today_doc:
        classes_today = len({rec['checkpoint'] for rec in today_doc.get("records", [])
                             if rec["user_id"] == student_id})

    return jsonify({
        'success': True, 'percentage': percentage, 'classes_today': classes_today,
        'total_classes_today': len(CHECKPOINTS), 'emergency_contacts': 5
    })

@app.route('/teacher')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher':
        return "Access Denied", 403
    return render_template('teacher_dashboard.html')

@app.route('/teacher_qr')
@login_required
def teacher_qr():
    if current_user.role != 'teacher':
        return "Access Denied", 403
    today_date = datetime.utcnow().strftime('%Y-%d-%m')
    qr_refresh_ms = app.config.get('QR_REFRESH_RATE_SECONDS', 10) * 1000
    return render_template('teacher_qr.html', qr_refresh_ms=qr_refresh_ms, today_date=today_date, checkpoints=CHECKPOINTS)

@app.route('/teacher/monitor')
@login_required
def teacher_monitor():
    if current_user.role != 'teacher':
        return "Access Denied", 403
    date_str = request.args.get('date', datetime.utcnow().strftime('%Y-%d-%m'))
    daily_attendance_doc = mongo.db.attendance.find_one({"date": date_str})
    return render_template('teacher_monitor.html', daily_doc=daily_attendance_doc, date_str=date_str)

@app.route('/teacher/manual_entry')
@login_required
def teacher_manual_entry():
    if current_user.role != 'teacher':
        return "Access Denied", 403
    today_date = datetime.utcnow().strftime('%Y-%d-%m')
    students = list(mongo.db.users.find({"role": "student"}))
    sections = sorted(list({s.get("section", "Unassigned") for s in students}))
    return render_template(
        'teacher_manual_entry.html', students=students, sections=sections,
        today_date=today_date, checkpoints=CHECKPOINTS
    )

@app.route('/student')
@login_required
def student_dashboard():
    name = current_user.student_name
    if current_user.role == 'teacher':
        return "Access Denied", 403
    

    
    return render_template('student_dashboard.html' , name=name)

# --- New Teacher Dashboard Routes ---

@app.route('/teacher/students')
@login_required
def teacher_students():
    """Route to view all students."""
    if current_user.role != 'teacher':
        return "Access Denied", 403
    
    students = list(mongo.db.users.find({"role": "student"}))
    return render_template('teacher_students.html', students=students)

@app.route('/teacher/assignments')
@login_required
def teacher_assignments():
    """Placeholder route for the assignments section."""
    if current_user.role != 'teacher':
        return "Access Denied", 403
    
    # In a full implementation, you'd fetch assignment data from the database.
    assignments = [
        {"title": "Math Homework 1", "due_date": "2025-09-10", "status": "Pending"},
        {"title": "Science Project", "due_date": "2025-09-12", "status": "Submitted"}
    ]
    return render_template('teacher_assignments.html', assignments=assignments)

@app.route('/teacher/grades')
@login_required
def teacher_grades():
    """Placeholder route for the grades section."""
    if current_user.role != 'teacher':
        return "Access Denied", 403

    # In a full implementation, you'd fetch grade data.
    grades = [
        {"student_name": "Amit Sharma", "subject": "Math", "grade": "A"},
        {"student_name": "Priya Verma", "subject": "Science", "grade": "B+"},
        {"student_name": "Rohit Kumar", "subject": "English", "grade": "A-"},
    ]
    return render_template('teacher_grades.html', grades=grades)

@app.route('/teacher/add_student')
@login_required
def teacher_add_student():
    """Route to view the add student form."""
    if current_user.role != 'teacher':
        return "Access Denied", 403
    return render_template('add_student.html')
from werkzeug.security import generate_password_hash

@app.route('/api/add_student', methods=['POST'])
@login_required
def api_add_student():
    """API endpoint for teachers to add a new student."""
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'error': 'Access Denied'}), 403

    data = request.get_json()
    required_fields = ['studentName', 'rollNumber', 'section', 'username', 'password']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'success': False, 'error': f'Missing field: {field}'}), 400

    # Check for duplicate username
    if mongo.db.users.find_one({"username": data['username']}):
        return jsonify({'success': False, 'error': 'Username already exists'}), 400

    # Insert student into DB
    new_student = {
        "username": data['username'],
        "password": generate_password_hash(data['password'], method='pbkdf2:sha256'),
        "role": "student",
        "student_name": data['studentName'],
        "roll_number": data['rollNumber'],
        "section": data['section'],
        "status": "absent"
    }
    result = mongo.db.users.insert_one(new_student)

    return jsonify({'success': True, 'message': 'Student added successfully', 'id': str(result.inserted_id)})


# --- API and WebSocket Logic ---
@socketio.on('connect', namespace='/teacher')
def teacher_connect():
    print("Teacher client connected")

@socketio.on('request_qr_code', namespace='/teacher')
def handle_qr_request(data):
    date, checkpoint = data.get('date'), data.get('checkpoint')
    if not date or not checkpoint: return
    token = serializer.dumps({'date': date, 'checkpoint': checkpoint, 'ts': time.time()})
    qr_image = generate_qr_code_image(token)
    emit('new_qr_code', {'image': qr_image})

@app.route('/api/mark_attendance', methods=['POST'])
@login_required
def api_mark_attendance():
    token = request.get_json().get('token')
    if not token: return jsonify({'success': False, 'error': 'Token is missing.'}), 400

    try:
        payload = serializer.loads(token, max_age=app.config.get('TOKEN_VALIDITY_SECONDS', 15))
        date, checkpoint = payload['date'], payload['checkpoint']

        existing_record = mongo.db.attendance.find_one({
            "date": date, "records": {"$elemMatch": {"user_id": ObjectId(current_user.id), "checkpoint": checkpoint}}
        })
        if existing_record: return jsonify({'success': False, 'error': f'Attendance already marked for {checkpoint}.'}), 409

        new_record = {"user_id": ObjectId(current_user.id), "username": current_user.username, "timestamp": datetime.utcnow(), "checkpoint": checkpoint, "method": "QR"}
        mongo.db.attendance.update_one({"date": date}, {"$push": {"records": new_record}}, upsert=True)
        socketio.emit('student_checked_in', {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, namespace='/teacher', broadcast=True)
        return jsonify({'success': True, 'message': 'Attendance marked successfully!'})

    except SignatureExpired: return jsonify({'success': False, 'error': 'QR Code has expired.'}), 400
    except (BadTimeSignature, Exception): return jsonify({'success': False, 'error': 'Invalid QR Code.'}), 400

@app.route('/api/manual_mark', methods=['POST'])
@login_required
def manual_mark():
    if current_user.role != 'teacher': return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json()
    student_id, date, checkpoint = data.get('student_id'), data.get('date'), data.get('checkpoint')
    if not all([student_id, date, checkpoint]): return jsonify({'success': False, 'error': 'Missing data.'}), 400

    student_data = mongo.db.users.find_one({"_id": ObjectId(student_id)})
    if not student_data: return jsonify({'success': False, 'error': 'Student not found'}), 404

    existing_record = mongo.db.attendance.find_one({
        "date": date, "records": {"$elemMatch": {"user_id": ObjectId(student_id), "checkpoint": checkpoint}}
    })
    if existing_record: return jsonify({'success': False, 'error': f'{student_data["username"]} already marked for {checkpoint}.'}), 409

    new_record = {"user_id": ObjectId(student_id), "username": student_data["username"], "timestamp": datetime.utcnow(), "checkpoint": checkpoint, "method": "Manual"}
    mongo.db.attendance.update_one({"date": date}, {"$push": {"records": new_record}}, upsert=True)
    socketio.emit('student_checked_in', {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, namespace='/teacher', broadcast=True)
    return jsonify({'success': True, 'username': student_data["username"]})

@app.route('/api/manual_bulk_mark', methods=['POST'])
@login_required
def manual_bulk_mark():
    if current_user.role != 'teacher': return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json()
    student_ids, date, checkpoint = data.get('student_ids', []), data.get('date'), data.get('checkpoint')
    if not student_ids or not date or not checkpoint: return jsonify({'success': False, 'error': 'Missing data'}), 400

    updated, skipped = [], []
    for sid in student_ids:
        student_data = mongo.db.users.find_one({"_id": ObjectId(sid)})
        if not student_data:
            skipped.append(f"ID:{sid}"); continue

        existing_record = mongo.db.attendance.find_one({
            "date": date, "records": {"$elemMatch": {"user_id": ObjectId(sid), "checkpoint": checkpoint}}
        })
        if existing_record:
            skipped.append(student_data["username"]); continue

        new_record = {"user_id": ObjectId(sid), "username": student_data["username"], "timestamp": datetime.utcnow(), "checkpoint": checkpoint, "method": "Manual"}
        mongo.db.attendance.update_one({"date": date}, {"$push": {"records": new_record}}, upsert=True)
        updated.append(student_data["username"])
        socketio.emit('student_checked_in', {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, namespace='/teacher', broadcast=True)

    return jsonify({'success': True, 'updated': updated, 'skipped': skipped})

# --- Main Execution & Data Seeding ---
if __name__ == '__main__':
    # If using mock DB, seed demo users
    ensure_demo_users()

    # Expose port
    try:
        port = int(os.environ.get("PORT", 5000))
    except Exception:
        port = 5000

    # For local dev use socketio.run
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)

# If running under Gunicorn, leave `app` and `socketio` available for the server to use.



