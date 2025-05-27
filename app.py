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

# Configure logging
logging.basicConfig(level=logging.INFO)
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
                    FOREIGN KEY (counselor_id) REFERENCES users(id)
                )''')
                logger.info("Classes table created")
                c.execute('''CREATE TABLE attendees (
                    id SERIAL PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    attendee_id TEXT UNIQUE NOT NULL,
                    "group" TEXT,
                    group_details TEXT,
                    notes TEXT
                )''')
                logger.info("Attendees table created")
                c.execute('''CREATE TABLE attendance (
                    id SERIAL PRIMARY KEY,
                    class_id INTEGER,
                    attendee_id INTEGER,
                    time_in TEXT,
                    time_out TEXT,
                    engagement TEXT,
                    comments TEXT,
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

                today = '2025-05-21'
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

                c.execute("INSERT INTO attendees (full_name, attendee_id, \"group\", group_details, notes) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                          ('John Smith', 'ATT001', 'Group A', 'Morning Session', 'Requires extra support'))
                attendee_id = c.fetchone()[0]
                c.execute("INSERT INTO attendees (full_name, attendee_id, \"group\", group_details, notes) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                          ('Jane Doe', 'ATT002', 'Group A', 'Morning Session', 'Good engagement'))
                attendee_id2 = c.fetchone()[0]
                logger.info("Sample attendees inserted")

                c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, engagement, comments) VALUES (%s, %s, %s, %s, %s, %s)",
                          (mindfulness_id, attendee_id, '10:00', '11:30', 'Yes', 'Actively participated'))
                c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, engagement, comments) VALUES (%s, %s, %s, %s, %s, %s)",
                          (mindfulness_id, attendee_id2, '10:00', '11:30', 'Yes', 'Good engagement'))
                c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, engagement, comments) VALUES (%s, %s, %s, %s, %s, %s)",
                          (yoga_id, attendee_id, '13:00', '14:00', 'Yes', 'Good participation'))
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

# Routes remain unchanged (copied from your original)
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
                    flash('Counselor dashboard is currently unavailable. Please try again later.')
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
    c.execute("SELECT id, group_name, class_name, date, group_hours, location FROM classes WHERE date = %s",
              (today,))
    today_classes = c.fetchall()
    conn.close()
    return render_template('admin_dashboard.html', today_classes=today_classes, today=today)

@app.route('/attendee_profile/<int:attendee_id>')
@login_required
def attendee_profile(attendee_id):
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, full_name, attendee_id, \"group\", group_details, notes FROM attendees WHERE id = %s",
              (attendee_id,))
    attendee = c.fetchone()
    if not attendee:
        flash('Attendee not found')
        return redirect(url_for('manage_attendees'))
    c.execute("SELECT c.id, c.group_name, c.class_name, c.date, c.group_hours, c.location FROM classes c JOIN class_attendees ca ON c.id = ca.class_id WHERE ca.attendee_id = %s",
              (attendee_id,))
    assigned_classes = c.fetchall()
    c.execute("SELECT c.class_name, a.time_in, a.time_out, a.engagement, a.comments FROM attendance a JOIN classes c ON a.class_id = c.id WHERE a.attendee_id = %s",
              (attendee_id,))
    attendance_records = c.fetchall()
    conn.close()
    return render_template('attendee_profile.html', attendee=attendee, assigned_classes=assigned_classes, attendance_records=attendance_records)

@app.route('/reports', methods=['GET', 'POST'])
@login_required
def reports():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
    classes = c.fetchall()
    c.execute("SELECT id, full_name FROM attendees ORDER BY full_name")
    attendees = c.fetchall()
    c.execute("SELECT id, full_name FROM users WHERE role = 'counselor' ORDER BY full_name")
    counselors = c.fetchall()
    
    today = datetime.today()
    default_start_date = today.strftime('%Y-%m-%d')
    default_end_date = (today + timedelta(days=6)).strftime('%Y-%m-%d')
    start_date = request.form.get('start_date', default_start_date)
    end_date = request.form.get('end_date', default_end_date)
    class_id = request.form.get('class_id', 'all')
    attendee_id = request.form.get('attendee_id', 'all')
    counselor_id = request.form.get('counselor_id', 'all')
    action = request.form.get('action', 'generate')
    
    # Log filter values
    logger.info(f"Reports filter values: start_date={start_date}, end_date={end_date}, class_id={class_id}, attendee_id={attendee_id}, counselor_id={counselor_id}, action={action}")
    
    # Build class query
    class_query = """
        SELECT c.id, c.class_name, c.group_name, c.date, c.group_hours, c.location,
               u.full_name AS counselor_name, u.credentials AS counselor_credentials
        FROM classes c
        JOIN users u ON c.counselor_id = u.id
        WHERE 1=1
    """
    params = []
    
    try:
        # Validate and apply date filters
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

        # Apply other filters
        if class_id and class_id != 'all':
            class_query += " AND c.id = %s"
            params.append(int(class_id))
        if counselor_id and counselor_id != 'all':
            class_query += " AND c.counselor_id = %s"
            params.append(int(counselor_id))
        
        # Fetch classes
        c.execute(class_query, params)
        class_records = c.fetchall()
        logger.info(f"Retrieved {len(class_records)} classes for report: {[r[1] for r in class_records]}")

        # Group attendance by class
        report_data = []
        for class_record in class_records:
            class_id = class_record[0]
            # Fetch attendees for this class
            attendee_query = """
                SELECT att.full_name, att.attendee_id, att."group",
                       a.engagement, a.time_in, a.time_out, a.comments
                FROM attendance a
                JOIN attendees att ON a.attendee_id = att.id
                WHERE a.class_id = %s
            """
            attendee_params = [class_id]
            if attendee_id and attendee_id != 'all':
                attendee_query += " AND a.attendee_id = %s"
                attendee_params.append(int(attendee_id))
            
            c.execute(attendee_query, attendee_params)
            attendee_records = c.fetchall()
            logger.info(f"Retrieved {len(attendee_records)} attendees for class_id {class_id}: {[r[0] for r in attendee_records]}")
            report_data.append({
                'class': class_record,
                'attendees': attendee_records
            })
        
        if not report_data and action == 'generate':
            flash('No classes found for the selected filters', 'info')
        
        if action == 'download_csv':
            try:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['Class Name', 'Group Name', 'Date', 'Group Hours', 'Location',
                                 'Counselor', 'Counselor Credentials', 'Attendee Name', 'Attendee ID',
                                 'Attendee Group', 'Engagement', 'Time In', 'Time Out', 'Comments'])
                for data in report_data:
                    class_record = data['class']
                    class_info = [
                        class_record[1], class_record[2], class_record[3], class_record[4],
                        class_record[5], class_record[6], class_record[7] or ''
                    ]
                    if not data['attendees']:
                        writer.writerow(class_info + ['No attendees', '', '', '', '', '', ''])
                    else:
                        for idx, attendee in enumerate(data['attendees']):
                            row = class_info if idx == 0 else ['', '', '', '', '', '', '']
                            row += [
                                attendee[0] or 'No attendees', attendee[1] or '',
                                attendee[2] or '', attendee[3] or '', attendee[4] or '',
                                attendee[5] or '', attendee[6] or ''
                            ]
                            writer.writerow(row)
                csv_content = output.getvalue()
                output.close()
                logger.info("CSV file generated successfully")
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
    
    conn.close()
    return render_template('reports.html', classes=classes, attendees=attendees, counselors=counselors, 
                           report_data=report_data, start_date=start_date, end_date=end_date, 
                           class_id=class_id, attendee_id=attendee_id, counselor_id=counselor_id)

@app.route('/manage_users', methods=['GET', 'POST'])
@login_required
def manage_users():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_counselor':
            username = request.form['username']
            password = request.form['password']
            full_name = request.form['full_name']
            credentials = request.form['credentials']
            email = request.form['email']
            try:
                hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s)",
                          (username, hashed_password, full_name, 'counselor', credentials, email))
                conn.commit()
                flash('Counselor added successfully')
            except psycopg2.IntegrityError:
                flash('Username already exists')
        elif action == 'add_admin':
            username = request.form['username']
            password = request.form['password']
            full_name = request.form['full_name']
            credentials = request.form['credentials']
            email = request.form['email']
            try:
                hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                c.execute("INSERT INTO users (username, password, full_name, role, credentials, email) VALUES (%s, %s, %s, %s, %s, %s)",
                          (username, hashed_password, full_name, 'admin', credentials, email))
                conn.commit()
                flash('Admin added successfully')
            except psycopg2.IntegrityError:
                flash('Username already exists')
        elif action == 'edit_counselor':
            counselor_id = request.form['counselor_id']
            username = request.form['username']
            full_name = request.form['full_name']
            credentials = request.form['credentials']
            email = request.form['email']
            password = request.form.get('password', '')
            try:
                if password:
                    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                    c.execute("UPDATE users SET username = %s, password = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'counselor'",
                              (username, hashed_password, full_name, credentials, email, counselor_id))
                else:
                    c.execute("UPDATE users SET username = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'counselor'",
                              (username, full_name, credentials, email, counselor_id))
                conn.commit()
                flash('Counselor updated successfully')
            except psycopg2.IntegrityError:
                flash('Username already exists')
        elif action == 'edit_admin':
            admin_id = request.form['admin_id']
            username = request.form['username']
            full_name = request.form['full_name']
            credentials = request.form['credentials']
            email = request.form['email']
            password = request.form.get('password', '')
            try:
                if password:
                    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                    c.execute("UPDATE users SET username = %s, password = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'admin'",
                              (username, hashed_password, full_name, credentials, email, admin_id))
                else:
                    c.execute("UPDATE users SET username = %s, full_name = %s, credentials = %s, email = %s WHERE id = %s AND role = 'admin'",
                              (username, full_name, credentials, email, admin_id))
                conn.commit()
                flash('Admin updated successfully')
            except psycopg2.IntegrityError:
                flash('Username already exists')
        elif action == 'delete_counselor':
            counselor_id = request.form['counselor_id']
            try:
                c.execute("DELETE FROM classes WHERE counselor_id = %s", (counselor_id,))
                c.execute("DELETE FROM users WHERE id = %s AND role = 'counselor'", (counselor_id,))
                conn.commit()
                flash('Counselor and associated classes deleted successfully')
            except psycopg2.errors.ForeignKeyViolation:
                flash('Cannot delete counselor because they are referenced in other records')
            except psycopg2.Error as e:
                logger.error(f"Error deleting counselor: {e}")
                flash('An error occurred while deleting the counselor')
        elif action == 'delete_admin':
            admin_id = request.form['admin_id']
            if int(admin_id) == current_user.id:
                flash('You cannot delete your own account')
            else:
                try:
                    c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
                    admin_count = c.fetchone()[0]
                    if admin_count <= 1:
                        flash('Cannot delete the last admin account')
                    else:
                        c.execute("DELETE FROM classes WHERE counselor_id = %s", (admin_id,))
                        c.execute("DELETE FROM users WHERE id = %s AND role = 'admin'", (admin_id,))
                        conn.commit()
                        flash('Admin and associated classes deleted successfully')
                except psycopg2.errors.ForeignKeyViolation:
                    flash('Cannot delete admin because they are referenced in other records')
                except psycopg2.Error as e:
                    logger.error(f"Error deleting admin: {e}")
                    flash('An error occurred while deleting the admin')
    c.execute("SELECT id, username, full_name, role, credentials, email FROM users WHERE role IN ('counselor', 'admin')")
    users = c.fetchall()
    conn.close()
    return render_template('manage_users.html', users=users, current_user_id=current_user.id)

@app.route('/manage_classes', methods=['GET', 'POST'])
@login_required
def manage_classes():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
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
            try:
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
            except psycopg2.IntegrityError:
                flash('Class creation failed due to duplicate or invalid data')
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
            try:
                c.execute("UPDATE classes SET group_name = %s, class_name = %s, date = %s, group_hours = %s, counselor_id = %s, group_type = %s, notes = %s, location = %s, recurring = %s, frequency = %s WHERE id = %s",
                          (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency, class_id))
                conn.commit()
                c.execute("DELETE FROM class_attendees WHERE class_id = %s", (class_id,))
                attendee_ids = request.form.getlist('attendee_ids')
                for attendee_id in attendee_ids:
                    c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (class_id, attendee_id))
                conn.commit()
                flash('Class updated successfully')
            except psycopg2.IntegrityError:
                flash('Class update failed due to duplicate or invalid data')
        elif action == 'delete':
            class_id = request.form['class_id']
            try:
                c.execute("DELETE FROM class_attendees WHERE class_id = %s", (class_id,))
                c.execute("DELETE FROM attendance WHERE class_id = %s", (class_id,))
                c.execute("DELETE FROM classes WHERE id = %s", (class_id,))
                conn.commit()
                flash('Class deleted successfully')
            except psycopg2.Error as e:
                logger.error(f"Error deleting class: {e}")
                flash('An error occurred while deleting the class')
        elif action == 'assign_attendee':
            class_id = request.form['class_id']
            attendee_id = request.form['attendee_id']
            try:
                c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)",
                          (class_id, attendee_id))
                conn.commit()
                flash('Attendee assigned successfully')
            except psycopg2.IntegrityError:
                flash('Attendee already assigned to this class')
        elif action == 'unassign_attendee':
            class_id = request.form['class_id']
            attendee_id = request.form['attendee_id']
            c.execute("DELETE FROM class_attendees WHERE class_id = %s AND attendee_id = %s", (class_id, attendee_id))
            conn.commit()
            flash('Attendee unassigned successfully')
    c.execute("SELECT id, username, full_name FROM users WHERE role = 'counselor'")
    counselors = c.fetchall()
    c.execute("""
        SELECT c.id, c.group_name, c.class_name, c.date, c.group_hours, c.counselor_id, c.group_type, c.notes, c.location, c.recurring, c.frequency, u.full_name
        FROM classes c
        LEFT JOIN users u ON c.counselor_id = u.id
    """)
    classes = c.fetchall()
    c.execute("SELECT id, full_name, attendee_id FROM attendees")
    attendees = c.fetchall()
    class_attendees = {}
    for class_ in classes:
        c.execute("SELECT a.id, a.full_name, a.attendee_id FROM attendees a JOIN class_attendees ca ON a.id = ca.attendee_id WHERE ca.class_id = %s", (class_[0],))
        class_attendees[class_[0]] = c.fetchall()
    conn.close()
    return render_template('manage_classes.html', counselors=counselors, classes=classes, attendees=attendees, class_attendees=class_attendees)

@app.route('/manage_attendees', methods=['GET', 'POST'])
@login_required
def manage_attendees():
    if current_user.role != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            full_name = request.form['full_name']
            attendee_id = request.form['attendee_id']
            group = request.form['group']
            group_details = request.form['group_details']
            notes = request.form['notes']
            try:
                c.execute("INSERT INTO attendees (full_name, attendee_id, \"group\", group_details, notes) VALUES (%s, %s, %s, %s, %s)",
                          (full_name, attendee_id, group, group_details, notes))
                conn.commit()
                flash('Attendee added successfully')
            except psycopg2.IntegrityError:
                flash('Attendee ID already exists')
        elif action == 'edit':
            attendee_id = request.form['attendee_id']
            full_name = request.form['full_name']
            new_attendee_id = request.form['new_attendee_id']
            group = request.form['group']
            group_details = request.form['group_details']
            notes = request.form['notes']
            try:
                c.execute("UPDATE attendees SET full_name = %s, attendee_id = %s, \"group\" = %s, group_details = %s, notes = %s WHERE id = %s",
                          (full_name, new_attendee_id, group, group_details, notes, attendee_id))
                conn.commit()
                flash('Attendee updated successfully')
            except psycopg2.IntegrityError:
                flash('Attendee ID already exists')
        elif action == 'delete':
            attendee_id = request.form['attendee_id']
            try:
                c.execute("DELETE FROM class_attendees WHERE attendee_id = %s", (attendee_id,))
                c.execute("DELETE FROM attendance WHERE attendee_id = %s", (attendee_id,))
                c.execute("DELETE FROM attendees WHERE id = %s", (attendee_id,))
                conn.commit()
                flash('Attendee deleted successfully')
            except psycopg2.errors.ForeignKeyViolation:
                flash('Cannot delete attendee because they are referenced in other records')
            except psycopg2.Error as e:
                logger.error(f"Error deleting attendee: {e}")
                flash('An error occurred while deleting the attendee')
    c.execute("SELECT id, full_name, attendee_id, \"group\", group_details, notes FROM attendees")
    attendees = c.fetchall()
    conn.close()
    return render_template('manage_attendees.html', attendees=attendees)

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
        today = datetime.today()
        today_str = today.strftime('%Y-%m-%d')
        week_later = (today + timedelta(days=7)).strftime('%Y-%m-%d')
        logger.info(f"Fetching classes for counselor_id: {current_user.id}, date: {today_str}")
        c.execute("SELECT id, group_name, class_name, date, group_hours, location FROM classes WHERE counselor_id = %s AND date = %s",
                  (current_user.id, today_str))
        today_classes = c.fetchall()
        logger.info(f"Retrieved {len(today_classes)} classes for today: {[cls[2] for cls in today_classes]}")
        c.execute("SELECT id, group_name, class_name, date, group_hours, location FROM classes WHERE counselor_id = %s AND date BETWEEN %s AND %s",
                  (current_user.id, (today + timedelta(days=1)).strftime('%Y-%m-%d'), week_later))
        upcoming_classes = c.fetchall()
        # Validate class data
        for cls in today_classes + upcoming_classes:
            if None in cls:
                logger.warning(f"Invalid class data: {cls}")
        conn.close()
        return render_template('counselor_dashboard.html', today_classes=today_classes, upcoming_classes=upcoming_classes,
                               today=today_str, counselor_name=counselor_name, counselor_credentials=counselor_credentials)
    except psycopg2.Error as e:
        logger.error(f"Database error in counselor_dashboard for user_id: {current_user.id}: {e}")
        flash('Error loading dashboard. Please try again later.')
        conn.close()
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Unexpected error in counselor_dashboard for user_id: {current_user.id}: {e}")
        flash('An unexpected error occurred. Please try again later.')
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
        # Fetch class details
        c.execute("SELECT id, class_name, date, group_hours, location FROM classes WHERE id = %s AND counselor_id = %s",
                  (class_id, current_user.id))
        class_info = c.fetchone()
        if not class_info:
            logger.warning(f"Class {class_id} not found or not authorized for counselor_id: {current_user.id}")
            flash('Class not found or you are not authorized')
            conn.close()
            return redirect(url_for('counselor_dashboard'))

        # Fetch attendees assigned to the class
        c.execute("SELECT a.id, a.full_name, a.attendee_id FROM attendees a JOIN class_attendees ca ON a.id = ca.attendee_id WHERE ca.class_id = %s",
                  (class_id,))
        attendees = c.fetchall()
        logger.info(f"Retrieved {len(attendees)} attendees for class_id: {class_id}")

        # Fetch existing attendance records
        c.execute("SELECT att.id, a.full_name, att.time_in, att.time_out, att.engagement, att.comments FROM attendees a JOIN attendance att ON a.id = att.attendee_id WHERE att.class_id = %s",
                  (class_id,))
        attendance_records = c.fetchall()

        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'record_attendance':
                attendee_id = request.form.get('attendee_id')
                time_in = request.form.get('time_in')
                time_out = request.form.get('time_out', None)  # Optional
                engagement = request.form.get('engagement', '')
                comments = request.form.get('comments', '')
                
                # Validate inputs
                if not attendee_id or not time_in:
                    logger.warning(f"Missing required fields for attendance: attendee_id={attendee_id}, time_in={time_in}")
                    flash('Please provide attendee and time in')
                    return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records)
                
                # Verify attendee_id is valid for this class
                c.execute("SELECT 1 FROM class_attendees WHERE class_id = %s AND attendee_id = %s", (class_id, attendee_id))
                if not c.fetchone():
                    logger.warning(f"Invalid attendee_id {attendee_id} for class_id {class_id}")
                    flash('Selected attendee is not assigned to this class')
                    return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records)
                
                # Prevent duplicate attendance records
                c.execute("SELECT 1 FROM attendance WHERE class_id = %s AND attendee_id = %s", (class_id, attendee_id))
                if c.fetchone():
                    logger.warning(f"Duplicate attendance record attempted for class_id {class_id}, attendee_id {attendee_id}")
                    flash('Attendance already recorded for this attendee in this class')
                    return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records)

                try:
                    c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, engagement, comments) VALUES (%s, %s, %s, %s, %s, %s)",
                              (class_id, attendee_id, time_in, time_out, engagement, comments))
                    conn.commit()
                    logger.info(f"Attendance recorded for class_id {class_id}, attendee_id {attendee_id}")
                    flash('Attendance recorded successfully')
                except psycopg2.IntegrityError as e:
                    logger.error(f"IntegrityError recording attendance for class_id {class_id}, attendee_id {attendee_id}: {e}")
                    flash('Error recording attendance: Invalid data or duplicate entry')
                except psycopg2.Error as e:
                    logger.error(f"Database error recording attendance for class_id {class_id}, attendee_id {attendee_id}: {e}")
                    flash('An error occurred while recording attendance')

            elif action == 'update_timeout':
                attendance_id = request.form.get('attendance_id')
                time_out = request.form.get('time_out')
                
                # Validate inputs
                if not attendance_id or not time_out:
                    logger.warning(f"Missing required fields for update_timeout: attendance_id={attendance_id}, time_out={time_out}")
                    flash('Please provide attendance ID and time out')
                    return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records)
                
                # Verify attendance record exists and belongs to this class
                c.execute("SELECT 1 FROM attendance WHERE id = %s AND class_id = %s", (attendance_id, class_id))
                if not c.fetchone():
                    logger.warning(f"Invalid attendance_id {attendance_id} for class_id {class_id}")
                    flash('Invalid attendance record')
                    return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records)

                try:
                    c.execute("UPDATE attendance SET time_out = %s WHERE id = %s AND class_id = %s",
                              (time_out, attendance_id, class_id))
                    conn.commit()
                    logger.info(f"Time out updated for attendance_id {attendance_id}, class_id {class_id}")
                    flash('Time out updated successfully')
                except psycopg2.Error as e:
                    logger.error(f"Database error updating time out for attendance_id {attendance_id}: {e}")
                    flash('An error occurred while updating time out')

        conn.close()
        if not attendees:
            flash('No attendees assigned to this class. Please assign attendees first.')
        return render_template('class_attendance.html', class_info=class_info, attendees=attendees, attendance_records=attendance_records)
    
    except psycopg2.Error as e:
        logger.error(f"Database error in class_attendance for class_id {class_id}: {e}")
        flash('Error loading attendance page. Please try again later.')
        conn.close()
        return redirect(url_for('counselor_dashboard'))
    except Exception as e:
        logger.error(f"Unexpected error in class_attendance for class_id {class_id}: {e}")
        flash('An unexpected error occurred. Please try again later.')
        conn.close()
        return redirect(url_for('counselor_dashboard'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))  # Match Render's detected port
    app.run(debug=True, host='0.0.0.0', port=port)
