"""
Taide - Ulejaava taitematerjali jagamise platvorm
taide.ee
"""
import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- Database config ---
# Use PostgreSQL if DATABASE_URL is set (Render), otherwise SQLite (local)
DATABASE_URL = os.environ.get('DATABASE_URL', '')

def is_postgres():
    return bool(DATABASE_URL)

def get_db():
    if 'db' not in g:
        if is_postgres():
            import psycopg2
            import psycopg2.extras
            # Render uses postgresql:// but psycopg2 needs postgresql://
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
        # Convert ? placeholders to %s for PostgreSQL
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
        return cursor.fetchall()  # Already returns dicts with RealDictCursor
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
                is_active INTEGER DEFAULT 1
            )
        """)
        db.commit()
        # Check if empty
        cur.execute("SELECT COUNT(*) FROM listings")
        count = cur.fetchone()[0]
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'taide.db')
        db = sqlite3.connect(db_path)
        cur = db.cursor()
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
                is_active INTEGER DEFAULT 1
            )
        """)
        db.commit()
        cur.execute("SELECT COUNT(*) FROM listings")
        count = cur.fetchone()[0]

    # Add sample data if empty
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
    data = request.get_json()

    required = ['material_type', 'title', 'contact_name', 'address', 'latitude', 'longitude']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'Field "{field}" is required'}), 400

    db_execute("""
        INSERT INTO listings (material_type, title, description, quantity, unit, price_type, price,
            contact_name, contact_phone, contact_email, address, latitude, longitude, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['material_type'], data['title'], data.get('description', ''),
        data.get('quantity', ''), data.get('unit', 'tonni'),
        data.get('price_type', 'tasuta'), data.get('price', 0),
        data['contact_name'], data.get('contact_phone', ''),
        data.get('contact_email', ''), data['address'],
        data['latitude'], data['longitude'], data.get('image_url', '')
    ))
    db_commit()
    return jsonify({'success': True, 'message': 'Kuulutus lisatud!'}), 201

@app.route('/api/listings/<int:listing_id>', methods=['DELETE'])
def delete_listing(listing_id):
    db_execute("UPDATE listings SET is_active = 0 WHERE id = ?", (listing_id,))
    db_commit()
    return jsonify({'success': True})

# --- Health check for Render ---
@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    init_db()
    print("")
    print("=== Taide server tootab! ===")
    print("Ava brauseris: http://localhost:5000")
    print("")
    app.run(debug=True, host='0.0.0.0', port=5000)
