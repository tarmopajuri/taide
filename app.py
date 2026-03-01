"""
Taide - Ulejaava taitematerjali jagamise platvorm
taide.ee
"""
import os
import uuid
from flask import Flask, request, jsonify, render_template, g, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', 'taide-dev-secret-key-change-me')

# --- Upload config ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
            ('admin@taide.ee', 'Admin', generate_password_hash('admin123'), 'admin')
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
    return render_template('index.html')

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
        return jsonify({'error': 'Pead olema sisse logitud'}), 401

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
    print("=== Taide server tootab! ===")
    print("Ava brauseris: http://localhost:5000")
    print("Admin: admin@taide.ee / admin123")
    print("")
    app.run(debug=True, host='0.0.0.0', port=5000)
