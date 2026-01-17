import sqlite3
import random
import re
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "ultimate_banking_v6_secret"

# --- CONFIGURATION ---
DB_NAME = "atm_ultimate_web_v3.db" # Changed version to v3
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- DATABASE SETUP ---
def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users (Added profile_pic)
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        account_id TEXT PRIMARY KEY, name TEXT, pin TEXT, email TEXT, phone TEXT, 
        id_card TEXT UNIQUE, profile_pic TEXT DEFAULT 'default.png'
    )''')
    
    # Chat Messages (New Table)
    cursor.execute('''CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, sender TEXT, 
        message TEXT, timestamp TEXT, is_read INTEGER DEFAULT 0
    )''')

    # Existing Tables...
    cursor.execute('''CREATE TABLE IF NOT EXISTS balances (
        account_id TEXT, currency TEXT, amount REAL,
        PRIMARY KEY(account_id, currency), FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, timestamp TEXT,
        type TEXT, currency TEXT, amount REAL, note TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS cards (
        card_number TEXT PRIMARY KEY, account_id TEXT, cvc TEXT, status TEXT,
        expiry TEXT, card_type TEXT, currency TEXT, card_pin TEXT,
        card_name TEXT DEFAULT 'My Card', expense_limit REAL DEFAULT 5000.0,
        FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        account_id TEXT, currency TEXT, balance REAL DEFAULT 0,
        earned_this_month REAL DEFAULT 0, last_month_str TEXT,
        PRIMARY KEY(account_id, currency)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, issue TEXT,
        status TEXT DEFAULT 'OPEN', timestamp TEXT,
        FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    conn.commit()
    conn.close()

# --- HELPERS ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def log_transaction(account_id, t_type, currency, amount, note="", conn=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True
    
    conn.execute("INSERT INTO transactions (account_id, timestamp, type, currency, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                 (account_id, timestamp, t_type, currency, amount, note))
    conn.commit()
    if should_close: conn.close()

# --- ROUTES ---

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uid = request.form['id']
        pin = request.form['pin']
        
        # Admin Login
        if uid == "9999" and pin == "admin":
            session['user_id'] = "9999"
            session['is_admin'] = True
            session['name'] = "Administrator"
            return redirect(url_for('admin'))
            
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE account_id=?", (uid,)).fetchone()
        conn.close()
        
        if user and user['pin'] == pin:
            session['user_id'] = user['account_id']
            session['name'] = user['name']
            session['profile_pic'] = user['profile_pic'] # Store pic in session
            session['is_admin'] = False
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid ID or PIN", "danger")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        id_card = request.form['id_card'].upper()
        pin = request.form['pin']
        
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new_id = str(1001 + count)
        
        try:
            conn.execute("INSERT INTO users (account_id, name, pin, email, phone, id_card) VALUES (?, ?, ?, ?, ?, ?)", 
                         (new_id, name, pin, email, phone, id_card))
            conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (new_id, "AZN", 0.0))
            conn.commit()
            flash(f"Success! User ID: {new_id}", "success")
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"Error: {e}", "danger")
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    balances = conn.execute("SELECT * FROM balances WHERE account_id=?", (uid,)).fetchall()
    bal_dict = {row['currency']: row['amount'] for row in balances}
    
    db_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
    cards_with_balance = []
    for c in db_cards:
        c_dict = dict(c)
        c_dict['balance'] = bal_dict.get(c['currency'], 0.00)
        cards_with_balance.append(c_dict)
        
    tx_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id=?", (uid,)).fetchone()[0]
    conn.close()
    return render_template('dashboard.html', balances=balances, cards=cards_with_balance, tx_count=tx_count)

# --- PROFILE PAGE ---
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()

    if request.method == 'POST':
        # Handle Profile Picture Upload
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{uid}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute("UPDATE users SET profile_pic=? WHERE account_id=?", (filename, uid))
                session['profile_pic'] = filename # Update session
                flash("Profile picture updated!", "success")
        
        # Handle Info Update
        if 'email' in request.form:
            conn.execute("UPDATE users SET email=?, phone=? WHERE account_id=?", 
                         (request.form['email'], request.form['phone'], uid))
            flash("Contact info updated!", "success")
        
        conn.commit()
        return redirect(url_for('profile'))

    user = conn.execute("SELECT * FROM users WHERE account_id=?", (uid,)).fetchone()
    conn.close()
    return render_template('profile.html', user=user)

# --- NOTIFICATIONS API ---
@app.route('/api/notifications')
def get_notifications():
    if 'user_id' not in session: return jsonify([])
    conn = get_db()
    # Fetch last 5 transactions as notifications
    txs = conn.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY id DESC LIMIT 5", (session['user_id'],)).fetchall()
    conn.close()
    
    data = []
    for t in txs:
        data.append({
            'timestamp': t['timestamp'],
            'message': f"{t['type']} {t['amount']} {t['currency']} - {t['note']}"
        })
    return jsonify(data)

# --- CHAT SYSTEM ---
@app.route('/api/chat/send', methods=['POST'])
def send_chat():
    if 'user_id' not in session: return jsonify({'status': 'error'})
    uid = session['user_id']
    msg = request.form.get('message')
    if msg:
        conn = get_db()
        ts = datetime.now().strftime("%H:%M")
        conn.execute("INSERT INTO chats (account_id, sender, message, timestamp) VALUES (?, ?, ?, ?)", 
                     (uid, 'USER', msg, ts))
        conn.commit()
        
        # Auto-reply from Admin (Simulation)
        if "help" in msg.lower():
            conn.execute("INSERT INTO chats (account_id, sender, message, timestamp) VALUES (?, ?, ?, ?)", 
                         (uid, 'ADMIN', "Support: We have received your request. An agent will reply shortly.", ts))
            conn.commit()
            
        conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/chat/get')
def get_chat():
    if 'user_id' not in session: return jsonify([])
    uid = session['user_id']
    conn = get_db()
    # Get last 20 messages
    msgs = conn.execute("SELECT * FROM chats WHERE account_id=? ORDER BY id ASC LIMIT 50", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(m) for m in msgs])

# --- ADMIN PANEL (Simplified) ---
@app.route('/admin')
def admin():
    if not session.get('is_admin'): return redirect(url_for('login'))
    return "Admin Panel - (Use previous code logic here)"

# --- OTHER ROUTES (Transfer, Cards, etc.) keep same as before ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
