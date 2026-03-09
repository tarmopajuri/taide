"""
Fillio - Ulejaava ehitusmaterjali jagamise platvorm
fillio.eu
"""
import os
import uuid
import json
import secrets
import smtplib
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, render_template, g, session, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', 'fillio-dev-secret-key-change-me')

# --- Upload config ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Email config ---
SMTP_SERVER = os.environ.get('SMTP_SERVER', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
SMTP_FROM = os.environ.get('SMTP_FROM', 'noreply@fillio.eu')

# --- Google OAuth config ---
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')

# --- Database config ---
DATABASE_URL = os.environ.get('DATABASE_URL', '')

def is_postgres():
    return bool(DATABASE_URL)

def get_db():
    if 'db' not in g:
        if is_postgres():
            import psycopg2
            import psycopg2.extras
            db_url = DATABASE_URL
            if db_url.startswith('postgres://'):
                db_url = db_url.replace('postgres://', 'postgresql://', 1)
            g.db = psycopg2.connect(db_url)
            g.db.autocommit = False
        else:
            import sqlite3
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'taide.db')
            g.db = sqlite3.connect(db_path)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

def db_execute(query, params=None):
    """Execute query with proper placeholder for PostgreSQL (%s) or SQLite (?)"""
    db = get_db()
    if is_postgres():
        query = query.replace('?', '%s')
        import psycopg2.extras
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = db.cursor()
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    return cur

def db_commit():
    db = get_db()
    db.commit()

def db_fetchall(cursor):
    if is_postgres():
        return cursor.fetchall()
    else:
        return [dict(row) for row in cursor.fetchall()]

def db_fetchone(cursor):
    if is_postgres():
        return cursor.fetchone()
    else:
        row = cursor.fetchone()
        return dict(row) if row else None

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- Auth helper ---
def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    cur = db_execute("SELECT id, email, username, role, created_at FROM users WHERE id = ?", (user_id,))
    return db_fetchone(cur)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def send_email(to_email, subject, html_body):
    """Send email via SMTP. Silent fail if not configured."""
    if not SMTP_SERVER or not SMTP_USER:
        print(f"[EMAIL] SMTP not configured. Would send to {to_email}: {subject}")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        print(f"[EMAIL] Sent to {to_email}: {subject}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False

# --- Initialize database ---
def init_db():
    if is_postgres():
        import psycopg2
        db_url = DATABASE_URL
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        db = psycopg2.connect(db_url)
        cur = db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                reset_token TEXT,
                reset_token_expires TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id SERIAL PRIMARY KEY,
                material_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                quantity TEXT,
                unit TEXT DEFAULT 'tonni',
                price_type TEXT DEFAULT 'tasuta',
                price REAL DEFAULT 0,
                contact_name TEXT NOT NULL,
                contact_phone TEXT,
                contact_email TEXT,
                address TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                user_id INTEGER
            )
        """)
        cur.execute("ALTER TABLE listings ADD COLUMN IF NOT EXISTS user_id INTEGER")
        # Add reset token columns if missing
        try:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP")
        except Exception:
            pass
        db.commit()
        cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        admin_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM listings")
        count = cur.fetchone()[0]
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'taide.db')
        db = sqlite3.connect(db_path)
        cur = db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                reset_token TEXT,
                reset_token_expires TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                quantity TEXT,
                unit TEXT DEFAULT 'tonni',
                price_type TEXT DEFAULT 'tasuta',
                price REAL DEFAULT 0,
                contact_name TEXT NOT NULL,
                contact_phone TEXT,
                contact_email TEXT,
                address TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                user_id INTEGER
            )
        """)
        try:
            cur.execute("ALTER TABLE listings ADD COLUMN user_id INTEGER")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN reset_token TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP")
        except Exception:
            pass
        db.commit()
        cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        admin_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM listings")
        count = cur.fetchone()[0]

    # Create default admin
    if admin_count == 0:
        ph = '%s' if is_postgres() else '?'
        cur.execute(
            f"INSERT INTO users (email, username, password_hash, role) VALUES ({ph}, {ph}, {ph}, {ph})",
            ('admin@fillio.eu', 'Admin', generate_password_hash('admin123'), 'admin')
        )
        db.commit()

    # Sample data
    if count == 0:
        sample_data = [
            ('kivi', 'Suuremad maakivid', 'Majaehitusest ule jaanud maakivid, sobivad haljastusse voi muuri ehituseks.', '~5', 'tonni', 'tasuta', 0, 'Mart Tamm', '+372 5551 2345', 'mart@email.ee', 'Parnu mnt 45, Tallinn', 59.4310, 24.7421, ''),
            ('liiv', 'Ehitusliiv', 'Kaevetoodest ule jaanud puhas ehitusliiv.', '~8', 'tonni', 'tasuta', 0, 'Kersti Kask', '+372 5556 7890', '', 'Tartu mnt 120, Tallinn', 59.4185, 24.7858, ''),
            ('muld', 'Must muld / kasvumuld', 'Aiast valja kaevatud hea kvaliteediga kasvumuld.', '~3', 'tonni', 'tasuta', 0, 'Jaan Lepp', '', 'jaan@email.ee', 'Manniku tee 12, Tallinn', 59.3875, 24.7013, ''),
            ('purustatud_betoon', 'Purustatud betoon 0-32', 'Lammutustoodest saadud purustatud betoon, sobib alustaitekss.', '~15', 'tonni', 'tasuta', 0, 'OU Ehitusabi', '+372 5559 0123', 'info@ehitusabi.ee', 'Peterburi tee 81, Tallinn', 59.4245, 24.8105, ''),
            ('kruus', 'Kruus / soelutud kruus', 'Ule jaanud kruus tee-ehitusest.', '~10', 'tonni', 'kokkuleppel', 0, 'Priit Mets', '+372 5553 4567', '', 'Paldiski mnt 229, Tallinn', 59.4125, 24.6215, ''),
            ('muld', 'Haljastusmuld tasuta', 'Kaevetoodest ule jaanud muld, tuleb ise kohale tulla.', '~20', 'tonni', 'tasuta', 0, 'Siim Sepp', '+372 5552 8901', 'siim@email.ee', 'Vana-Tartu mnt 5, Tartu', 58.3742, 26.7290, ''),
            ('kivi', 'Paekivi tukid', 'Paekivi tukid, sobivad aiaehitusse.', '~2', 'tonni', 'tasuta', 0, 'Maria Kivi', '+372 5554 3210', '', 'Riia mnt 50, Tartu', 58.3650, 26.7180, ''),
            ('liiv', 'Taiteliiv', 'Ehitusplatsilt ule jaanud taiteliiv, hea hinnaga.', '~12', 'tonni', 'kokkuleppel', 0, 'Andres Puu', '+372 5557 6543', 'andres@email.ee', 'Laane 2, Parnu', 58.3856, 24.5044, ''),
        ]
        placeholder = '%s' if is_postgres() else '?'
        placeholders = ', '.join([placeholder] * 14)
        for row in sample_data:
            cur.execute(f"""
                INSERT INTO listings (material_type, title, description, quantity, unit, price_type, price,
                    contact_name, contact_phone, contact_email, address, latitude, longitude, image_url)
                VALUES ({placeholders})
            """, row)
        db.commit()
    db.close()

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html', google_client_id=GOOGLE_CLIENT_ID)

# --- Static docs ---
@app.route('/docs/<path:filename>')
def serve_doc(filename):
    docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'docs')
    return send_from_directory(docs_dir, filename)

# --- Auth routes ---
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not email or not username or not password:
        return jsonify({'error': 'Koik valjad on kohustuslikud'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Parool peab olema vahemalt 6 marki'}), 400

    cur = db_execute("SELECT id FROM users WHERE email = ?", (email,))
    if db_fetchone(cur):
        return jsonify({'error': 'See e-posti aadress on juba kasutusel'}), 400

    db_execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
               (email, username, generate_password_hash(password)))
    db_commit()

    cur = db_execute("SELECT id, email, username, role FROM users WHERE email = ?", (email,))
    user = db_fetchone(cur)
    session['user_id'] = user['id']

    # Send welcome email
    send_email(email, 'Tere tulemast Fillio platvormile! Sinu konto on loodud', f"""
    <div style="font-family: 'Open Sans', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f8f9fa;">
        <div style="background: linear-gradient(135deg, #3A7025, #4A9030); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
            <h1 style="margin: 0; font-size: 32px; font-family: 'Montserrat', Arial, sans-serif; letter-spacing: -0.5px;">Fillio</h1>
            <p style="margin: 8px 0 0; opacity: 0.85; font-size: 14px;">Jaga ülejäävat ehitus- ja täitematerjali</p>
        </div>
        <div style="background: white; padding: 30px; border: 1px solid #e5e7eb;">
            <h2 style="color: #3A7025; margin: 0 0 16px; font-family: 'Montserrat', Arial, sans-serif;">Tere, {username}!</h2>
            <p style="color: #333; line-height: 1.6; margin: 0 0 16px;">Sinu konto Fillio platvormil on edukalt loodud. Oled nüüd osa kogukonnast, mis aitab vähendada ehitusmaterjali raiskamist!</p>
            <p style="color: #333; line-height: 1.6; margin: 0 0 8px; font-weight: 600;">Mida saad Fillio platvormil teha:</p>
            <ul style="color: #555; line-height: 1.8; margin: 0 0 24px; padding-left: 20px;">
                <li>Lisa kuulutusi ülejäävate ehitusmaterjalide kohta</li>
                <li>Otsi tasuta või soodsaid ehitusmaterjale</li>
                <li>Halda oma kuulutusi ja kontaktandmeid</li>
                <li>Sirvi interaktiivsel kaardil materjale oma piirkonnas</li>
            </ul>
            <p style="text-align: center; margin: 24px 0;">
                <a href="https://fillio.eu" style="background: #5BB139; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 700; font-size: 16px; display: inline-block;">
                    Logi sisse
                </a>
            </p>
        </div>
        <div style="background: #fef3c7; padding: 16px 24px; border-left: 4px solid #f59e0b; border-right: 1px solid #e5e7eb;">
            <p style="color: #92400e; font-size: 13px; margin: 0; line-height: 1.5;">
                &#9888;&#65039; <strong>Turvalisuse märkus:</strong> Kui sa ei loonud Fillio kontot, palun võta meiega kohe ühendust aadressil
                <a href="mailto:tarmo@tardek.com" style="color: #92400e;">tarmo@tardek.com</a>.
            </p>
        </div>
        <div style="background: #f3f4f6; padding: 20px 24px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; text-align: center;">
            <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px;">Küsimuste korral: <a href="mailto:tarmo@tardek.com" style="color: #5BB139;">tarmo@tardek.com</a></p>
            <p style="color: #9ca3af; font-size: 11px; margin: 0;">&copy; 2026 Fillio &middot; Tardek &middot; <a href="https://fillio.eu" style="color: #9ca3af;">fillio.eu</a></p>
        </div>
    </div>
    """)

    return jsonify({'success': True, 'user': user}), 201

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'error': 'E-post ja parool on kohustuslikud'}), 400

    cur = db_execute("SELECT id, email, username, password_hash, role FROM users WHERE email = ?", (email,))
    user = db_fetchone(cur)

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Vale e-post voi parool'}), 401

    session['user_id'] = user['id']
    return jsonify({'success': True, 'user': {
        'id': user['id'], 'email': user['email'],
        'username': user['username'], 'role': user['role']
    }})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
def me():
    user = get_current_user()
    if user:
        return jsonify({'user': user})
    return jsonify({'user': None})

# --- Password reset ---
@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'error': 'E-posti aadress on kohustuslik'}), 400

    cur = db_execute("SELECT id, username FROM users WHERE email = ?", (email,))
    user = db_fetchone(cur)

    # Always return success to prevent email enumeration
    if not user:
        return jsonify({'success': True, 'message': 'Kui see e-post on registreeritud, saadame parooli taastamise lingi.'})

    # Generate reset token
    token = secrets.token_urlsafe(32)

    if is_postgres():
        db_execute("UPDATE users SET reset_token = ?, reset_token_expires = NOW() + INTERVAL '1 hour' WHERE email = ?", (token, email))
    else:
        db_execute("UPDATE users SET reset_token = ?, reset_token_expires = datetime('now', '+1 hour') WHERE email = ?", (token, email))
    db_commit()

    # Send reset email
    reset_url = f"https://fillio.eu?reset_token={token}"
    send_email(email, 'Fillio - parooli taastamine', f"""
    <div style="font-family: 'Open Sans', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f8f9fa;">
        <div style="background: linear-gradient(135deg, #3A7025, #4A9030); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
            <h1 style="margin: 0; font-size: 32px; font-family: 'Montserrat', Arial, sans-serif;">Fillio</h1>
            <p style="margin: 8px 0 0; opacity: 0.85; font-size: 14px;">Jaga ülejäävat ehitus- ja täitematerjali</p>
        </div>
        <div style="background: white; padding: 30px; border: 1px solid #e5e7eb;">
            <h2 style="color: #3A7025; margin: 0 0 16px; font-family: 'Montserrat', Arial, sans-serif;">Parooli taastamine</h2>
            <p style="color: #333; line-height: 1.6; margin: 0 0 8px;">Tere, {user['username']}!</p>
            <p style="color: #333; line-height: 1.6; margin: 0 0 24px;">Keegi (loodetavasti sina) soovis taastada sinu Fillio konto parooli. Kliki allolevale nupule uue parooli seadmiseks:</p>
            <p style="text-align: center; margin: 24px 0;">
                <a href="{reset_url}" style="background: #5BB139; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 700; font-size: 16px; display: inline-block;">
                    Taasta parool
                </a>
            </p>
            <p style="color: #666; font-size: 13px; margin: 16px 0 0; text-align: center;">See link kehtib 1 tunni.</p>
        </div>
        <div style="background: #fef3c7; padding: 16px 24px; border-left: 4px solid #f59e0b; border-right: 1px solid #e5e7eb;">
            <p style="color: #92400e; font-size: 13px; margin: 0; line-height: 1.5;">
                &#9888;&#65039; Kui sa ei soovinud parooli taastada, ignoreeri seda kirja. Sinu konto on turvaline.
            </p>
        </div>
        <div style="background: #f3f4f6; padding: 20px 24px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; text-align: center;">
            <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px;">Küsimuste korral: <a href="mailto:tarmo@tardek.com" style="color: #5BB139;">tarmo@tardek.com</a></p>
            <p style="color: #9ca3af; font-size: 11px; margin: 0;">&copy; 2026 Fillio &middot; Tardek &middot; <a href="https://fillio.eu" style="color: #9ca3af;">fillio.eu</a></p>
        </div>
    </div>
    """)

    return jsonify({'success': True, 'message': 'Kui see e-post on registreeritud, saadame parooli taastamise lingi.'})

@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    token = (data.get('token') or '').strip()
    new_password = data.get('password') or ''

    if not token or not new_password:
        return jsonify({'error': 'Token ja uus parool on kohustuslikud'}), 400

    if len(new_password) < 6:
        return jsonify({'error': 'Parool peab olema vahemalt 6 marki'}), 400

    if is_postgres():
        cur = db_execute("SELECT id, email FROM users WHERE reset_token = ? AND reset_token_expires > NOW()", (token,))
    else:
        cur = db_execute("SELECT id, email FROM users WHERE reset_token = ? AND reset_token_expires > datetime('now')", (token,))

    user = db_fetchone(cur)

    if not user:
        return jsonify({'error': 'Link on aegunud voi vigane. Proovi uuesti.'}), 400

    db_execute("UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL WHERE id = ?",
               (generate_password_hash(new_password), user['id']))
    db_commit()

    return jsonify({'success': True, 'message': 'Parool on edukalt muudetud!'})

# --- Google OAuth ---
@app.route('/api/auth/google', methods=['POST'])
def google_auth():
    data = request.get_json()
    credential = data.get('credential', '')

    if not credential:
        return jsonify({'error': 'Google credential puudub'}), 400

    if not GOOGLE_CLIENT_ID:
        return jsonify({'error': 'Google login pole seadistatud'}), 500

    # Verify token with Google
    try:
        verify_url = f'https://oauth2.googleapis.com/tokeninfo?id_token={credential}'
        req = urllib.request.Request(verify_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_info = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[GOOGLE AUTH] Token verification failed: {e}")
        return jsonify({'error': 'Google tokeni verifitseerimine ebaonnestus'}), 401

    # Check audience matches our client ID
    if token_info.get('aud') != GOOGLE_CLIENT_ID:
        return jsonify({'error': 'Vale Google client ID'}), 401

    email = token_info.get('email', '').lower()
    name = token_info.get('name', '') or token_info.get('given_name', '') or email.split('@')[0]

    if not email:
        return jsonify({'error': 'E-posti aadress puudub Google kontost'}), 400

    # Check if user exists
    cur = db_execute("SELECT id, email, username, role FROM users WHERE email = ?", (email,))
    user = db_fetchone(cur)

    if user:
        # Login existing user
        session['user_id'] = user['id']
        return jsonify({'success': True, 'user': user, 'is_new': False})
    else:
        # Register new user with random password (they use Google to login)
        random_pass = secrets.token_urlsafe(32)
        db_execute("INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                   (email, name, generate_password_hash(random_pass)))
        db_commit()

        cur = db_execute("SELECT id, email, username, role FROM users WHERE email = ?", (email,))
        user = db_fetchone(cur)
        session['user_id'] = user['id']

        # Send welcome email
        send_email(email, 'Tere tulemast Fillio platvormile! Sinu konto on loodud', f"""
        <div style="font-family: 'Open Sans', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f8f9fa;">
            <div style="background: linear-gradient(135deg, #3A7025, #4A9030); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                <h1 style="margin: 0; font-size: 32px; font-family: 'Montserrat', Arial, sans-serif;">Fillio</h1>
                <p style="margin: 8px 0 0; opacity: 0.85; font-size: 14px;">Jaga ülejäävat ehitus- ja täitematerjali</p>
            </div>
            <div style="background: white; padding: 30px; border: 1px solid #e5e7eb;">
                <h2 style="color: #3A7025; margin: 0 0 16px; font-family: 'Montserrat', Arial, sans-serif;">Tere, {name}!</h2>
                <p style="color: #333; line-height: 1.6; margin: 0 0 16px;">Sinu konto on edukalt loodud Google konto kaudu. Oled nüüd osa kogukonnast, mis aitab vähendada ehitusmaterjali raiskamist!</p>
                <p style="color: #333; line-height: 1.6; margin: 0 0 8px; font-weight: 600;">Mida saad Fillio platvormil teha:</p>
                <ul style="color: #555; line-height: 1.8; margin: 0 0 24px; padding-left: 20px;">
                    <li>Lisa kuulutusi ülejäävate ehitusmaterjalide kohta</li>
                    <li>Otsi tasuta või soodsaid ehitusmaterjale</li>
                    <li>Halda oma kuulutusi ja kontaktandmeid</li>
                    <li>Sirvi interaktiivsel kaardil materjale oma piirkonnas</li>
                </ul>
                <p style="text-align: center; margin: 24px 0;">
                    <a href="https://fillio.eu" style="background: #5BB139; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 700; font-size: 16px; display: inline-block;">
                        Logi sisse
                    </a>
                </p>
            </div>
            <div style="background: #fef3c7; padding: 16px 24px; border-left: 4px solid #f59e0b; border-right: 1px solid #e5e7eb;">
                <p style="color: #92400e; font-size: 13px; margin: 0; line-height: 1.5;">
                    &#9888;&#65039; <strong>Turvalisuse märkus:</strong> Kui sa ei loonud Fillio kontot, palun võta meiega kohe ühendust aadressil
                    <a href="mailto:tarmo@tardek.com" style="color: #92400e;">tarmo@tardek.com</a>.
                </p>
            </div>
            <div style="background: #f3f4f6; padding: 20px 24px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; text-align: center;">
                <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px;">Küsimuste korral: <a href="mailto:tarmo@tardek.com" style="color: #5BB139;">tarmo@tardek.com</a></p>
                <p style="color: #9ca3af; font-size: 11px; margin: 0;">&copy; 2026 Fillio &middot; Tardek &middot; <a href="https://fillio.eu" style="color: #9ca3af;">fillio.eu</a></p>
            </div>
        </div>
        """)

        return jsonify({'success': True, 'user': user, 'is_new': True}), 201

# --- User's own listings ---
@app.route('/api/my-listings', methods=['GET'])
def my_listings():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Pead olema sisse logitud'}), 401

    cur = db_execute("SELECT * FROM listings WHERE user_id = ? ORDER BY created_at DESC", (user['id'],))
    return jsonify(db_fetchall(cur))

# --- Listings routes ---
@app.route('/api/listings', methods=['GET'])
def get_listings():
    material_type = request.args.get('material_type', '')
    search = request.args.get('search', '')

    query = "SELECT * FROM listings WHERE is_active = 1"
    params = []

    if material_type and material_type != 'koik':
        query += " AND material_type = ?"
        params.append(material_type)

    if search:
        if is_postgres():
            query += " AND (title ILIKE ? OR description ILIKE ? OR address ILIKE ?)"
        else:
            query += " AND (title LIKE ? OR description LIKE ? OR address LIKE ?)"
        search_term = f'%{search}%'
        params.extend([search_term, search_term, search_term])

    query += " ORDER BY created_at DESC"

    cur = db_execute(query, params)
    listings = db_fetchall(cur)
    return jsonify(listings)

@app.route('/api/listings', methods=['POST'])
def create_listing():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Kuulutuse lisamiseks pead olema sisse logitud'}), 401

    data = request.get_json()
    required = ['material_type', 'title', 'contact_name', 'address', 'latitude', 'longitude']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'Vali "{field}" on kohustuslik'}), 400

    db_execute("""
        INSERT INTO listings (material_type, title, description, quantity, unit, price_type, price,
            contact_name, contact_phone, contact_email, address, latitude, longitude, image_url, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['material_type'], data['title'], data.get('description', ''),
        data.get('quantity', ''), data.get('unit', 'tonni'),
        data.get('price_type', 'tasuta'), data.get('price', 0),
        data['contact_name'], data.get('contact_phone', ''),
        data.get('contact_email', ''), data['address'],
        data['latitude'], data['longitude'], data.get('image_url', ''),
        user['id']
    ))
    db_commit()
    return jsonify({'success': True, 'message': 'Kuulutus lisatud!'}), 201

@app.route('/api/listings/<int:listing_id>', methods=['DELETE'])
def delete_listing(listing_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Pead olema sisse logitud'}), 401

    cur = db_execute("SELECT user_id FROM listings WHERE id = ? AND is_active = 1", (listing_id,))
    listing = db_fetchone(cur)

    if not listing:
        return jsonify({'error': 'Kuulutust ei leitud'}), 404

    if listing.get('user_id') != user['id'] and user['role'] != 'admin':
        return jsonify({'error': 'Sul pole oigust seda kustutada'}), 403

    db_execute("UPDATE listings SET is_active = 0 WHERE id = ?", (listing_id,))
    db_commit()
    return jsonify({'success': True})

# --- Upload ---
@app.route('/api/upload', methods=['POST'])
def upload_file():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Pead olema sisse logitud'}), 401

    if 'file' not in request.files:
        return jsonify({'error': 'Faili ei leitud'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Faili ei valitud'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Lubatud: jpg, png, webp'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    return jsonify({'success': True, 'image_url': f'/static/uploads/{filename}'})

# --- Admin routes ---
@app.route('/api/admin/listings', methods=['GET'])
def admin_listings():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({'error': 'Ainult admin'}), 403

    cur = db_execute("SELECT * FROM listings ORDER BY created_at DESC")
    return jsonify(db_fetchall(cur))

@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({'error': 'Ainult admin'}), 403

    cur = db_execute("SELECT id, email, username, role, created_at FROM users ORDER BY created_at DESC")
    return jsonify(db_fetchall(cur))

@app.route('/api/admin/listings/<int:listing_id>', methods=['DELETE'])
def admin_delete_listing(listing_id):
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({'error': 'Ainult admin'}), 403

    db_execute("DELETE FROM listings WHERE id = ?", (listing_id,))
    db_commit()
    return jsonify({'success': True})

# --- Health check ---
@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

# --- Initialize DB on startup ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    print("")
    print("=== Fillio server tootab! ===")
    print("Ava brauseris: http://localhost:5000")
    print("Admin: admin@fillio.eu / admin123")
    print("")
    app.run(debug=True, host='0.0.0.0', port=5000)
