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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key')

bcrypt = Bcrypt(app)
app.jinja_env.add_extension('jinja2.ext.do')
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

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
            logger.info("Sample attendee inserted")

            c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, engagement, comments) VALUES (%s, %s, %s, %s, %s, %s)",
                      (mindfulness_id, attendee_id, '10:00', '11:30', 'Yes', 'Actively participated'))
            c.execute("INSERT INTO attendance (class_id, attendee_id, time_in, time_out, engagement, comments) VALUES (%s, %s, %s, %s, %s, %s)",
                      (yoga_id, attendee_id, '13:00', '14:00', 'Yes', 'Good participation'))
            logger.info("Sample attendance inserted")

            c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s)", (mindfulness_id, attendee_id))
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

# Run init_db on app startup
init_db()
scheduler.start()

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
        start = datetime.strptime(start_date, '%Y-%m-%d')
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
                c.execute("INSERT INTO classes (group_name, class_name, date, group_hours, counselor_id, group_type, notes, location, recurring, frequency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                          (group_name, class_name, new_date, group_hours, counselor_id, group_type, notes, location, 0, None))
                new_class_id = c.fetchone()[0]
                c.execute("SELECT attendee_id FROM class_attendees WHERE class_id = %s", (class_id,))
                attendees = c.fetchall()
                for attendee in attendees:
                    c.execute("INSERT INTO class_attendees (class_id, attendee_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                              (new_class_id, attendee[0]))
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
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, username, password, role FROM users WHERE username = %s", (username,))
        user = c.fetchone()
        conn.close()
        if user and bcrypt.check_password_hash(user[2], password):
            user_obj = User(user[0], user[1], user[3])
            login_user(user_obj)
            if user[3] == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user[3] == 'counselor':
                return redirect(url_for('counselor_dashboard'))
        flash('Invalid username or password')
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
    class_id = request.form.get('class_id', '')
    attendee_id = request.form.get('attendee_id', '')
    counselor_id = request.form.get('counselor_id', '')
    action = request.form.get('action', '')
    
    # Build dynamic query
    query = """
        SELECT c.class_name, c.group_name, c.date, c.group_hours, c.location,
               u.full_name AS counselor_name, u.credentials AS counselor_credentials,
               att.full_name AS attendee_name, att.attendee_id, att."group",
               a.engagement, a.time_in, a.time_out, a.comments
        FROM attendance a
        JOIN classes c ON a.class_id = c.id
        JOIN attendees att ON a.attendee_id = att.id
        JOIN users u ON c.counselor_id = u.id
        WHERE 1=1
    """
    params = []
    
    if start_date and end_date:
        query += " AND c.date BETWEEN %s AND %s"
        params.extend([start_date, end_date])
    if class_id and class_id != 'all':
        query += " AND a.class_id = %s"
        params.append(class_id)
    if attendee_id and attendee_id != 'all':
        query += " AND a.attendee_id = %s"
        params.append(attendee_id)
    if counselor_id and counselor_id != 'all':
        query += " AND c.counselor_id = %s"
        params.append(counselor_id)
    
    try:
        c.execute(query, params)
        report_records = c.fetchall()
        logger.info(f"Report query executed with {len(report_records)} records returned")
        if not report_records and action == 'generate':
            flash('No attendance records found for the selected filters', 'info')
    except psycopg2.Error as e:
        logger.error(f"Error executing report query: {e}")
        flash('An error occurred while generating the report', 'error')
        report_records = []
    
    if action == 'download_csv':
        try:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Class Name', 'Class Group', 'Date', 'Group Hour', 'Location',
                             'Counselor', 'Counselor Credentials', 'Attendee Name', 'ID', 'Group',
                             'Engagement', 'Time In', 'Time Out', 'Comments'])
            if not report_records:
                logger.warning("No records to include in CSV download")
                flash('No attendance records found to download as CSV', 'info')
                conn.close()
                return redirect(url_for('reports'))
            for record in report_records:
                writer.writerow([
                    record[0], record[1], record[2], record[3], record[4],
                    record[5], record[6] or '', record[7], record[8], record[9] or '',
                    record[10], record[11], record[12] or 'Not set', record[13] or ''
                ])
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
    
    conn.close()
    return render_template('reports.html', classes=classes, attendees=attendees, counselors=counselors, 
                           report_records=report_records, start_date=start_date, end_date=end_date, 
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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
