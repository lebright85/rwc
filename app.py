import os
import logging
import time
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
import psycopg2
import io
import csv
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from urllib.parse import urlparse
from werkzeug.routing import BuildError
import math

# Configure logging
logging.basicConfig(level=logging.INFO, filename='app.log')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key')
app.jinja_env.add_extension('jinja2.ext.do')

bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Custom Jinja2 filter to safely handle url_for
def safe_url_for(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except BuildError:
        logger.warning(f"Failed to build URL for endpoint: {endpoint}")
        return '#'
app.jinja_env.filters['safe_url_for'] = safe_url_for

def get_db_connection():
    try:
        db_url = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/dbname')
        parsed_url = urlparse(db_url)
        conn = psycopg2.connect(
            dbname=parsed_url.path[1:],
            user=parsed_url.username,
            password=parsed_url.password,
            host=parsed_url.hostname,
            port=parsed_url.port
        )
        logger.info("Database connection established")
        return conn
    except psycopg2.Error as e:
        logger.error(f"Failed to connect to database: {e}")
        raise

scheduler = BackgroundScheduler(jobstores={
    'default': SQLAlchemyJobStore(url=os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/dbname'))
})

def init_db():
    if os.getenv('INITIALIZE_DB', 'false').lower() == 'true':
        max_retries = 3
        retry_delay = 5
        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("DROP TABLE IF EXISTS attendance CASCADE")
                c.execute("DROP TABLE IF EXISTS class_attendees CASCADE")
                c.execute("DROP TABLE IF EXISTS attendee_groups CASCADE")
                c.execute("DROP TABLE IF EXISTS groups CASCADE")
                c.execute("DROP TABLE IF EXISTS attendees CASCADE")
                c.execute("DROP TABLE IF EXISTS classes CASCADE")
                c.execute("DROP TABLE IF EXISTS users CASCADE")
                logger.info("Existing tables dropped")

                c.execute('''CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    credentials TEXT,
                    email TEXT
                )''')
                logger.info("Users table created")
                c.execute('''CREATE TABLE classes (
                    id SERIAL PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    class_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    group_hours TEXT NOT NULL,
                    counselor_id INTEGER,
                    group_type TEXT,
                    notes TEXT,
                    location TEXT,
                    recurring INTEGER NOT NULL DEFAULT 0,
                    frequency TEXT,
                    locked BOOLEAN NOT NULL DEFAULT FALSE,
                    FOREIGN KEY (counselor_id) REFERENCES users(id)
                )''')
                logger.info("Classes table created")
                c.execute('''CREATE TABLE groups (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                )''')
                logger.info("Groups table created")
                c.execute('''CREATE TABLE attendees (
                    id SERIAL PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    attendee_id TEXT UNIQUE NOT NULL,
                    group_details TEXT,
                    notes TEXT
                )''')
                logger.info("Attendees table created")
                c.execute('''CREATE TABLE attendee_groups (
                    attendee_id INTEGER,
                    group_id INTEGER,
                    PRIMARY KEY (attendee_id, group_id),
                    FOREIGN KEY (attendee_id) REFERENCES attendees(id) ON DELETE CASCADE,
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                )''')
                logger.info("Attendee_groups table created")
                c.execute('''CREATE TABLE attendance (
                    id SERIAL PRIMARY KEY,
                    class_id INTEGER,
                    attendee_id INTEGER,
                    time_in TEXT,
                    time_out TEXT,
                    attendance_status TEXT NOT NULL DEFAULT 'Present',
                    notes TEXT,
                    location TEXT,
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (attendee_id) REFERENCES attendees(id)
                )''')
                logger.info("Attendance table created")
                c.execute('''CREATE TABLE class_attendees (
                    class_id INTEGER,
                    attendee_id INTEGER,
                    PRIMARY KEY (class_id, attendee_id),
                    FOREIGN KEY (class_id) REFERENCES classes(id),
                    FOREIGN KEY (attendee_id) REFERENCES attendees(id)
                )''')
                logger.info("Class_attendees table created")

                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                          ('admin', bcrypt.generate_password_hash('admin123').decode('utf-8'), 'Admin User', 'admin', 'Treatment Director', 'admin@example.com'))
                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                          ('counselor1', bcrypt.generate_password_hash('counselor123').decode('utf-8'), 'Jane Doe', 'counselor', 'Clinical Trainee', 'jane@example.com'))
                counselor1_id = c.fetchone()[0]
                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                          ('counselor2', bcrypt.generate_password_hash('counselor456').decode('utf-8'), 'Mark Johnson', 'counselor', 'Therapist', 'mark@example.com'))
                counselor2_id = c.fetchone()[0]
                logger.info("Sample users inserted")

                c.execute("INSERT INTO groups (name) VALUES (%s) RETURNING id", ('Group A',))
                group_a_id = c.fetchone()[0]
                c.execute("INSERT INTO groups (name) VALUES (%s) RETURNING id", ('Group B',))
                group_b_id = c.fetchone()[0]
                c.execute("INSERT INTO groups (name) VALUES (%s) RETURNING id", ('Group C',))
                group_c_id = c.fetchone()[0]
                c.execute("INSERT INTO groups (name) VALUES (%s) RETURNING id", ('Group D',))
                group_d_id = c.fetchone()[0]
                logger.info("Sample groups inserted")

                today = '2025-08-03'
                tomorrow = (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                day_after = (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d')
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          ('Group A', 'Mindfulness', today, '10:00-11:30', counselor1_id, 'Therapy', 'Focus on relaxation', 'Office', 1, 'weekly'))
                mindfulness_id = c.fetchone()[0]
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          ('Group D', 'Yoga Session', today, '13:00-14:00', counselor2_id, 'Wellness', 'Beginner-friendly', 'Zoom', 0, None))
                yoga_id = c.fetchone()[0]
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          ('Group B', 'Stress Management', tomorrow, '14:00-15:30', counselor1_id, 'Workshop', 'Interactive session', 'Zoom', 0, None))
                stress_id = c.fetchone()[0]
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          ('Group C', 'Coping Skills', day_after, '09:00-10:30', counselor2_id, 'Therapy', 'Group discussion', 'Office', 0, None))
                coping_id = c.fetchone()[0]
                logger.info("Sample classes inserted")

                c.execute("INSERT INTO attendees (full_name, attendee_id, group_details, notes) VALUES (%s, %s, %s, %s) RETURNING id",
                          ('John Smith', 'ATT001', 'Morning Session', 'Requires extra support'))
                attendee_id = c.fetchone()[0]
                c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s)", (attendee_id, group_a_id))
                c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s)", (attendee_id, group_b_id))
                c.execute("INSERT INTO attendees (full_name, attendee_id, group_details, notes) VALUES (%s, %s, %s, %s) RETURNING id",
                          ('Jane Doe', 'ATT002', 'Morning Session', 'Good engagement'))
                attendee_id2 = c.fetchone()[0]
                c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s)", (attendee_id2, group_a_id))
                logger.info("Sample attendees inserted")

                c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, attendance_status, notes, location) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                          (mindfulness_id, attendee_id, '10:00', '11:30', 'Present', 'Actively participated', 'Office'))
                c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, attendance_status, notes, location) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                          (mindfulness_id, attendee_id2, '10:00', '11:30', 'Present', 'Good engagement', 'Office'))
                c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, attendance_status, notes, location) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                          (yoga_id, attendee_id, '13:00', '14:00', 'Present', 'Good participation', 'Zoom'))
                logger.info("Sample attendance inserted")

                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)", (mindfulness_id, attendee_id))
                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)", (mindfulness_id, attendee_id2))
                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)", (yoga_id, attendee_id))
                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)", (stress_id, attendee_id))
                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)", (coping_id, attendee_id))
                logger.info("Sample class-attendee assignments inserted")

                conn.commit()
                conn.close()
                logger.info("Database initialized successfully")
                return
            except psycopg2.Error as e:
                logger.error(f"Database initialization failed on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise
            except Exception as e:
                logger.error(f"Unexpected error during database initialization on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise
    else:
        logger.info("Database initialization skipped as INITIALIZE_DB is not set to 'true'")

# Run init_db on app startup only if configured
if os.getenv('INITIALIZE_DB', 'false').lower() == 'true':
    init_db()

scheduler.start()
logger.info(f"Starting application: {__file__}")
logger.info(f"App routes defined: {list(app.url_map.iter_rules())}")
logger.info("Registered routes: %s", [rule.endpoint for rule in app.url_map.iter_rules()])

def generate_recurring_classes():
    conn = get_db_connection()
    c = conn.cursor()
    today = datetime.today().strftime('%Y-%m-%d')
    max_date = (datetime.today() + timedelta(days=28)).strftime('%Y-%m-%d')
    c.execute("SELECT id, group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, frequency FROM classes WHERE recurring = 1 AND date <= %s",
              (max_date,))
    recurring_classes = c.fetchall()
    for cls in recurring_classes:
        class_id, group_name, class_name, start_date, group_hours, counselor_id, group_type, notes, location, frequency = cls
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            logger.error(f"Invalid date format for class {class_id}: {start_date}")
            continue
        if frequency == 'weekly':
            delta = timedelta(days=7)
        else:
            continue
        current_date = start + delta
        while current_date.strftime('%Y-%m-%d') <= max_date:
            new_date = current_date.strftime('%Y-%m-%d')
            c.execute("SELECT id FROM classes WHERE class_name = %s AND date = %s AND counselor_id = %s",
                      (class_name, new_date, counselor_id))
            if not c.fetchone():
                try:
                    c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                              (group_name, class_name, new_date, group_hours, counselor_id, group_type, notes, location, 0, None))
                    new_class_id = c.fetchone()[0]
                    c.execute("SELECT attendee_id FROM class_attendees WHERE class_id = %s", (class_id,))
                    attendees = c.fetchall()
                    for attendee in attendees:
                        c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                  (new_class_id, attendee[0]))
                except psycopg2.Error as e:
                    logger.error(f"Error generating recurring class for {class_name} on {new_date}: {e}")
            current_date += delta
    conn.commit()
    conn.close()
    logger.info("Recurring classes generated")

scheduler.add_job(generate_recurring_classes, 'interval', days=1, id='generate_recurring_classes', replace_existing=True)

class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = id
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, role FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()
    conn.close()
    if user:
        return User(user[0], user[1], user[2])
    return None

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        logger.info(f"Login attempt for username: {username}")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, username, password, role FROM users WHERE username = %s", (username,))
        user = c.fetchone()
        conn.close()
        if user and bcrypt.check_password_hash(user[2], password):
            user_obj = User(user[0], user[1], user[3])
            login_user(user_obj)
            logger.info(f"Login successful for user: {username}, role: {user[3]}")
            if user[3] == 'admin':
                logger.info("Redirecting to admin_dashboard")
                return redirect(url_for('admin_dashboard'))
            elif user[3] == 'counselor':
                logger.info("Redirecting to counselor_dashboard")
                try:
                    redirect_url = url_for('counselor_dashboard')
                    logger.info(f"Generated redirect URL: {redirect_url}")
                    return redirect(redirect_url)
                except BuildError as e:
                    logger.error(f"Failed to redirect to counselor_dashboard for user: {username}. Error: {str(e)}")
                    flash('Counselor dashboard is currently unavailable.')
                    return redirect(url_for('login'))
        flash('Invalid username or password')
        logger.warning(f"Login failed for username: {username}")
    return render_template('login.html')

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    today = datetime.today().strftime('%Y-%m-%d')
    try:
        c.execute("SELECT id, group_name, class_name, date, group_hours, location FROM classes WHERE date = %s ORDER BY group_name, group_hours",
                  (today,))
        today_classes = c.fetchall()
    except psycopg2.Error as e:
        logger.error(f"Database error in admin_dashboard: {e}")
        flash('Error loading dashboard. Please try again.', 'error')
        today_classes = []
    finally:
        conn.close()
    return render_template('admin_dashboard.html', today_classes=today_classes, today=today)

@app.route('/attendee_profile/<int:attendee_id>')
@login_required
def attendee_profile(attendee_id):
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT id, full_name, attendee_id, group_details, notes FROM attendees WHERE id = %s",
                  (attendee_id,))
        attendee = c.fetchone()
        if not attendee:
            flash('Attendee not found')
            return redirect(url_for('manage_attendees'))
        c.execute("SELECT c.id, c.group_name, c.class_name, c.date, c.group_hours, c.location FROM classes c JOIN class_attendees ca ON c.id = ca.class_id WHERE ca.attendee_id = %s",
                  (attendee_id,))
        assigned_classes = c.fetchall()
        c.execute("SELECT c.class_name, a.time_in, a.time_out, a.attendance_status, a.notes, a.location FROM attendance a JOIN classes c ON a.class_id = c.id WHERE a.attendee_id = %s",
                  (attendee_id,))
        attendance_records = c.fetchall()
        c.execute("""
            SELECT string_agg(g.name, ', ') FROM groups g
            JOIN attendee_groups ag ON g.id = ag.group_id
            WHERE ag.attendee_id = %s
        """, (attendee_id,))
        groups_str = c.fetchone()[0] or 'N/A'
    except psycopg2.Error as e:
        logger.error(f"Database error in attendee_profile: {e}")
        flash('Error loading attendee profile. Please try again.', 'error')
        return redirect(url_for('manage_attendees'))
    finally:
        conn.close()
    return render_template('attendee_profile.html', attendee=attendee, assigned_classes=assigned_classes, attendance_records=attendance_records, groups_str=groups_str)

@app.route('/reports', methods=['GET', 'POST'])
@login_required
def reports():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
        classes = c.fetchall()
        c.execute("SELECT id, full_name FROM attendees ORDER BY full_name")
        attendees = c.fetchall()
        c.execute("SELECT id, full_name FROM users WHERE role = 'counselor' ORDER BY full_name")
        counselors = c.fetchall()
        c.execute("SELECT id, name FROM groups ORDER BY name")
        groups = c.fetchall()
        
        today = datetime.today()
        default_start_date = today.strftime('%Y-%m-%d')
        default_end_date = (today + timedelta(days=6)).strftime('%Y-%m-%d')
        start_date = request.form.get('start_date', default_start_date)
        end_date = request.form.get('end_date', default_end_date)
        class_id = request.form.get('class_id', 'all')
        attendee_id = request.form.get('attendee_id', 'all')
        counselor_id = request.form.get('counselor_id', 'all')
        group_id = request.form.get('group_id', 'all')
        sort_by = request.form.get('sort_by', 'date')
        action = request.form.get('action', 'generate')
        
        logger.info(f"Reports filter values: start_date={start_date}, end_date={end_date}, class_id={class_id}, attendee_id={attendee_id}, counselor_id={counselor_id}, group_id={group_id}, sort_by={sort_by}, action={action}")
        
        class_query = """
            SELECT DISTINCT c.id, c.class_name, c.group_name, c.date, c.group_hours, c.location,
                   u.full_name AS counselor_name, u.credentials AS counselor_credentials
            FROM classes c
            JOIN users u ON c.counselor_id = u.id
            LEFT JOIN class_attendees ca ON c.id = ca.class_id
            LEFT JOIN attendees att ON ca.attendee_id = att.id
            LEFT JOIN attendee_groups ag ON att.id = ag.attendee_id
            WHERE 1=1
        """
        params = []
        
        try:
            if start_date and end_date:
                try:
                    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                    if start_dt <= end_dt:
                        class_query += " AND c.date >= %s AND c.date <= %s"
                        params.extend([start_date, end_date])
                    else:
                        flash('Start date must be before end date', 'error')
                        raise ValueError("Invalid date range")
                except ValueError as e:
                    logger.error(f"Invalid date format: {e}")
                    flash('Invalid date format. Please use YYYY-MM-DD', 'error')
                    raise
            else:
                flash('Start and end dates are required', 'error')
                raise ValueError("Missing date filters")

            if class_id and class_id != 'all':
                class_query += " AND c.id = %s"
                params.append(int(class_id))
            if counselor_id and counselor_id != 'all':
                class_query += " AND c.counselor_id = %s"
                params.append(int(counselor_id))
            if group_id and group_id != 'all':
                class_query += " AND ag.group_id = %s"
                params.append(int(group_id))
            
            if sort_by == 'group':
                class_query += " ORDER BY c.group_name ASC, c.date ASC"
            else:
                class_query += " ORDER BY c.date ASC"
            c.execute(class_query, params)
            class_records = c.fetchall()
            logger.info(f"Retrieved {len(class_records)} classes for report: {[r[1] for r in class_records]}")

            # Calculate group attendee totals
            group_attendee_counts = {}
            group_query = """
                SELECT c.group_name, COUNT(DISTINCT ca.attendee_id) as attendee_count
                FROM classes c
                JOIN class_attendees ca ON c.id = ca.class_id
                WHERE 1=1
            """
            group_params = []
            if start_date and end_date:
                group_query += " AND c.date >= %s AND c.date <= %s"
                group_params.extend([start_date, end_date])
            if class_id and class_id != 'all':
                group_query += " AND c.id = %s"
                group_params.append(int(class_id))
            if counselor_id and counselor_id != 'all':
                group_query += " AND c.counselor_id = %s"
                group_params.append(int(counselor_id))
            if group_id and group_id != 'all':
                group_query += " AND EXISTS (SELECT 1 FROM attendee_groups ag WHERE ag.attendee_id = ca.attendee_id AND ag.group_id = %s)"
                group_params.append(int(group_id))
            group_query += " GROUP BY c.group_name"
            c.execute(group_query, group_params)
            for row in c.fetchall():
                group_attendee_counts[row[0]] = row[1]
            logger.info(f"Group attendee counts: {group_attendee_counts}")

            report_data = []
            for class_record in class_records:
                class_id = class_record[0]
                attendee_query = """
                    SELECT att.full_name, att.attendee_id, string_agg(g.name, ', ') AS groups,
                           a.attendance_status, a.time_in, a.time_out, a.notes, a.location
                    FROM attendance a
                    JOIN attendees att ON a.attendee_id = att.id
                    LEFT JOIN attendee_groups ag ON att.id = ag.attendee_id
                    LEFT JOIN groups g ON ag.group_id = g.id
                    WHERE a.class_id = %s
                """
                attendee_params = [class_id]
                if attendee_id and attendee_id != 'all':
                    attendee_query += " AND a.attendee_id = %s"
                    attendee_params.append(int(attendee_id))
                if group_id and group_id != 'all':
                    attendee_query += " AND ag.group_id = %s"
                    attendee_params.append(int(group_id))
                attendee_query += " GROUP BY a.id, att.id ORDER BY att.full_name ASC"
                c.execute(attendee_query, attendee_params)
                attendee_records = c.fetchall()
                logger.info(f"Retrieved {len(attendee_records)} attendees for class_id {class_id}: {[r[0] for r in attendee_records]}")

                # Calculate present and absent counts
                c.execute("""
                    SELECT 
                        SUM(CASE WHEN a.attendance_status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                        SUM(CASE WHEN a.attendance_status = 'Absent' THEN 1 ELSE 0 END) AS absent_count
                    FROM attendance a
                    WHERE a.class_id = %s
                """, (class_id,))
                counts = c.fetchone()
                present_count = counts[0] or 0
                absent_count = counts[1] or 0

                report_data.append({
                    'class': class_record,
                    'attendees': attendee_records,
                    'present_count': present_count,
                    'absent_count': absent_count
                })
            
            if not report_data and action == 'generate':
                flash('No classes found for the selected filters', 'info')
            
            if action == 'download_csv':
                try:
                    output = io.StringIO()
                    writer = csv.writer(output)
                    writer.writerow(['Class Name', 'Group Name', 'Date', 'Group Hours', 'Location',
                                     'Counselor', 'Counselor Credentials', 'Present Count', 'Absent Count', 'Group Attendee Total',
                                     'Attendee Name', 'Attendee ID', 'Groups', 'Attendance Status', 'Time In', 'Time Out', 'Notes', 'Attendee Location'])
                    for data in report_data:
                        class_record = data['class']
                        group_attendee_total = group_attendee_counts.get(class_record[2], 0)
                        class_info = [
                            class_record[1], class_record[2], class_record[3], class_record[4],
                            class_record[5], class_record[6], class_record[7] or '',
                            data['present_count'], data['absent_count'], group_attendee_total
                        ]
                        if not data['attendees']:
                            writer.writerow(class_info + ['No attendees', '', '', '', '', '', ''])
                        else:
                            for idx, attendee in enumerate(data['attendees']):
                                row = class_info if idx == 0 else ['', '', '', '', '', '', '', '', '', '']
                                row += [
                                    attendee[0] or 'No attendees', attendee[1] or '',
                                    attendee[2] or 'N/A', attendee[3] or 'Present',
                                    attendee[4] or '', attendee[5] or '',
                                    attendee[6] or '', attendee[7] or ''
                                ]
                                writer.writerow(row)
                    csv_content = output.getvalue()
                    output.close()
                    logger.info("CSV file generated successfully with present/absent counts and group attendee totals")
                    conn.close()
                    return Response(
                        csv_content,
                        mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename=attendance_report.csv'}
                    )
                except Exception as e:
                    logger.error(f"Error generating CSV: {e}")
                    flash('An error occurred while generating the CSV file', 'error')
                    conn.close()
                    return redirect(url_for('reports'))
        
        except (psycopg2.Error, ValueError) as e:
            logger.error(f"Error executing report query: {e}")
            report_data = []
            if action == 'generate':
                flash('Error generating report. Please try again.', 'error')
        
    except psycopg2.Error as e:
        logger.error(f"Database error in reports: {e}")
        flash('Error loading reports page. Please try again.', 'error')
        classes, attendees, counselors, groups, report_data, group_attendee_counts = [], [], [], [], [], {}
    
    conn.close()
    return render_template('reports.html', classes=classes, attendees=attendees, counselors=counselors, groups=groups,
                           report_data=report_data, group_attendee_counts=group_attendee_counts,
                           start_date=start_date, end_date=end_date, 
                           class_id=class_id, attendee_id=attendee_id, counselor_id=counselor_id, group_id=group_id, sort_by=sort_by)

@app.route('/manage_groups', methods=['GET', 'POST'])
@login_required
def manage_groups():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'add':
                name = request.form['name']
                c.execute("INSERT INTO groups (name) VALUES (%s)", (name,))
                conn.commit()
                flash('Group added successfully')
            elif action == 'edit':
                group_id = request.form['group_id']
                name = request.form['name']
                c.execute("UPDATE groups SET name = %s WHERE id = %s", (name, group_id))
                conn.commit()
                flash('Group updated successfully')
            elif action == 'delete':
                group_id = request.form['group_id']
                c.execute("SELECT COUNT(*) FROM attendee_groups WHERE group_id = %s", (group_id,))
                count = c.fetchone()[0]
                if count > 0:
                    flash('Cannot delete group because it is assigned to attendees')
                else:
                    c.execute("DELETE FROM groups WHERE id = %s", (group_id,))
                    conn.commit()
                    flash('Group deleted successfully')
        except psycopg2.IntegrityError:
            flash('Group name already exists')
        except psycopg2.Error as e:
            logger.error(f"Database error in manage_groups: {e}")
            flash('An error occurred while processing your request', 'error')
        except Exception as e:
            logger.error(f"Unexpected error in manage_groups: {e}")
            flash('An unexpected error occurred', 'error')
    try:
        c.execute("SELECT id, name FROM groups ORDER BY name")
        groups = c.fetchall()
        group_attendees = {}
        for group in groups:
            c.execute("""
                SELECT a.id, a.full_name, a.attendee_id
                FROM attendees a
                JOIN attendee_groups ag ON a.id = ag.attendee_id
                WHERE ag.group_id = %s
                ORDER BY a.full_name
            """, (group[0],))
            group_attendees[group[0]] = c.fetchall()
    except psycopg2.Error as e:
        logger.error(f"Database error fetching groups or attendees: {e}")
        flash('Error loading groups. Please try again.', 'error')
        groups = []
        group_attendees = {}
    finally:
        conn.close()
    return render_template('manage_groups.html', groups=groups, group_attendees=group_attendees)

@app.route('/manage_users', methods=['GET', 'POST'])
@login_required
def manage_users():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'add_counselor':
                username = request.form['username']
                password = request.form['password']
                full_name = request.form['full_name']
                credentials = request.form['credentials']
                email = request.form['email']
                hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s)",
                          (username, hashed_password, full_name, 'counselor', credentials, email))
                conn.commit()
                flash('Counselor added successfully')
            elif action == 'add_admin':
                username = request.form['username']
                password = request.form['password']
                full_name = request.form['full_name']
                credentials = request.form['credentials']
                email = request.form['email']
                hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s)",
                          (username, hashed_password, full_name, 'admin', credentials, email))
                conn.commit()
                flash('Admin added successfully')
            elif action == 'edit_counselor':
                counselor_id = request.form['counselor_id']
                username = request.form['username']
                full_name = request.form['full_name']
                credentials = request.form['credentials']
                email = request.form['email']
                password = request.form.get('password', '')
                if password:
                    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                    c.execute("UPDATE users SET username = %s, password = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'counselor'",
                              (username, hashed_password, full_name, credentials, email, counselor_id))
                else:
                    c.execute("UPDATE users SET username = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'counselor'",
                              (username, full_name, credentials, email, counselor_id))
                conn.commit()
                flash('Counselor updated successfully')
            elif action == 'edit_admin':
                admin_id = request.form['admin_id']
                username = request.form['username']
                full_name = request.form['full_name']
                credentials = request.form['credentials']
                email = request.form['email']
                password = request.form.get('password', '')
                if password:
                    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                    c.execute("UPDATE users SET username = %s, password = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'admin'",
                              (username, hashed_password, full_name, credentials, email, admin_id))
                else:
                    c.execute("UPDATE users SET username = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'admin'",
                              (username, full_name, credentials, email, admin_id))
                conn.commit()
                flash('Admin updated successfully')
            elif action == 'delete_counselor':
                counselor_id = request.form['counselor_id']
                c.execute("DELETE FROM classes WHERE counselor_id = %s", (counselor_id,))
                c.execute("DELETE FROM users WHERE id = %s AND role = 'counselor'", (counselor_id,))
                conn.commit()
                flash('Counselor and associated classes deleted successfully')
            elif action == 'delete_admin':
                admin_id = request.form['admin_id']
                if int(admin_id) == current_user.id:
                    flash('You cannot delete your own account')
                else:
                    c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
                    admin_count = c.fetchone()[0]
                    if admin_count <= 1:
                        flash('Cannot delete the last admin account')
                    else:
                        c.execute("DELETE FROM classes WHERE counselor_id = %s", (admin_id,))
                        c.execute("DELETE FROM users WHERE id = %s AND role = 'admin'", (admin_id,))
                        conn.commit()
                        flash('Admin and associated classes deleted successfully')
        except psycopg2.IntegrityError:
            flash('Username already exists')
        except psycopg2.Error as e:
            logger.error(f"Database error in manage_users: {e}")
            flash('An error occurred while processing your request', 'error')
        except Exception as e:
            logger.error(f"Unexpected error in manage_users: {e}")
            flash('An unexpected error occurred', 'error')
    try:
        c.execute("SELECT id, username, full_name, role, credentials, email FROM users WHERE role IN ('counselor', 'admin')")
        users = c.fetchall()
    except psycopg2.Error as e:
        logger.error(f"Database error fetching users: {e}")
        flash('Error loading users. Please try again.', 'error')
        users = []
    finally:
        conn.close()
    return render_template('manage_users.html', users=users, current_user_id=current_user.id)

@app.route('/manage_classes', methods=['GET', 'POST'])
@login_required
def manage_classes():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    # Sorting and filtering parameters
    sort_by = request.args.get('sort_by', 'date')
    group_filter = request.args.get('group_filter', 'all')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    page = int(request.args.get('page', 1))
    per_page = 10

    if request.method == 'POST':
        action = request.form.get('action')
        logger.info(f"Received action in manage_classes: {action}")
        try:
            if action == 'add':
                group_name = request.form['group_name']
                class_name = request.form['class_name']
                date = request.form['date']
                group_hours = request.form['group_hours']
                counselor_id = request.form['counselor_id']
                group_type = request.form['group_type']
                notes = request.form['notes']
                location = request.form['location']
                recurring = 1 if request.form.get('recurring') == 'on' else 0
                frequency = request.form.get('frequency') if recurring else None
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency))
                new_class_id = c.fetchone()[0]
                conn.commit()
                flash('Class added successfully')
                if recurring and frequency == 'weekly':
                    start = datetime.strptime(date, '%Y-%m-%d')
                    max_date = (datetime.today() + timedelta(days=28)).strftime('%Y-%m-%d')
                    current_date = start + timedelta(days=7)
                    while current_date.strftime('%Y-%m-%d') <= max_date:
                        new_date = current_date.strftime('%Y-%m-%d')
                        c.execute("SELECT id FROM classes WHERE class_name = %s AND date = %s AND counselor_id = %s",
                                  (class_name, new_date, counselor_id))
                        if not c.fetchone():
                            c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                                      (group_name, class_name, new_date, group_hours, counselor_id, group_type, notes, location, 0, None))
                            new_instance_id = c.fetchone()[0]
                        current_date += timedelta(days=7)
                    conn.commit()
            elif action == 'edit':
                class_id = request.form['class_id']
                group_name = request.form['group_name']
                class_name = request.form['class_name']
                date = request.form['date']
                group_hours = request.form['group_hours']
                counselor_id = request.form['counselor_id']
                group_type = request.form['group_type']
                notes = request.form['notes']
                location = request.form['location']
                recurring = 1 if request.form.get('recurring') == 'on' else 0
                frequency = request.form.get('frequency') if recurring else None
                propagate = request.form.get('propagate', 'off') == 'on'
                c.execute("UPDATE classes SET group_name = %s, class_name = %s, date = %s, group_hours = %s, counselor_id = %s, group_type = %s, notes = %s, location = %s, recurring = %s, frequency = %s WHERE id = %s",
                          (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency, class_id))
                conn.commit()
                attendee_ids = request.form.getlist('attendee_ids')
                c.execute("DELETE FROM class_attendees WHERE class_id = %s", (class_id,))
                for attendee_id in attendee_ids:
                    c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (class_id, attendee_id))
                conn.commit()
                logger.info(f"Class {class_id} updated successfully")
                flash('Class updated successfully')
                if propagate and recurring:
                    c.execute("SELECT id FROM classes WHERE class_name = %s AND counselor_id = %s AND date > %s",
                              (class_name, counselor_id, date))
                    future_ids = [row[0] for row in c.fetchall()]
                    for future_id in future_ids:
                        c.execute("UPDATE classes SET group_name = %s, class_name = %s, group_hours = %s, counselor_id = %s, group_type = %s, notes = %s, location = %s WHERE id = %s",
                                  (group_name, class_name, group_hours, counselor_id, group_type, notes, location, future_id))
                        c.execute("DELETE FROM class_attendees WHERE class_id = %s", (future_id,))
                        for attendee_id in attendee_ids:
                            c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                      (future_id, attendee_id))
                    conn.commit()
                    logger.info(f"Propagated changes to {len(future_ids)} future recurring classes")
                    flash('Changes propagated to future recurring classes')
            elif action == 'delete' or action == 'delete_all_future':
                class_id = request.form['class_id']
                if action == 'delete_all_future':
                    c.execute("SELECT class_name, counselor_id, date FROM classes WHERE id = %s", (class_id,))
                    class_info = c.fetchone()
                    if class_info:
                        class_name, counselor_id, current_date = class_info
                        c.execute("DELETE FROM class_attendees WHERE class_id IN (SELECT id FROM classes WHERE class_name = %s AND counselor_id = %s AND date >= %s)",
                                  (class_name, counselor_id, current_date))
                        c.execute("DELETE FROM attendance WHERE class_id IN (SELECT id FROM classes WHERE class_name = %s AND counselor_id = %s AND date >= %s)",
                                  (class_name, counselor_id, current_date))
                        c.execute("DELETE FROM classes WHERE class_name = %s AND counselor_id = %s AND date >= %s",
                                  (class_name, counselor_id, current_date))
                        conn.commit()
                        logger.info(f"Deleted class {class_id} and all future recurring instances")
                        flash('Class and all future recurring instances deleted successfully')
                    else:
                        flash('Class not found', 'error')
                else:
                    c.execute("DELETE FROM class_attendees WHERE class_id = %s", (class_id,))
                    c.execute("DELETE FROM attendance WHERE class_id = %s", (class_id,))
                    c.execute("DELETE FROM classes WHERE id = %s", (class_id,))
                    conn.commit()
                    logger.info(f"Class {class_id} deleted successfully")
                    flash('Class deleted successfully')
            elif action == 'assign_attendee':
                class_id = request.form['class_id']
                attendee_id = request.form['attendee_id']
                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                          (class_id, attendee_id))
                conn.commit()
                logger.info(f"Attendee {attendee_id} assigned to class {class_id}")
                flash('Attendee assigned successfully')
            elif action == 'unassign_attendee':
                class_id = request.form['class_id']
                attendee_id = request.form['attendee_id']
                c.execute("DELETE FROM class_attendees WHERE class_id = %s AND attendee_id = %s", (class_id, attendee_id))
                conn.commit()
                logger.info(f"Attendee {attendee_id} unassigned from class {class_id}")
                flash('Attendee unassigned successfully')
            elif action == 'assign_group':
                class_id = request.form['class_id']
                group_id = request.form['group_id']
                c.execute("SELECT attendee_id FROM attendee_groups WHERE group_id = %s", (group_id,))
                attendee_ids = [row[0] for row in c.fetchall()]
                if not attendee_ids:
                    logger.warning(f"No attendees found in group {group_id} for class {class_id}")
                    flash(f'No attendees found in group', 'error')
                else:
                    assigned_count = 0
                    for attendee_id in attendee_ids:
                        try:
                            c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                      (class_id, attendee_id))
                            assigned_count += c.rowcount
                        except psycopg2.Error as e:
                            logger.error(f"Error assigning attendee {attendee_id} to class {class_id}: {e}")
                    conn.commit()
                    if assigned_count > 0:
                        logger.info(f"Assigned {assigned_count} attendees from group {group_id} to class {class_id}")
                        flash(f'Assigned {assigned_count} attendees from group to class', 'success')
                    else:
                        logger.info(f"No new attendees assigned from group {group_id} to class {class_id}")
                        flash(f'No new attendees assigned from group (already assigned or no attendees)', 'info')
            elif action == 'toggle_lock':
                class_id = request.form['class_id']
                locked = request.form['locked'] == 'true'
                c.execute("UPDATE classes SET locked = %s WHERE id = %s", (locked, class_id))
                conn.commit()
                logger.info(f"Class {class_id} {'locked' if locked else 'unlocked'}")
                flash(f'Class {"locked" if locked else "unlocked"} successfully')
            else:
                logger.warning(f"Unknown action received: {action}")
                flash('Invalid action', 'error')
        except psycopg2.IntegrityError:
            flash('Action failed due to duplicate or invalid data')
        except psycopg2.Error as e:
            logger.error(f"Database error in manage_classes: {e}")
            flash('Error processing your request. Please try again.', 'error')
            conn.rollback()
        except Exception as e:
            logger.error(f"Unexpected error in manage_classes: {e}")
            flash('An unexpected error occurred. Please try again.', 'error')
            conn.rollback()

    try:
        # Count total classes for pagination
        count_query = "SELECT COUNT(*) FROM classes c WHERE 1=1"
        count_params = []
        if group_filter != 'all':
            count_query += " AND c.group_name = %s"
            count_params.append(group_filter)
        if start_date:
            count_query += " AND c.date >= %s"
            count_params.append(start_date)
        if end_date:
            count_query += " AND c.date <= %s"
            count_params.append(end_date)
        c.execute(count_query, count_params)
        total_classes = c.fetchone()[0]
        total_pages = math.ceil(total_classes / per_page)
        page = max(1, min(page, total_pages))  # Ensure page is within bounds
        offset = (page - 1) * per_page

        c.execute("SELECT id, username, full_name FROM users WHERE role = 'counselor'")
        counselors = c.fetchall()
        classes_query = """
            SELECT c.id, c.group_name, c.class_name, c.date, c.group_hours, c.counselor_id, c.group_type, c.notes, c.location, c.recurring, c.frequency, u.full_name, c.locked
            FROM classes c
            LEFT JOIN users u ON c.counselor_id = u.id
            WHERE 1=1
        """
        params = []
        if group_filter != 'all':
            classes_query += " AND c.group_name = %s"
            params.append(group_filter)
        if start_date:
            classes_query += " AND c.date >= %s"
            params.append(start_date)
        if end_date:
            classes_query += " AND c.date <= %s"
            params.append(end_date)
        if sort_by == 'group':
            classes_query += " ORDER BY c.group_name ASC, c.date ASC"
        else:
            classes_query += " ORDER BY c.date ASC"
        classes_query += " LIMIT %s OFFSET %s"
        params.extend([per_page, offset])
        c.execute(classes_query, params)
        classes = c.fetchall()
        c.execute("SELECT id, full_name, attendee_id FROM attendees ORDER BY full_name ASC")
        attendees = c.fetchall()
        c.execute("SELECT id, name FROM groups ORDER BY name")
        groups = c.fetchall()
        class_attendees = {}
        for class_ in classes:
            c.execute("""
                SELECT a.id, a.full_name, a.attendee_id, string_agg(g.name, ', ') AS groups
                FROM attendees a
                JOIN class_attendees ca ON a.id = ca.attendee_id
                LEFT JOIN attendee_groups ag ON a.id = ag.attendee_id
                LEFT JOIN groups g ON ag.group_id = g.id
                WHERE ca.class_id = %s
                GROUP BY a.id
                ORDER BY a.full_name ASC
            """, (class_[0],))
            class_attendees[class_[0]] = c.fetchall()
    except psycopg2.Error as e:
        logger.error(f"Database error in manage_classes data fetch: {e}")
        flash('Error loading classes. Please try again.', 'error')
        conn.close()
        return render_template('manage_classes.html', counselors=[], classes=[], attendees=[], class_attendees={}, groups=[], sort_by=sort_by, group_filter=group_filter, start_date=start_date, end_date=end_date, page=1, total_pages=1)
    finally:
        conn.close()

    return render_template('manage_classes.html', counselors=counselors, classes=classes, attendees=attendees, class_attendees=class_attendees, groups=groups, sort_by=sort_by, group_filter=group_filter, start_date=start_date, end_date=end_date, page=page, total_pages=total_pages)

@app.route('/counselor_manage_classes', methods=['GET', 'POST'])
@login_required
def counselor_manage_classes():
    if current_user.role != 'counselor':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    # Sorting and filtering parameters
    sort_by = request.args.get('sort_by', 'date')
    group_filter = request.args.get('group_filter', 'all')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    page = int(request.args.get('page', 1))
    per_page = 10

    if request.method == 'POST':
        action = request.form.get('action')
        logger.info(f"Received action in counselor_manage_classes: {action}")
        try:
            if action == 'add':
                group_name = request.form['group_name']
                class_name = request.form['class_name']
                date = request.form['date']
                group_hours = request.form['group_hours']
                counselor_id = current_user.id  # Restrict to current counselor
                group_type = request.form['group_type']
                notes = request.form['notes']
                location = request.form['location']
                recurring = 1 if request.form.get('recurring') == 'on' else 0
                frequency = request.form.get('frequency') if recurring else None
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency))
                new_class_id = c.fetchone()[0]
                conn.commit()
                flash('Class added successfully')
                if recurring and frequency == 'weekly':
                    start = datetime.strptime(date, '%Y-%m-%d')
                    max_date = (datetime.today() + timedelta(days=28)).strftime('%Y-%m-%d')
                    current_date = start + timedelta(days=7)
                    while current_date.strftime('%Y-%m-%d') <= max_date:
                        new_date = current_date.strftime('%Y-%m-%d')
                        c.execute("SELECT id FROM classes WHERE class_name = %s AND date = %s AND counselor_id = %s",
                                  (class_name, new_date, counselor_id))
                        if not c.fetchone():
                            c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                                      (group_name, class_name, new_date, group_hours, counselor_id, group_type, notes, location, 0, None))
                            new_instance_id = c.fetchone()[0]
                        current_date += timedelta(days=7)
                    conn.commit()
            elif action == 'edit':
                class_id = request.form['class_id']
                group_name = request.form['group_name']
                class_name = request.form['class_name']
                date = request.form['date']
                group_hours = request.form['group_hours']
                counselor_id = current_user.id  # Restrict to current counselor
                group_type = request.form['group_type']
                notes = request.form['notes']
                location = request.form['location']
                recurring = 1 if request.form.get('recurring') == 'on' else 0
                frequency = request.form.get('frequency') if recurring else None
                propagate = request.form.get('propagate', 'off') == 'on'
                c.execute("SELECT locked FROM classes WHERE id = %s AND counselor_id = %s", (class_id, current_user.id))
                class_info = c.fetchone()
                if not class_info:
                    flash('Class not found or you are not authorized to edit it', 'error')
                elif class_info[0]:
                    flash('This class is locked and cannot be edited', 'error')
                else:
                    c.execute("UPDATE classes SET group_name = %s, class_name = %s, date = %s, group_hours = %s, counselor_id = %s, group_type = %s, notes = %s, location = %s, recurring = %s, frequency = %s WHERE id = %s AND counselor_id = %s",
                              (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency, class_id, current_user.id))
                    if c.rowcount == 0:
                        flash('Class not found or you are not authorized to edit it', 'error')
                    else:
                        conn.commit()
                        attendee_ids = request.form.getlist('attendee_ids')
                        c.execute("DELETE FROM class_attendees WHERE class_id = %s", (class_id,))
                        for attendee_id in attendee_ids:
                            c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                      (class_id, attendee_id))
                        conn.commit()
                        logger.info(f"Class {class_id} updated successfully by counselor {current_user.id}")
                        flash('Class updated successfully')
                        if propagate and recurring:
                            c.execute("SELECT id FROM classes WHERE class_name = %s AND counselor_id = %s AND date > %s",
                                      (class_name, counselor_id, date))
                            future_ids = [row[0] for row in c.fetchall()]
                            for future_id in future_ids:
                                c.execute("UPDATE classes SET group_name = %s, class_name = %s, group_hours = %s, counselor_id = %s, group_type = %s, notes = %s, location = %s WHERE id = %s",
                                          (group_name, class_name, group_hours, counselor_id, group_type, notes, location, future_id))
                                c.execute("DELETE FROM class_attendees WHERE class_id = %s", (future_id,))
                                for attendee_id in attendee_ids:
                                    c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                              (future_id, attendee_id))
                            conn.commit()
                            logger.info(f"Propagated changes to {len(future_ids)} future recurring classes")
                            flash('Changes propagated to future recurring classes')
            elif action == 'delete' or action == 'delete_all_future':
                class_id = request.form['class_id']
                c.execute("SELECT locked, class_name, counselor_id, date FROM classes WHERE id = %s AND counselor_id = %s", (class_id, current_user.id))
                class_info = c.fetchone()
                if not class_info:
                    flash('Class not found or you are not authorized to delete it', 'error')
                elif class_info[0]:
                    flash('This class is locked and cannot be deleted', 'error')
                else:
                    if action == 'delete_all_future':
                        class_name, counselor_id, current_date = class_info[1], class_info[2], class_info[3]
                        c.execute("DELETE FROM class_attendees WHERE class_id IN (SELECT id FROM classes WHERE class_name = %s AND counselor_id = %s AND date >= %s)",
                                  (class_name, counselor_id, current_date))
                        c.execute("DELETE FROM attendance WHERE class_id IN (SELECT id FROM classes WHERE class_name = %s AND counselor_id = %s AND date >= %s)",
                                  (class_name, counselor_id, current_date))
                        c.execute("DELETE FROM classes WHERE class_name = %s AND counselor_id = %s AND date >= %s",
                                  (class_name, counselor_id, current_date))
                        conn.commit()
                        logger.info(f"Deleted class {class_id} and all future recurring instances by counselor {current_user.id}")
                        flash('Class and all future recurring instances deleted successfully')
                    else:
                        c.execute("DELETE FROM class_attendees WHERE class_id = %s", (class_id,))
                        c.execute("DELETE FROM attendance WHERE class_id = %s", (class_id,))
                        c.execute("DELETE FROM classes WHERE id = %s AND counselor_id = %s", (class_id, current_user.id))
                        conn.commit()
                        logger.info(f"Class {class_id} deleted successfully by counselor {current_user.id}")
                        flash('Class deleted successfully')
            elif action == 'assign_attendee':
                class_id = request.form['class_id']
                attendee_id = request.form['attendee_id']
                c.execute("SELECT locked FROM classes WHERE id = %s AND counselor_id = %s", (class_id, current_user.id))
                class_info = c.fetchone()
                if not class_info:
                    flash('Class not found or you are not authorized to edit it', 'error')
                elif class_info[0]:
                    flash('This class is locked and cannot be edited', 'error')
                else:
                    c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (class_id, attendee_id))
                    conn.commit()
                    logger.info(f"Attendee {attendee_id} assigned to class {class_id} by counselor {current_user.id}")
                    flash('Attendee assigned successfully')
            elif action == 'unassign_attendee':
                class_id = request.form['class_id']
                attendee_id = request.form['attendee_id']
                c.execute("SELECT locked FROM classes WHERE id = %s AND counselor_id = %s", (class_id, current_user.id))
                class_info = c.fetchone()
                if not class_info:
                    flash('Class not found or you are not authorized to edit it', 'error')
                elif class_info[0]:
                    flash('This class is locked and cannot be edited', 'error')
                else:
                    c.execute("DELETE FROM class_attendees WHERE class_id = %s AND attendee_id = %s", (class_id, attendee_id))
                    conn.commit()
                    logger.info(f"Attendee {attendee_id} unassigned from class {class_id} by counselor {current_user.id}")
                    flash('Attendee unassigned successfully')
            elif action == 'assign_group':
                class_id = request.form['class_id']
                group_id = request.form['group_id']
                c.execute("SELECT locked FROM classes WHERE id = %s AND counselor_id = %s", (class_id, current_user.id))
                class_info = c.fetchone()
                if not class_info:
                    flash('Class not found or you are not authorized to edit it', 'error')
                elif class_info[0]:
                    flash('This class is locked and cannot be edited', 'error')
                else:
                    c.execute("SELECT attendee_id FROM attendee_groups WHERE group_id = %s", (group_id,))
                    attendee_ids = [row[0] for row in c.fetchall()]
                    if not attendee_ids:
                        logger.warning(f"No attendees found in group {group_id} for class {class_id}")
                        flash(f'No attendees found in group', 'error')
                    else:
                        assigned_count = 0
                        for attendee_id in attendee_ids:
                            try:
                                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                          (class_id, attendee_id))
                                assigned_count += c.rowcount
                            except psycopg2.Error as e:
                                logger.error(f"Error assigning attendee {attendee_id} to class {class_id}: {e}")
                        conn.commit()
                        if assigned_count > 0:
                            logger.info(f"Assigned {assigned_count} attendees from group {group_id} to class {class_id} by counselor {current_user.id}")
                            flash(f'Assigned {assigned_count} attendees from group to class', 'success')
                        else:
                            logger.info(f"No new attendees assigned from group {group_id} to class {class_id}")
                            flash(f'No new attendees assigned from group (already assigned or no attendees)', 'info')
            else:
                logger.warning(f"Unknown action received: {action}")
                flash('Invalid action', 'error')
        except psycopg2.IntegrityError:
            flash('Action failed due to duplicate or invalid data')
        except psycopg2.Error as e:
            logger.error(f"Database error in counselor_manage_classes: {e}")
            flash('Error processing your request. Please try again.', 'error')
            conn.rollback()
        except Exception as e:
            logger.error(f"Unexpected error in counselor_manage_classes: {e}")
            flash('An unexpected error occurred. Please try again.', 'error')
            conn.rollback()

    try:
        # Count total classes for pagination
        count_query = "SELECT COUNT(*) FROM classes c WHERE c.counselor_id = %s"
        count_params = [current_user.id]
        if group_filter != 'all':
            count_query += " AND c.group_name = %s"
            count_params.append(group_filter)
        if start_date:
            count_query += " AND c.date >= %s"
            count_params.append(start_date)
        if end_date:
            count_query += " AND c.date <= %s"
            count_params.append(end_date)
        c.execute(count_query, count_params)
        total_classes = c.fetchone()[0]
        total_pages = math.ceil(total_classes / per_page)
        page = max(1, min(page, total_pages))  # Ensure page is within bounds
        offset = (page - 1) * per_page

        c.execute("SELECT id, username, full_name FROM users WHERE role = 'counselor'")
        counselors = c.fetchall()
        classes_query = """
            SELECT c.id, c.group_name, c.class_name, c.date, c.group_hours, c.counselor_id, c.group_type, c.notes, c.location, c.recurring, c.frequency, u.full_name, c.locked
            FROM classes c
            LEFT JOIN users u ON c.counselor_id = u.id
            WHERE c.counselor_id = %s
        """
        params = [current_user.id]
        if group_filter != 'all':
            classes_query += " AND c.group_name = %s"
            params.append(group_filter)
        if start_date:
            classes_query += " AND c.date >= %s"
            params.append(start_date)
        if end_date:
            classes_query += " AND c.date <= %s"
            params.append(end_date)
        if sort_by == 'group':
            classes_query += " ORDER BY c.group_name ASC, c.date ASC"
        else:
            classes_query += " ORDER BY c.date ASC"
        classes_query += " LIMIT %s OFFSET %s"
        params.extend([per_page, offset])
        c.execute(classes_query, params)
        classes = c.fetchall()
        logger.info(f"Retrieved {len(classes)} classes for counselor_manage_classes: {[f'{c[2]} (recurring={c[9]})' for c in classes]}")
        c.execute("SELECT id, full_name, attendee_id FROM attendees ORDER BY full_name ASC")
        attendees = c.fetchall()
        c.execute("SELECT id, name FROM groups ORDER BY name")
        groups = c.fetchall()
        class_attendees = {}
        for class_ in classes:
            c.execute("""
                SELECT a.id, a.full_name, a.attendee_id, string_agg(g.name, ', ') AS groups
                FROM attendees a
                JOIN class_attendees ca ON a.id = ca.attendee_id
                LEFT JOIN attendee_groups ag ON a.id = ag.attendee_id
                LEFT JOIN groups g ON ag.group_id = g.id
                WHERE ca.class_id = %s
                GROUP BY a.id
                ORDER BY a.full_name ASC
            """, (class_[0],))
            class_attendees[class_[0]] = c.fetchall()
    except psycopg2.Error as e:
        logger.error(f"Database error in counselor_manage_classes data fetch: {e}")
        flash('Error loading classes. Please try again.', 'error')
        conn.close()
        return render_template('counselor_manage_classes.html', classes=[], attendees=[], class_attendees={}, groups=[], sort_by=sort_by, group_filter=group_filter, start_date=start_date, end_date=end_date, counselors=counselors, page=1, total_pages=1)
    finally:
        conn.close()

    return render_template('counselor_manage_classes.html', classes=classes, attendees=attendees, class_attendees=class_attendees, groups=groups, sort_by=sort_by, group_filter=group_filter, start_date=start_date, end_date=end_date, counselors=counselors, page=page, total_pages=total_pages)

@app.route('/manage_attendees', methods=['GET', 'POST'])
@login_required
def manage_attendees():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    group_filter = request.args.get('group_filter', 'all')
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'add':
                full_name = request.form['full_name']
                attendee_id = request.form['attendee_id']
                group_details = request.form['group_details']
                notes = request.form['notes']
                c.execute("INSERT INTO attendees (full_name, attendee_id, group_details, notes) VALUES (%s, %s, %s, %s) RETURNING id",
                          (full_name, attendee_id, group_details, notes))
                new_attendee_id = c.fetchone()[0]
                conn.commit()
                group_ids = request.form.getlist('group_ids')
                for group_id in group_ids:
                    c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (new_attendee_id, group_id))
                conn.commit()
                flash('Attendee added successfully')
            elif action == 'edit':
                attendee_id = request.form['attendee_id']
                full_name = request.form['full_name']
                new_attendee_id = request.form['new_attendee_id']
                group_details = request.form['group_details']
                notes = request.form['notes']
                c.execute("UPDATE attendees SET full_name = %s, attendee_id = %s, group_details = %s, notes = %s WHERE id = %s",
                          (full_name, new_attendee_id, group_details, notes, attendee_id))
                conn.commit()
                c.execute("DELETE FROM attendee_groups WHERE attendee_id = %s", (attendee_id,))
                group_ids = request.form.getlist('group_ids')
                for group_id in group_ids:
                    c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (attendee_id, group_id))
                conn.commit()
                flash('Attendee updated successfully')
            elif action == 'delete':
                attendee_id = request.form['attendee_id']
                c.execute("DELETE FROM class_attendees WHERE attendee_id = %s", (attendee_id,))
                c.execute("DELETE FROM attendance WHERE attendee_id = %s", (attendee_id,))
                c.execute("DELETE FROM attendee_groups WHERE attendee_id = %s", (attendee_id,))
                c.execute("DELETE FROM attendees WHERE id = %s", (attendee_id,))
                conn.commit()
                flash('Attendee deleted successfully')
            elif action == 'move_to_discharged':
                attendee_id = request.form['attendee_id']
                c.execute("SELECT id FROM groups WHERE name = 'Discharged'")
                discharged_id = c.fetchone()
                if not discharged_id:
                    flash('Discharged group not found', 'error')
                else:
                    c.execute("DELETE FROM attendee_groups WHERE attendee_id = %s", (attendee_id,))
                    c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (attendee_id, discharged_id[0]))
                    conn.commit()
                    flash('Attendee moved to Discharged group successfully')
            elif action == 'move_from_discharged':
                attendee_id = request.form['attendee_id']
                group_ids = request.form.getlist('group_ids')
                if not group_ids:
                    flash('Please select at least one group to move the attendee to', 'error')
                else:
                    c.execute("SELECT id FROM groups WHERE name = 'Discharged'")
                    discharged_id = c.fetchone()
                    if discharged_id:
                        c.execute("DELETE FROM attendee_groups WHERE attendee_id = %s AND group_id = %s",
                                  (attendee_id, discharged_id[0]))
                    for group_id in group_ids:
                        c.execute("INSERT INTO attendee_groups (attendee_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                  (attendee_id, group_id))
                    conn.commit()
                    flash('Attendee moved back to active groups successfully')
        except psycopg2.IntegrityError:
            flash('Attendee ID already exists')
        except psycopg2.Error as e:
            logger.error(f"Database error in manage_attendees: {e}")
            flash('Error processing your request. Please try again.', 'error')
        except Exception as e:
            logger.error(f"Unexpected error in manage_attendees: {e}")
            flash('An unexpected error occurred. Please try again.', 'error')
    try:
        attendees_query = """
            SELECT a.id, a.full_name, a.attendee_id, a.group_details, a.notes
            FROM attendees a
            WHERE 1=1
        """
        params = []
        if group_filter != 'all':
            attendees_query += " AND EXISTS (SELECT 1 FROM attendee_groups ag JOIN groups g ON ag.group_id = g.id WHERE ag.attendee_id = a.id AND g.name = %s)"
            params.append(group_filter)
        attendees_query += " ORDER BY a.full_name ASC"
        c.execute(attendees_query, params)
        attendees = c.fetchall()
        c.execute("SELECT id, name FROM groups ORDER BY name")
        groups = c.fetchall()
        attendee_groups = {}
        for attendee in attendees:
            c.execute("""
                SELECT g.id, g.name FROM groups g
                JOIN attendee_groups ag ON g.id = ag.group_id
                WHERE ag.attendee_id = %s
            """, (attendee[0],))
            attendee_groups[attendee[0]] = c.fetchall()
    except psycopg2.Error as e:
        logger.error(f"Database error in manage_attendees data fetch: {e}")
        flash('Error loading attendees. Please try again.', 'error')
        conn.close()
        return render_template('manage_attendees.html', attendees=[], groups=[], attendee_groups={}, group_filter=group_filter)
    finally:
        conn.close()

    return render_template('manage_attendees.html', attendees=attendees, groups=groups, attendee_groups=attendee_groups, group_filter=group_filter)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/counselor_dashboard')
@login_required
def counselor_dashboard():
    if current_user.role != 'counselor':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT full_name, credentials FROM users WHERE id = %s AND role = 'counselor'",
                  (current_user.id,))
        counselor = c.fetchone()
        if not counselor:
            logger.warning(f"Counselor not found for user_id: {current_user.id}")
            flash('Counselor not found')
            return redirect(url_for('login'))
        counselor_name, counselor_credentials = counselor
        today = datetime.today().strftime('%Y-%m-%d')
        past_page = int(request.args.get('past_page', 1))
        per_page = 10

        # Today classes
        logger.info(f"Fetching classes for counselor_id: {current_user.id}, date: {today}")
        c.execute("SELECT id, group_name, class_name, date, group_hours, location, locked FROM classes WHERE counselor_id = %s AND date = %s",
                  (current_user.id, today))
        today_classes = c.fetchall()

        # Upcoming classes
        c.execute("SELECT id, group_name, class_name, date, group_hours, location, locked FROM classes WHERE counselor_id = %s AND date BETWEEN %s AND %s",
                  (current_user.id, (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d'), (datetime.today() + timedelta(days=7)).strftime('%Y-%m-%d')))
        upcoming_classes = c.fetchall()

        # Past classes with pagination
        count_query = "SELECT COUNT(*) FROM classes WHERE counselor_id = %s AND date < %s"
        c.execute(count_query, (current_user.id, today))
        total_past_classes = c.fetchone()[0]
        total_past_pages = math.ceil(total_past_classes / per_page)
        past_page = max(1, min(past_page, total_past_pages))  # Ensure page is within bounds
        offset = (past_page - 1) * per_page
        c.execute("SELECT id, group_name, class_name, date, group_hours, location, locked FROM classes WHERE counselor_id = %s AND date < %s ORDER BY date DESC LIMIT %s OFFSET %s",
                  (current_user.id, today, per_page, offset))
        past_classes = c.fetchall()
        logger.info(f"Retrieved {len(past_classes)} past classes for counselor_id: {current_user.id}, page: {past_page}")

        for cls in today_classes + upcoming_classes + past_classes:
            if None in cls:
                logger.warning(f"Invalid class data: {cls}")
        conn.close()
        return render_template('counselor_dashboard.html', today_classes=today_classes, upcoming_classes=upcoming_classes, past_classes=past_classes,
                               today=today, counselor_name=counselor_name, counselor_credentials=counselor_credentials,
                               past_page=past_page, total_past_pages=total_past_pages)
    except psycopg2.Error as e:
        logger.error(f"Database error in counselor_dashboard: {e}")
        flash('Error loading dashboard. Please try again.', 'error')
        conn.close()
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Unexpected error in counselor_dashboard: {e}")
        flash('An unexpected error occurred. Please try again.', 'error')
        conn.close()
        return redirect(url_for('login'))

@app.route('/class_attendance/<int:class_id>', methods=['GET', 'POST'])
@login_required
def class_attendance(class_id):
    if current_user.role != 'counselor':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT id, class_name, date, group_hours, location, locked FROM classes WHERE id = %s AND counselor_id = %s",
                  (class_id, current_user.id))
        class_info = c.fetchone()
        if not class_info:
            logger.warning(f"Class {class_id} not found or not authorized for counselor_id {current_user.id}")
            flash('Class not found or you are not authorized')
            conn.close()
            return redirect(url_for('counselor_dashboard'))

        locked = class_info[5]
        if locked:
            flash('This class is locked and cannot be edited.')
        c.execute("SELECT a.id, a.full_name, a.attendee_id FROM attendees a JOIN class_attendees ca ON a.id = ca.attendee_id WHERE ca.class_id = %s ORDER BY a.full_name ASC",
                  (class_id,))
        attendees = c.fetchall()
        logger.info(f"Retrieved {len(attendees)} attendees for class_id: {class_id}")

        attendance_records = {a[0]: (None, None, 'Present', None, None) for a in attendees}
        c.execute("SELECT attendee_id, time_in, time_out, attendance_status, notes, location FROM attendance WHERE class_id = %s",
                  (class_id,))
        for record in c.fetchall():
            attendance_records[record[0]] = record[1:]

        if request.method == 'POST' and request.form.get('action') == 'submit_attendance' and not locked:
            for attendee in attendees:
                attendee_id = attendee[0]
                present = request.form.get(f'present_{attendee_id}', 'off') == 'on'
                attendance_status = 'Present' if present else request.form.get(f'status_{attendee_id}', 'Absent')
                time_in = request.form.get(f'time_in_{attendee_id}', None) if present else None
                time_out = request.form.get(f'time_out_{attendee_id}', None) if present else None
                notes = request.form.get(f'notes_{attendee_id}', None)
                location = request.form.get(f'location_{attendee_id}', None)
                c.execute("SELECT id FROM attendance WHERE class_id = %s AND attendee_id = %s", (class_id, attendee_id))
                existing_id = c.fetchone()
                if existing_id:
                    c.execute("UPDATE attendance SET time_in = %s, time_out = %s, attendance_status = %s, notes = %s, location = %s WHERE id = %s",
                              (time_in, time_out, attendance_status, notes, location, existing_id[0]))
                else:
                    c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, attendance_status, notes, location) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                              (class_id, attendee_id, time_in, time_out, attendance_status, notes, location))
            conn.commit()
            logger.info(f"Attendance submitted for class_id {class_id}")
            flash('Attendance submitted successfully')
        
        conn.close()
        if not attendees:
            flash('No attendees assigned to this class. Please assign attendees via Manage Classes.')
        return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records, locked=locked)
    
    except psycopg2.Error as e:
        logger.error(f"Database error in class_attendance: {e}")
        flash('Error loading attendance page. Please try again.', 'error')
        conn.close()
        return redirect(url_for('counselor_dashboard'))
    except Exception as e:
        logger.error(f"Unexpected error in class_attendance: {e}")
        flash('An unexpected error occurred. Please try again.', 'error')
        conn.close()
        return redirect(url_for('counselor_dashboard'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(debug=True, host='0.0.0.0', port=port)
