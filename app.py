import os
import time
import base64
import io
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, login_user, logout_user, current_user, login_required, UserMixin
from flask_pymongo import PyMongo
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
from pymongo.errors import ConnectionFailure, OperationFailure

# --- In-Memory Database for Fallback ---
# This class simulates the behavior of MongoDB collections using dictionaries.
class InMemoryDB:
    def __init__(self):
        self.users = {}
        self.attendance = {}
        self.latest_id = 0

    def find_one(self, collection_name, query):
        collection = getattr(self, collection_name)
        for doc_id, doc in collection.items():
            # Simple query matching for demonstration
            match = True
            for key, value in query.items():
                if key == '_id':
                    if isinstance(value, ObjectId):
                        if str(doc_id) != str(value):
                            match = False
                            break
                    else:
                        if doc_id != value:
                            match = False
                            break
                elif key in doc and doc[key] != value:
                    match = False
                    break
                elif key not in doc:
                    match = False
                    break
            if match:
                return doc.copy()
        return None

    def find(self, collection_name, query):
        collection = getattr(self, collection_name)
        results = []
        for doc_id, doc in collection.items():
            match = True
            for key, value in query.items():
                if key in doc and doc[key] != value:
                    match = False
                    break
                elif key not in doc:
                    match = False
                    break
            if match:
                results.append(doc.copy())
        return results

    def insert_one(self, collection_name, document):
        collection = getattr(self, collection_name)
        self.latest_id += 1
        new_id = self.latest_id
        document['_id'] = ObjectId(str(new_id).zfill(24)) # Dummy ObjectId
        collection[new_id] = document
        return type('result', (object,), {'inserted_id': document['_id']})

    def update_one(self, collection_name, query, update, upsert=False):
        collection = getattr(self, collection_name)
        for doc_id, doc in collection.items():
            match = True
            for key, value in query.items():
                if key in doc and doc[key] != value:
                    match = False
                    break
                elif key not in doc:
                    match = False
                    break
            if match:
                for op, fields in update.items():
                    if op == '$push':
                        for field, new_value in fields.items():
                            if field not in doc:
                                doc[field] = []
                            doc[field].append(new_value)
                return type('result', (object,), {'modified_count': 1})
        if upsert:
            new_doc = {}
            for key, value in query.items():
                new_doc[key] = value
            for op, fields in update.items():
                if op == '$push':
                    for field, new_value in fields.items():
                        new_doc[field] = [new_value]
            self.insert_one(collection_name, new_doc)
            return type('result', (object,), {'upserted_id': new_doc['_id']})

    def count_documents(self, collection_name, query={}):
        return len(self.find(collection_name, query))

# --- Configuration ---
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-super-secret-key-you-should-change'
    # Fallback to a development URI if environment variable is not set
    MONGO_URI = os.environ.get('MONGO_URI') or "mongodb://localhost:27017/attendance_app"
    QR_REFRESH_RATE_SECONDS = 2
    TOKEN_VALIDITY_SECONDS = 5

# --- App Initialization ---
app = Flask(__name__)
app.config.from_object(Config)

# Attempt to connect to MongoDB Atlas
mongo_client = None
in_memory_db = None
try:
    mongo_client = PyMongo(app)
    mongo_client.db.command('ping')
    print("Successfully connected to MongoDB Atlas.")
except (ConnectionFailure, OperationFailure) as e:
    print(f"MongoDB connection failed: {e}. Using in-memory database.")
    in_memory_db = InMemoryDB()

# Function to get the correct database object (MongoDB or in-memory)
def get_db():
    return mongo_client.db if mongo_client else in_memory_db

socketio = SocketIO(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Setup for generating and verifying secure, timed tokens
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

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
        self.student_name = user_data.get("student_name", user_data["username"])

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    # Handle the case where the in-memory database may not have a valid ObjectId
    if mongo_client:
        user_data = db.users.find_one({"_id": ObjectId(user_id)})
    else:
        user_data = db.users.find_one('users', {'_id': ObjectId(user_id)})

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
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        redirect_url = url_for('teacher_dashboard') if current_user.role == 'teacher' else url_for('student_dashboard')
        if current_user.role == 'demo':
            redirect_url = url_for('student_dashboard')
        return redirect(redirect_url)
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        user_data = db.users.find_one({"username": username})
        
        if not user_data:
            flash(f"No user found with username '{username}'.")
        elif not check_password_hash(user_data['password'], password):
            flash('Incorrect password, please try again.')
        else:
            user = User(user_data)
            login_user(user)
            redirect_url = url_for('teacher_dashboard') if user.role == 'teacher' else url_for('student_dashboard')
            if user.role == 'demo':
                redirect_url = url_for('student_dashboard')
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

    db = get_db()
    student_id = ObjectId(current_user.id)
    total_classes = db.attendance.count_documents('attendance', {})
    attended_classes = db.attendance.count_documents('attendance', {"records.user_id": student_id})
    percentage = round((attended_classes / total_classes) * 100, 2) if total_classes > 0 else 0

    today_date = datetime.utcnow().strftime('%Y-%d-%m')
    today_doc = db.attendance.find_one('attendance', {"date": today_date})
    classes_today = 0
    if today_doc:
        classes_today = len({rec['checkpoint'] for rec in today_doc.get("records", [])
                             if str(rec["user_id"]) == str(student_id)})

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
    db = get_db()
    date_str = request.args.get('date', datetime.utcnow().strftime('%Y-%d-%m'))
    daily_attendance_doc = db.attendance.find_one('attendance', {"date": date_str})
    return render_template('teacher_monitor.html', daily_doc=daily_attendance_doc, date_str=date_str)

@app.route('/teacher/manual_entry')
@login_required
def teacher_manual_entry():
    if current_user.role != 'teacher':
        return "Access Denied", 403
    db = get_db()
    today_date = datetime.utcnow().strftime('%Y-%d-%m')
    students = list(db.users.find('users', {"role": "student"}))
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
    return render_template('student_dashboard.html', name=name)

# --- New Teacher Dashboard Routes ---
@app.route('/teacher/students')
@login_required
def teacher_students():
    """Route to view all students."""
    if current_user.role != 'teacher':
        return "Access Denied", 403
    db = get_db()
    students = list(db.users.find('users', {"role": "student"}))
    return render_template('teacher_students.html', students=students)

@app.route('/teacher/assignments')
@login_required
def teacher_assignments():
    """Placeholder route for the assignments section."""
    if current_user.role != 'teacher':
        return "Access Denied", 403
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

    db = get_db()
    if db.users.find_one({"username": data['username']}):
        return jsonify({'success': False, 'error': 'Username already exists'}), 400

    new_student = {
        "username": data['username'],
        "password": generate_password_hash(data['password'], method='pbkdf2:sha256'),
        "role": "student",
        "student_name": data['studentName'],
        "roll_number": data['rollNumber'],
        "section": data['section'],
        "status": "absent"
    }
    result = db.users.insert_one('users', new_student)

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
        
        db = get_db()
        existing_record = db.attendance.find_one('attendance', {
            "date": date, "records": {"$elemMatch": {"user_id": ObjectId(current_user.id), "checkpoint": checkpoint}}
        })
        if existing_record: return jsonify({'success': False, 'error': f'Attendance already marked for {checkpoint}.'}), 409

        new_record = {"user_id": ObjectId(current_user.id), "username": current_user.username, "timestamp": datetime.utcnow(), "checkpoint": checkpoint, "method": "QR"}
        db.attendance.update_one('attendance', {"date": date}, {"$push": {"records": new_record}}, upsert=True)
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

    db = get_db()
    student_data = db.users.find_one('users', {"_id": ObjectId(student_id)})
    if not student_data: return jsonify({'success': False, 'error': 'Student not found'}), 404

    existing_record = db.attendance.find_one('attendance', {
        "date": date, "records": {"$elemMatch": {"user_id": ObjectId(student_id), "checkpoint": checkpoint}}
    })
    if existing_record: return jsonify({'success': False, 'error': f'{student_data["username"]} already marked for {checkpoint}.'}), 409

    new_record = {"user_id": ObjectId(student_id), "username": student_data["username"], "timestamp": datetime.utcnow(), "checkpoint": checkpoint, "method": "Manual"}
    db.attendance.update_one('attendance', {"date": date}, {"$push": {"records": new_record}}, upsert=True)
    socketio.emit('student_checked_in', {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, namespace='/teacher', broadcast=True)
    return jsonify({'success': True, 'username': student_data["username"]})

@app.route('/api/manual_bulk_mark', methods=['POST'])
@login_required
def manual_bulk_mark():
    if current_user.role != 'teacher': return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json()
    student_ids, date, checkpoint = data.get('student_ids', []), data.get('date'), data.get('checkpoint')
    if not student_ids or not date or not checkpoint: return jsonify({'success': False, 'error': 'Missing data'}), 400
    
    db = get_db()
    updated, skipped = [], []
    for sid in student_ids:
        student_data = db.users.find_one('users', {"_id": ObjectId(sid)})
        if not student_data:
            skipped.append(f"ID:{sid}"); continue

        existing_record = db.attendance.find_one('attendance', {
            "date": date, "records": {"$elemMatch": {"user_id": ObjectId(sid), "checkpoint": checkpoint}}
        })
        if existing_record:
            skipped.append(student_data["username"]); continue

        new_record = {"user_id": ObjectId(sid), "username": student_data["username"], "timestamp": datetime.utcnow(), "checkpoint": checkpoint, "method": "Manual"}
        db.attendance.update_one('attendance', {"date": date}, {"$push": {"records": new_record}}, upsert=True)
        updated.append(student_data["username"])
        socketio.emit('student_checked_in', {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, namespace='/teacher', broadcast=True)

    return jsonify({'success': True, 'updated': updated, 'skipped': skipped})

# --- Main Execution & Data Seeding ---
if __name__ == '__main__':
    with app.app_context():
        db = get_db()
        if db.users.count_documents('users') == 0:
            print("Seeding database with demo users...")
            hashed_password = generate_password_hash("password", method='pbkdf2:sha256')
            demo_users = [
                {"username": "teacher", "password": hashed_password, "role": "teacher", "section": "A", "status": "absent"},
                {"username": "student1", "password": hashed_password, "role": "student", "section": "A", "status": "absent"},
                {"username": "student2", "password": hashed_password, "role": "student", "section": "B", "status": "absent"},
                {"username": "demo", "password": hashed_password, "role": "demo", "section": "C", "status": "absent"},
            ]
            
            # Using insert_many for efficiency
            # The in-memory db also supports this method
            if mongo_client:
                 db.users.insert_many(demo_users)
            else:
                 for user in demo_users:
                     db.users.insert_one('users', user)
                 
            print("Demo users created.")

    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
