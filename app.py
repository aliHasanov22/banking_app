#!/usr/bin/python3
import sqlite3
import random
import re
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "ultimate_banking_v6_secret"

# --- CONFIGURATION (V7) ---
DB_NAME = "atm_ultimate_web_v3.db"  # Using V3 to support new tables
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
    
    # 1. Users (Updated with profile_pic for V7)
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        account_id TEXT PRIMARY KEY, name TEXT, pin TEXT, email TEXT, phone TEXT, 
        id_card TEXT UNIQUE, profile_pic TEXT DEFAULT 'default.png'
    )''')
    
    # 2. Chat Messages (New in V7)
    cursor.execute('''CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, sender TEXT, 
        message TEXT, timestamp TEXT, is_read INTEGER DEFAULT 0
    )''')

    # 3. Balances
    cursor.execute('''CREATE TABLE IF NOT EXISTS balances (
        account_id TEXT, currency TEXT, amount REAL,
        PRIMARY KEY(account_id, currency), FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    
    # 4. Transactions
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, timestamp TEXT,
        type TEXT, currency TEXT, amount REAL, note TEXT
    )''')
    
    # 5. Cards
    cursor.execute('''CREATE TABLE IF NOT EXISTS cards (
        card_number TEXT PRIMARY KEY, account_id TEXT, cvc TEXT, status TEXT,
        expiry TEXT, card_type TEXT, currency TEXT, card_pin TEXT,
        card_name TEXT DEFAULT 'My Card', expense_limit REAL DEFAULT 5000.0,
        FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    
    # 6. Bonuses
    cursor.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        account_id TEXT, currency TEXT, balance REAL DEFAULT 0,
        earned_this_month REAL DEFAULT 0, last_month_str TEXT,
        PRIMARY KEY(account_id, currency)
    )''')
    
    # 7. Tickets
    cursor.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, issue TEXT,
        status TEXT DEFAULT 'OPEN', timestamp TEXT,
        FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    
    conn.commit()
    conn.close()

def check_and_update_db_schema():
    """Helper to ensure users have a status column for suspension"""
    conn = get_db()
    try:
        # Try to select the column to see if it exists
        conn.execute("SELECT status FROM users LIMIT 1")
    except sqlite3.OperationalError:
        # If error, column missing -> Add it
        print("Migrating DB: Adding 'status' column to users table...")
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'ACTIVE'")
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

def process_bonus(user_id, currency, transfer_amount):
    """1% cashback logic"""
    bonus_amount = transfer_amount * 0.01
    current_month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    
    row = conn.execute("SELECT balance, earned_this_month, last_month_str FROM bonuses WHERE account_id=? AND currency=?", 
                       (user_id, currency)).fetchone()

    if not row:
        current_bal, earned_month, last_month = 0.0, 0.0, current_month
        conn.execute("INSERT INTO bonuses VALUES (?, ?, 0, 0, ?)", (user_id, currency, current_month))
    else:
        current_bal, earned_month, last_month = row['balance'], row['earned_this_month'], row['last_month_str']

    if last_month != current_month:
        earned_month = 0.0
        last_month = current_month

    remaining_cap = 10.0 - earned_month
    if remaining_cap > 0:
        final_bonus = min(bonus_amount, remaining_cap)
        if final_bonus > 0:
            conn.execute('''UPDATE bonuses SET balance=?, earned_this_month=?, last_month_str=? 
                            WHERE account_id=? AND currency=?''',
                         (current_bal + final_bonus, earned_month + final_bonus, last_month, user_id, currency))
            flash(f"BONUS: You earned {final_bonus:.2f} {currency} cashback!", "success")
    
    conn.commit()
    conn.close()

# --- CORE ROUTES ---

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
        if uid == "0000" and pin == "1234":
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
            session['profile_pic'] = user['profile_pic'] # V7 Feature
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
        
        if not re.match(r'^[A-Z0-9]{7}$', id_card):
            flash("ID must be 7 chars (A-Z, 0-9)", "warning")
            return redirect(url_for('register'))
            
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new_id = str(1001 + count)
        
        try:
            # Using specific columns to handle default profile_pic safely
            conn.execute("INSERT INTO users (account_id, name, pin, email, phone, id_card) VALUES (?, ?, ?, ?, ?, ?)", 
                         (new_id, name, pin, email, phone, id_card))
            conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (new_id, "AZN", 0.0))
            conn.commit()
            flash(f"Success! Your User ID is {new_id}", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("ID Card already exists", "danger")
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
    
    # Balances
    balances = conn.execute("SELECT * FROM balances WHERE account_id=?", (uid,)).fetchall()
    bal_dict = {row['currency']: row['amount'] for row in balances}
    
    # Cards with Balance attached
    db_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
    cards_with_balance = []
    for c in db_cards:
        c_dict = dict(c)
        c_dict['balance'] = bal_dict.get(c['currency'], 0.00)
        cards_with_balance.append(c_dict)
    
    # Stats
    tx_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id=?", (uid,)).fetchone()[0]
    conn.close()
    return render_template('dashboard.html', balances=balances, cards=cards_with_balance, tx_count=tx_count)

# --- V7 FEATURES (Chat, Profile, Notifications) ---

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()

    if request.method == 'POST':
        # Upload Pic
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and allowed_file(file.filename):
                filename = secure_filename(f"{uid}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute("UPDATE users SET profile_pic=? WHERE account_id=?", (filename, uid))
                session['profile_pic'] = filename
                flash("Profile picture updated!", "success")
        
        # Update Details
        if 'email' in request.form:
            conn.execute("UPDATE users SET email=?, phone=? WHERE account_id=?", 
                         (request.form['email'], request.form['phone'], uid))
            flash("Contact info updated!", "success")
        
        conn.commit()
        return redirect(url_for('profile'))

    user = conn.execute("SELECT * FROM users WHERE account_id=?", (uid,)).fetchone()
    conn.close()
    return render_template('profile.html', user=user)

@app.route('/api/notifications')
def get_notifications():
    if 'user_id' not in session: return jsonify([])
    conn = get_db()
    txs = conn.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY id DESC LIMIT 5", (session['user_id'],)).fetchall()
    conn.close()
    
    data = []
    for t in txs:
        data.append({
            'timestamp': t['timestamp'],
            'message': f"{t['type']} {t['amount']} {t['currency']} - {t['note']}"
        })
    return jsonify(data)

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
        
        # Auto-reply Simulation
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
    msgs = conn.execute("SELECT * FROM chats WHERE account_id=? ORDER BY id ASC LIMIT 50", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(m) for m in msgs])

# --- FINANCIAL ROUTES (Transfer, Topup, Cards) ---
#tansfer 1.1v
@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    
    try:
        if request.method == 'POST':
            sender_card_num = request.form['sender_card']
            receiver_card_num = request.form['receiver_card']
            try: amount = float(request.form['amount'])
            except: amount = 0
            pin = request.form['pin']
            
            sender_card = conn.execute("SELECT * FROM cards WHERE card_number=?", (sender_card_num,)).fetchone()
            rcv = conn.execute("SELECT account_id, status, currency FROM cards WHERE card_number=?", (receiver_card_num,)).fetchone()
            
            # --- UPDATED FEE LOGIC (Excess Only) ---
            total_fee = 0.0
            fee_note = ""
            if amount > 5000:
                excess_amount = amount - 5000
                # Fee is 2% of the EXCESS amount, but minimum 1.0
                total_fee = max(excess_amount * 0.02, 1.0)
                fee_note = f"Fee (2% on {excess_amount} excess)"

            total_deduction = amount + total_fee
            # ---------------------------------------

            # Validation
            if not sender_card or sender_card['card_pin'] != pin: flash("Invalid Card or PIN", "danger")
            elif not rcv: flash("Receiver not found", "danger")
            elif rcv['status'] != 'ACTIVE': flash("Receiver card inactive", "danger")
            elif rcv['currency'] != sender_card['currency']: flash("Currency mismatch.", "danger")
            elif rcv['account_id'] == uid: flash("Cannot send to self.", "warning")
            elif amount > sender_card['expense_limit']: 
                flash(f"Amount exceeds your card limit of {sender_card['expense_limit']}", "danger")
            else:
                bal_row = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (uid, sender_card['currency'])).fetchone()
                bal = bal_row['amount'] if bal_row else 0.0
                
                if total_deduction > bal:
                    flash(f"Insufficient funds. Total needed: {total_deduction:.2f} (Amount + {total_fee} Fee)", "danger")
                else:
                    # Execute Transfer
                    conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (bal - total_deduction, uid, sender_card['currency']))
                    
                    rcv_bal_row = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (rcv['account_id'], sender_card['currency'])).fetchone()
                    if not rcv_bal_row:
                        conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (rcv['account_id'], sender_card['currency'], amount))
                    else:
                        conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (rcv_bal_row['amount'] + amount, rcv['account_id'], sender_card['currency']))
                    
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    curr = sender_card['currency']
                    
                    conn.execute("INSERT INTO transactions (account_id, timestamp, type, currency, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                                 (uid, ts, "SENT", curr, -amount, f"To {receiver_card_num}"))
                    
                    if total_fee > 0:
                         conn.execute("INSERT INTO transactions (account_id, timestamp, type, currency, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                                 (uid, ts, "FEE", curr, -total_fee, fee_note))

                    conn.execute("INSERT INTO transactions (account_id, timestamp, type, currency, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                                 (rcv['account_id'], ts, "RECEIVED", curr, amount, f"From {sender_card_num}"))
                    
                    conn.commit()
                    process_bonus(uid, curr, amount)
                    flash(f"Sent {amount} {curr} successfully!", "success")
                    return redirect(url_for('history'))

        # Prepare data for form
        balances = conn.execute("SELECT * FROM balances WHERE account_id=?", (uid,)).fetchall()
        bal_dict = {row['currency']: row['amount'] for row in balances}
        db_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
        
        active_cards_with_bal = []
        for c in db_cards:
             c_dict = dict(c)
             c_dict['balance'] = bal_dict.get(c['currency'], 0.00)
             active_cards_with_bal.append(c_dict)

        return render_template('transfer.html', cards=active_cards_with_bal)
    except Exception as e:
        print(e)
        return redirect(url_for('transfer'))
    finally:
        try: conn.close()
        except: pass

@app.route('/topup', methods=['GET', 'POST'])
def topup():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    
    try:
        if request.method == 'POST':
            card_num = request.form['card_num']
            try: amount = float(request.form['amount'])
            except: amount = 0
            
            if amount <= 0:
                flash("Amount must be positive.", "warning")
            else:
                card = conn.execute("SELECT currency FROM cards WHERE card_number=? AND account_id=?", (card_num, uid)).fetchone()
                if card:
                    curr = card['currency']
                    bal_row = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (uid, curr)).fetchone()
                    current_bal = bal_row['amount'] if bal_row else 0.0
                    
                    if not bal_row: conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (uid, curr, amount))
                    else: conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (current_bal + amount, uid, curr))
                    
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("INSERT INTO transactions (account_id, timestamp, type, currency, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                                 (uid, ts, "DEPOSIT", curr, amount, f"Top Up via Card {card_num[-4:]}"))
                    conn.commit()
                    flash(f"Added {amount} {curr}!", "success")
                    return redirect(url_for('dashboard'))
                else:
                    flash("Card not found.", "danger")
        
        my_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
        return render_template('topup.html', cards=my_cards)
    finally:
        conn.close()

@app.route('/cards')
def cards():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    balances = conn.execute("SELECT * FROM balances WHERE account_id=?", (uid,)).fetchall()
    bal_dict = {row['currency']: row['amount'] for row in balances}
    db_cards = conn.execute("SELECT * FROM cards WHERE account_id=?", (uid,)).fetchall()
    
    cards_out = []
    for c in db_cards:
        c_dict = dict(c)
        c_dict['balance'] = bal_dict.get(c['currency'], 0.00)
        cards_out.append(c_dict)
    conn.close()
    return render_template('cards.html', cards=cards_out)

@app.route('/card_settings/<card_num>', methods=['GET', 'POST'])
def card_settings(card_num):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    card = conn.execute("SELECT * FROM cards WHERE card_number=? AND account_id=?", (card_num, session['user_id'])).fetchone()
    
    if not card: return redirect(url_for('cards'))
        
    if request.method == 'POST':
        action = request.form['action']
        if card['status'] == 'PENDING':
            flash("Card is Pending", "danger")
        else:
            if action == 'rename':
                conn.execute("UPDATE cards SET card_name=? WHERE card_number=?", (request.form['new_name'], card_num))
            elif action == 'change_pin':
                conn.execute("UPDATE cards SET card_pin=? WHERE card_number=?", (request.form['new_pin'], card_num))
            elif action == 'change_limit':
                try: conn.execute("UPDATE cards SET expense_limit=? WHERE card_number=?", (float(request.form['new_limit']), card_num))
                except: pass
            elif action == 'toggle_block':
                new_status = 'BLOCKED' if card['status'] == 'ACTIVE' else 'ACTIVE'
                conn.execute("UPDATE cards SET status=? WHERE card_number=?", (new_status, card_num))
            
            conn.commit()
            flash("Settings Updated", "success")
            return redirect(url_for('card_settings', card_num=card_num))

    conn.close()
    return render_template('card_settings.html', card=card)

@app.route('/order_card', methods=['GET', 'POST'])
def order_card():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    if request.method == 'POST':
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM cards WHERE account_id=?", (uid,)).fetchone()[0]
        if count >= 3:
            flash("Max 3 Cards allowed", "danger")
        else:
            currency = request.form['currency']
            ctype = request.form['card_type']
            prefix = "4" if ctype == "VISA" else "5"
            c_num = prefix + ''.join([str(random.randint(0,9)) for _ in range(15)])
            cvc = ''.join([str(random.randint(0,9)) for _ in range(3)])
            pin = ''.join([str(random.randint(0,9)) for _ in range(4)])
            
            conn.execute("INSERT INTO cards (card_number, account_id, cvc, status, expiry, card_type, currency, card_pin) VALUES (?, ?, ?, 'PENDING', '12/30', ?, ?, ?)", 
                         (c_num, uid, cvc, ctype, currency, pin))
            conn.commit()
            flash(f"Ordered! PIN: {pin}", "success")
        conn.close()
        return redirect(url_for('cards'))
    return render_template('order_card.html')

@app.route('/bonuses')
def bonuses():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    my_bonuses = conn.execute("SELECT * FROM bonuses WHERE account_id=?", (session['user_id'],)).fetchall()
    conn.close()
    return render_template('bonuses.html', bonuses=my_bonuses)

@app.route('/claim_bonus/<currency>')
def claim_bonus(currency):
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    row = conn.execute("SELECT balance FROM bonuses WHERE account_id=? AND currency=?", (uid, currency)).fetchone()
    
    if row and row['balance'] >= 1.0:
        cur_bal = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (uid, currency)).fetchone()
        wallet_amount = cur_bal['amount'] if cur_bal else 0.0
        
        if not cur_bal: conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (uid, currency, row['balance']))
        else: conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (wallet_amount + row['balance'], uid, currency))
        
        conn.execute("UPDATE bonuses SET balance=0 WHERE account_id=? AND currency=?", (uid, currency))
        log_transaction(uid, "BONUS_CLAIM", currency, row['balance'], "Claimed Cashback", conn)
        conn.commit()
        flash("Bonus Claimed!", "success")
    else:
        flash("Minimum 1.00 required", "warning")
    
    conn.close()
    return redirect(url_for('bonuses'))

# --- history
@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    
    # 1. Fetch Transactions
    txs = conn.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY id DESC LIMIT 20", (session['user_id'],)).fetchall()
    
    # 2. Calculate Chart Data (Income vs Expense)
    income = 0
    expense = 0
    for t in txs:
        if t['amount'] > 0:
            income += t['amount']
        else:
            expense += abs(t['amount'])
            
    conn.close()
    
    # Pass data to template
    return render_template('history.html', txs=txs, chart_income=income, chart_expense=expense)

@app.route('/support', methods=['GET', 'POST'])
def support():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    if request.method == 'POST':
        conn.execute("INSERT INTO tickets (account_id, issue, timestamp) VALUES (?, ?, ?)", 
                     (uid, request.form['issue'], datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        flash("Ticket Submitted", "success")
    tickets = conn.execute("SELECT * FROM tickets WHERE account_id=?", (uid,)).fetchall()
    conn.close()
    return render_template('support.html', tickets=tickets)
#new
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    # 1. Security Check
    if not session.get('is_admin'): 
        return redirect(url_for('login'))
    
    # Ensure DB schema is up to date (adds status column if missing)
    check_and_update_db_schema()
    
    conn = get_db()
    searched_user = None
    user_cards = []
    user_txs = []
    user_bals = []
    suspicious_activity = []
    
    # 2. Handle Suspension / Activation
    if 'action' in request.args and 'target_id' in request.args:
        action = request.args.get('action')
        target = request.args.get('target_id')
        new_status = 'SUSPENDED' if action == 'suspend' else 'ACTIVE'
        conn.execute("UPDATE users SET status=? WHERE account_id=?", (new_status, target))
        conn.commit()
        flash(f"User {target} is now {new_status}", "success")
        return redirect(url_for('admin', search_query=target))

    # 3. Handle Search
    # Check if we have a search query from POST (form) or GET (url redirect)
    query = request.form.get('search_query') or request.args.get('search_query')
    
    if query:
        # A. Find User
        searched_user = conn.execute("SELECT * FROM users WHERE account_id=? OR id_card=? OR email=?", (query, query, query)).fetchone()
        
        if searched_user:
            uid = searched_user['account_id']
            
            # B. Get Financials
            user_bals = conn.execute("SELECT * FROM balances WHERE account_id=?", (uid,)).fetchall()
            
            # C. Get Cards (We will mask them in the template)
            user_cards = conn.execute("SELECT * FROM cards WHERE account_id=?", (uid,)).fetchall()
            
            # D. Get Transactions (Last 50)
            user_txs = conn.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY id DESC LIMIT 50", (uid,)).fetchall()
            
            # E. Detect "Suspicious" Activity
            # Logic: Any transaction > 5000 OR any transaction labeled 'FEE' (high frequency check)
            suspicious_activity = conn.execute("""
                SELECT * FROM transactions 
                WHERE account_id=? AND (abs(amount) > 5000 OR type='FEE')
                ORDER BY id DESC
            """, (uid,)).fetchall()

    conn.close()
    return render_template('admin.html', 
                           user=searched_user, 
                           cards=user_cards, 
                           txs=user_txs, 
                           balances=user_bals,
                           suspicious=suspicious_activity,
                           search_query=query)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)






