import time
import base64
import io
import eventlet
eventlet.monkey_patch()
import queue 
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, login_user, logout_user, current_user, login_required, UserMixin
from flask_pymongo import PyMongo
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
import qrcode

from chatbot import CampusChatbot
# --- App Initialization ---
app = Flask(__name__)
app.config.from_object(Config)
mongo = PyMongo(app)
socketio = SocketIO(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Setup for generating and verifying secure, timed tokens
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# Initialize chatbot once
chatbot = CampusChatbot("chat_dataset.csv")


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

def _update_attendance_record(student_id, student_username, date, checkpoint, method):
    """
    Helper function to update a single attendance record.
    This consolidates the logic from multiple routes.
    """
    existing_record = mongo.db.attendance.find_one({
        "date": date,
        "records": {"$elemMatch": {"user_id": ObjectId(student_id), "checkpoint": checkpoint}}
    })
    
    if existing_record:
        return {'success': False, 'error': f'{student_username} already marked for {checkpoint}.'}, 409

    new_record = {
        "user_id": ObjectId(student_id),
        "username": student_username,
        "timestamp": datetime.utcnow(),
        "checkpoint": checkpoint,
        "method": method
    }
    mongo.db.attendance.update_one({"date": date}, {"$push": {"records": new_record}}, upsert=True)
    
    socketio.emit(
        'student_checked_in', 
        {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, 
        namespace='/teacher', 
        broadcast=True
    )
    return {'success': True, 'username': student_username}

# --- Authentication Routes ---
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
    students = list(mongo.db.users.find({"role": "student"}))
    return render_template('teacher_dashboard.html', students=students)

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
    
    student_count = mongo.db.users.count_documents({"role": "student"})
    
    return render_template('student_dashboard.html' , name=name , total_students = student_count)

@app.route("/api/chatbot", methods=["POST"])
@login_required
def chatbot_reply():
    data = request.get_json()
    user_text = data.get("message", "").strip()

    if not user_text:
        return jsonify({"success": False, "error": "Empty message"}), 400

    reply = chatbot.get_reply(user_text)
    # The backend's job is now just to return the text reply.
    # The frontend will handle the text-to-speech.
    return jsonify({"success": True, "reply": reply})

# --- New Teacher Dashboard Routes ---

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

    if mongo.db.users.find_one({"username": data['username']}):
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
    students_with_status = data.get('students')
    date = data.get('date')
    checkpoint = data.get('checkpoint')

    if not students_with_status or not date or not checkpoint:
        return jsonify({'success': False, 'error': 'Missing data'}), 400

    updated, skipped = [], []
    for student_data in students_with_status:
        student_id = student_data.get('id')
        is_present = student_data.get('present')

        if not student_id:
            skipped.append("Missing ID"); continue

        student_info = mongo.db.users.find_one({"_id": ObjectId(student_id)})
        if not student_info:
            skipped.append(f"ID:{student_id}"); continue
        
        # Check if attendance is already marked
        existing_record = mongo.db.attendance.find_one({
            "date": date, "records": {"$elemMatch": {"user_id": ObjectId(student_id), "checkpoint": checkpoint}}
        })

        if is_present:
            if not existing_record:
                # Mark present
                new_record = {
                    "user_id": ObjectId(student_id),
                    "username": student_info["username"],
                    "timestamp": datetime.utcnow(),
                    "checkpoint": checkpoint,
                    "method": "Manual",
                    "status": "present" # Adding status field
                }
                mongo.db.attendance.update_one(
                    {"date": date}, 
                    {"$push": {"records": new_record}}, 
                    upsert=True
                )
                updated.append(student_info["username"])
                socketio.emit('student_checked_in', {**new_record, 'timestamp': new_record['timestamp'].strftime('%I:%M:%S %p'), 'date': date}, namespace='/teacher', broadcast=True)
            else:
                skipped.append(f"{student_info['username']} (already present)")
        else:
            # Student is marked absent, check if they were present and remove the record
            if existing_record:
                mongo.db.attendance.update_one(
                    {"date": date},
                    {"$pull": {"records": {"user_id": ObjectId(student_id), "checkpoint": checkpoint}}}
                )
                updated.append(f"{student_info['username']} (marked absent)")
            else:
                skipped.append(f"{student_info['username']} (already absent)")
    
    return jsonify({'success': True, 'updated': updated, 'skipped': skipped})


# --- Main Execution & Data Seeding ---
# --- Main Execution & Data Seeding ---
if __name__ == '__main__':
    with app.app_context():
        if mongo.db.users.count_documents({}) == 0:
            print("Seeding database with demo users...")
            hashed_password = generate_password_hash("password", method='pbkdf2:sha256')
            demo_users = [
                {"username": "teacher", "password": hashed_password, "role": "teacher", "section": "A", "status": "absent"},
                {"username": "student1", "password": hashed_password, "role": "student", "section": "A", "status": "absent"},
                {"username": "student2", "password": hashed_password, "role": "student", "section": "B", "status": "absent"},
            ]
            mongo.db.users.insert_many(demo_users)
            print("Demo users created.")

    # ✅ Use Render's PORT or fallback to 5000
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")

    
    socketio.run(app, debug=True, host='127.0.0.1')








