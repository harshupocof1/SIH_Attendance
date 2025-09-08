import time
import base64
import io
from datetime import datetime
# from bson.objectid import ObjectId # --- DATABASE FEATURES DISABLED ---
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, login_user, logout_user, current_user, login_required, UserMixin
# from flask_pymongo import PyMongo # --- DATABASE FEATURES DISABLED ---
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
import qrcode

# --- App Initialization ---
app = Flask(__name__)
app.config.from_object(Config)
# mongo = PyMongo(app) # --- DATABASE FEATURES DISABLED ---
socketio = SocketIO(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- FAKE IN-MEMORY DATABASE FOR DEMO ---
# This dictionary replaces the MongoDB users collection for login purposes.
hashed_password = generate_password_hash("password", method='pbkdf2:sha256')
FAKE_USERS = {
    '1': {'_id': '1', 'username': 'teacher', 'password': hashed_password, 'is_teacher': True},
    '2': {'_id': '2', 'username': 'student1', 'password': hashed_password, 'is_teacher': False},
    '3': {'_id': '3', 'username': 'student2', 'password': hashed_password, 'is_teacher': False},
}
# --- END FAKE DATABASE ---

# Setup for generating and verifying secure, timed tokens
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# --- Custom User Class for Flask-Login (since we have no SQLAlchemy model) ---
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data["_id"])
        self.username = user_data["username"]
        self.password_hash = user_data["password"]
        self.is_teacher = user_data.get("is_teacher", False)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    # --- MODIFIED FOR DEMO: Looks in the FAKE_USERS dictionary ---
    if user_id in FAKE_USERS:
        return User(FAKE_USERS[user_id])
    return None

# --- Helper Functions ---
def generate_qr_code_image(token):
    """Generates a QR code image and returns it as a base64 string."""
    img = qrcode.make(token)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')

# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # --- MODIFIED FOR DEMO: Searches the FAKE_USERS dictionary ---
        user_data = None
        for u in FAKE_USERS.values():
            if u['username'] == username:
                user_data = u
                break
        
        if user_data and check_password_hash(user_data['password'], password):
            user = User(user_data)
            login_user(user)
            if user.is_teacher:
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('student_scan'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Core Application Routes ---
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/teacher')
@login_required
def teacher_dashboard():
    if not current_user.is_teacher:
        return "Access Denied", 403
    # --- MODIFIED FOR DEMO: Gets students from FAKE_USERS dictionary ---
    students = [u for u in FAKE_USERS.values() if not u['is_teacher']]
    return render_template('teacher_dashboard.html', students=students)

@app.route('/student')
@login_required
def student_scan():
    if current_user.is_teacher:
        return "Access Denied", 403
    return render_template('student_scan.html')

# --- API and WebSocket Logic ---
@socketio.on('connect', namespace='/teacher')
def teacher_connect():
    print("Teacher client connected")

@socketio.on('request_qr_code', namespace='/teacher')
def handle_qr_request():
    session_id = f"CLASS-42-{datetime.utcnow().strftime('%Y-%m-%d')}"
    token = serializer.dumps({'session_id': session_id, 'ts': time.time()})
    qr_image = generate_qr_code_image(token)
    
    emit('new_qr_code', {
        'image': qr_image,
        'refresh_rate': app.config['QR_REFRESH_RATE_SECONDS']
    })

@app.route('/api/mark_attendance', methods=['POST'])
@login_required
def api_mark_attendance():
    # --- DATABASE FEATURES DISABLED ---
    # This function now simulates a successful scan without saving to a database.
    # It still emits the WebSocket event so the teacher's dashboard updates in real-time.
    socketio.emit('student_checked_in', {
        'username': current_user.username,
        'timestamp': datetime.utcnow().strftime('%I:%M:%S %p')
    }, namespace='/teacher')
    
    return jsonify({'success': True, 'message': 'Attendance marked successfully (DEMO MODE)!'})

@app.route('/api/manual_mark', methods=['POST'])
@login_required
def manual_mark():
    # --- DATABASE FEATURES DISABLED ---
    return jsonify({'success': False, 'error': 'Manual marking is disabled in this demo.'}), 403

# --- Main Execution & Data Seeding ---
if __name__ == '__main__':
    # --- DATABASE FEATURES DISABLED ---
    # The data seeding section that connects to the database has been removed.


    socketio.run(app, host="0.0.0.0", port=port, debug=False,allow_unsafe_werkzeug=True)
