#!/usr/bin/python3
import sqlite3
import random
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = "ultimate_banking_v6_secret"  # Secure key

# --- Configuration (Matches script3.py) ---
DB_NAME = "atm_ultimate_web_v2.db"
ADMIN_ID = "9999"
ADMIN_PIN = "admin"

# --- Database Setup (Identical to script3.py) ---
def get_db():
    # Add timeout=10 (waits 10 seconds before erroring)
    conn = sqlite3.connect(DB_NAME, timeout=10) 
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Users
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        account_id TEXT PRIMARY KEY, name TEXT, pin TEXT, email TEXT, phone TEXT, id_card TEXT UNIQUE
    )''')
    # Balances
    cursor.execute('''CREATE TABLE IF NOT EXISTS balances (
        account_id TEXT, currency TEXT, amount REAL,
        PRIMARY KEY(account_id, currency), FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    # Transactions
    cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, timestamp TEXT,
        type TEXT, currency TEXT, amount REAL, note TEXT
    )''')
    # Cards
    cursor.execute('''CREATE TABLE IF NOT EXISTS cards (
        card_number TEXT PRIMARY KEY, account_id TEXT, cvc TEXT, status TEXT,
        expiry TEXT, card_type TEXT, currency TEXT, card_pin TEXT,
        card_name TEXT DEFAULT 'My Card', expense_limit REAL DEFAULT 5000.0,
        FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    # Bonuses
    cursor.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        account_id TEXT, currency TEXT, balance REAL DEFAULT 0,
        earned_this_month REAL DEFAULT 0, last_month_str TEXT,
        PRIMARY KEY(account_id, currency)
    )''')
    # Tickets
    cursor.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, issue TEXT,
        status TEXT DEFAULT 'OPEN', timestamp TEXT,
        FOREIGN KEY(account_id) REFERENCES users(account_id)
    )''')
    conn.commit()
    conn.close()

# --- Logic Helpers ---
def log_transaction(account_id, t_type, currency, amount, note=""):
    conn = get_db()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO transactions (account_id, timestamp, type, currency, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                 (account_id, timestamp, t_type, currency, amount, note))
    conn.commit()
    conn.close()

def process_bonus(user_id, currency, transfer_amount):
    """Exact logic from script3.py: 1% cashback, max 10.0/month"""
    bonus_amount = transfer_amount * 0.01
    current_month = datetime.now().strftime("%Y-%m")
    conn = get_db()
    
    # Check existing bonus record
    row = conn.execute("SELECT balance, earned_this_month, last_month_str FROM bonuses WHERE account_id=? AND currency=?", 
                       (user_id, currency)).fetchone()

    if not row:
        current_bal, earned_month, last_month = 0.0, 0.0, current_month
        conn.execute("INSERT INTO bonuses VALUES (?, ?, 0, 0, ?)", (user_id, currency, current_month))
    else:
        current_bal, earned_month, last_month = row['balance'], row['earned_this_month'], row['last_month_str']

    # Reset if month changed
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

# --- ROUTES ---

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

# 1. Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uid = request.form['id']
        pin = request.form['pin']
        
        if uid == ADMIN_ID and pin == ADMIN_PIN:
            session['user_id'] = ADMIN_ID
            session['is_admin'] = True
            return redirect(url_for('admin'))
            
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE account_id=?", (uid,)).fetchone()
        conn.close()
        
        if user and user['pin'] == pin:
            session['user_id'] = user['account_id']
            session['name'] = user['name']
            session['is_admin'] = False
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid ID or PIN", "danger")
    return render_template('login.html')

# 2. Register
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
        # Generate ID (1001 + count)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        new_id = str(1001 + count)
        
        try:
            conn.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", (new_id, name, pin, email, phone, id_card))
            conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (new_id, "AZN", 0.0)) # Initial Wallet
            conn.commit()
            flash(f"Success! Your User ID is {new_id}", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("ID Card already exists", "danger")
        finally:
            conn.close()
    return render_template('register.html')

# 3. Dashboard (Overview + Wallets)
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    
    # 1. Wallets
    balances = conn.execute("SELECT * FROM balances WHERE account_id=?", (uid,)).fetchall()
    
    # Quick Stats
    tx_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id=?", (uid,)).fetchone()[0]
    card_count = conn.execute("SELECT COUNT(*) FROM cards WHERE account_id=?", (uid,)).fetchone()[0]
    
    conn.close()
    return render_template('dashboard.html', balances=balances, tx_count=tx_count, card_count=card_count)

# 4. Transfer
@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    
    if request.method == 'POST':
        sender_card_num = request.form['sender_card']
        receiver_card_num = request.form['receiver_card']
        amount = float(request.form['amount'])
        pin = request.form['pin']
        
        # Validations
        sender = conn.execute("SELECT * FROM cards WHERE card_number=?", (sender_card_num,)).fetchone()
        receiver = conn.execute("SELECT * FROM cards WHERE card_number=?", (receiver_card_num,)).fetchone()
        
        if not sender or sender['card_pin'] != pin:
            flash("Wrong Card or PIN", "danger")
        elif not receiver:
            flash("Receiver card not found", "danger")
        elif receiver['status'] != 'ACTIVE':
            flash("Receiver card is inactive", "danger")
        elif receiver['currency'] != sender['currency']:
            flash(f"Currency mismatch. Sender is {sender['currency']}, Receiver is {receiver['currency']}", "danger")
        elif receiver['account_id'] == uid:
            flash("Cannot send to yourself", "warning")
        elif amount > sender['expense_limit']:
            flash(f"Amount exceeds limit ({sender['expense_limit']})", "danger")
        else:
            # Balance check
            bal_row = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (uid, sender['currency'])).fetchone()
            current_bal = bal_row['amount'] if bal_row else 0.0
            
            if amount > current_bal:
                flash("Insufficient funds in wallet", "danger")
            else:
                # Execute
                # Deduct from Sender
                conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (current_bal - amount, uid, sender['currency']))
                # Add to Receiver
                rcv_bal_row = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (receiver['account_id'], sender['currency'])).fetchone()
                rcv_bal = rcv_bal_row['amount'] if rcv_bal_row else 0.0
                if not rcv_bal_row:
                    conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (receiver['account_id'], sender['currency'], amount))
                else:
                    conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (rcv_bal + amount, receiver['account_id'], sender['currency']))
                
                # Logs
                log_transaction(uid, "SENT", sender['currency'], -amount, f"To {receiver_card_num}")
                log_transaction(receiver['account_id'], "RECEIVED", sender['currency'], amount, f"From {sender_card_num}")
                
                flash("Transfer Successful!", "success")
                conn.commit() # Commit transfer first
                process_bonus(uid, sender['currency'], amount) # Then bonus
                conn.close()
                return redirect(url_for('history'))

    my_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
    conn.close()
    return render_template('transfer.html', cards=my_cards)
# --- NEW MODULE: TOP UP (Add Money) ---
@app.route('/topup', methods=['GET', 'POST'])
def topup():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    
    # MOVED: Only open DB when we are sure we need it, or use try/finally
    conn = get_db()
    
    try:
        if request.method == 'POST':
            card_num = request.form['card_num']
            try:
                amount = float(request.form['amount'])
            except ValueError:
                flash("Invalid amount entered.", "danger")
                # Connection closes in 'finally' block now
                return redirect(url_for('topup'))
                
            if amount <= 0:
                flash("Amount must be positive.", "warning")
            else:
                card = conn.execute("SELECT currency, card_number FROM cards WHERE card_number=? AND account_id=?", (card_num, uid)).fetchone()
                
                if card:
                    curr = card['currency']
                    bal_row = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (uid, curr)).fetchone()
                    current_bal = bal_row['amount'] if bal_row else 0.0
                    
                    if not bal_row:
                        conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (uid, curr, amount))
                    else:
                        conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (current_bal + amount, uid, curr))
                    
                    log_transaction(uid, "DEPOSIT", curr, amount, f"Top Up via Card {card['card_number'][-4:]}")
                    
                    conn.commit()
                    flash(f"Successfully added {amount:.2f} {curr} to your wallet!", "success")
                    return redirect(url_for('dashboard'))
                else:
                    flash("Card not found.", "danger")

        # GET request logic
        my_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
        return render_template('topup.html', cards=my_cards)

    finally:
        # This runs NO MATTER WHAT happens above
        conn.close()

    # Get active cards to show in the dropdown
    my_cards = conn.execute("SELECT * FROM cards WHERE account_id=? AND status='ACTIVE'", (uid,)).fetchall()
    conn.close()
    return render_template('topup.html', cards=my_cards)
# 5. Cards & Settings
@app.route('/cards')
def cards():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    my_cards = conn.execute("SELECT * FROM cards WHERE account_id=?", (session['user_id'],)).fetchall()
    conn.close()
    return render_template('cards.html', cards=my_cards)

@app.route('/card_settings/<card_num>', methods=['GET', 'POST'])
def card_settings(card_num):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    card = conn.execute("SELECT * FROM cards WHERE card_number=? AND account_id=?", (card_num, session['user_id'])).fetchone()
    
    if not card:
        return redirect(url_for('cards'))
        
    if request.method == 'POST':
        action = request.form['action']
        
        if card['status'] == 'PENDING':
            flash("Access Denied: Card is Pending Approval", "danger")
        else:
            if action == 'rename':
                conn.execute("UPDATE cards SET card_name=? WHERE card_number=?", (request.form['new_name'], card_num))
                flash("Card renamed", "success")
            elif action == 'change_pin':
                conn.execute("UPDATE cards SET card_pin=? WHERE card_number=?", (request.form['new_pin'], card_num))
                flash("PIN updated", "success")
            elif action == 'change_limit':
                try:
                    conn.execute("UPDATE cards SET expense_limit=? WHERE card_number=?", (float(request.form['new_limit']), card_num))
                    flash("Limit updated", "success")
                except: flash("Invalid Limit", "danger")
            elif action == 'toggle_block':
                new_status = 'BLOCKED' if card['status'] == 'ACTIVE' else 'ACTIVE'
                conn.execute("UPDATE cards SET status=? WHERE card_number=?", (new_status, card_num))
                flash(f"Card {new_status}", "warning")
        
        conn.commit()
        conn.close()
        return redirect(url_for('card_settings', card_num=card_num))

    conn.close()
    return render_template('card_settings.html', card=card)

# 6. Order Card
@app.route('/order_card', methods=['GET', 'POST'])
def order_card():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    
    if request.method == 'POST':
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM cards WHERE account_id=?", (uid,)).fetchone()[0]
        if count >= 3:
            flash("Limit Reached (Max 3 Cards)", "danger")
        else:
            currency = request.form['currency']
            ctype = request.form['card_type']
            
            # Generate Details
            prefix = "4" if ctype == "VISA" else "5"
            c_num = prefix + ''.join([str(random.randint(0,9)) for _ in range(15)])
            cvc = ''.join([str(random.randint(0,9)) for _ in range(3)])
            pin = ''.join([str(random.randint(0,9)) for _ in range(4)])
            
            conn.execute('''INSERT INTO cards (card_number, account_id, cvc, status, expiry, card_type, currency, card_pin)
                            VALUES (?, ?, ?, 'PENDING', '12/30', ?, ?, ?)''', 
                            (c_num, uid, cvc, ctype, currency, pin))
            conn.commit()
            flash(f"Ordered! PIN: {pin}. Status: PENDING", "success")
        conn.close()
        return redirect(url_for('cards'))
        
    return render_template('order_card.html')

# 7. Bonuses
@app.route('/bonuses')
def bonuses():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    # Get bonuses
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
        # Get current wallet balance
        cur_bal = conn.execute("SELECT amount FROM balances WHERE account_id=? AND currency=?", (uid, currency)).fetchone()
        wallet_amount = cur_bal['amount'] if cur_bal else 0.0
        
        # Move bonus to wallet
        if not cur_bal:
             conn.execute("INSERT INTO balances VALUES (?, ?, ?)", (uid, currency, row['balance']))
        else:
             conn.execute("UPDATE balances SET amount=? WHERE account_id=? AND currency=?", (wallet_amount + row['balance'], uid, currency))
        
        # Reset bonus
        conn.execute("UPDATE bonuses SET balance=0 WHERE account_id=? AND currency=?", (uid, currency))
        
        log_transaction(uid, "BONUS_CLAIM", currency, row['balance'], "Claimed Cashback")
        conn.commit()
        flash("Bonus Claimed!", "success")
    else:
        flash("Minimum 1.00 required to claim", "warning")
    
    conn.close()
    return redirect(url_for('bonuses'))

# 8. History
@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    txs = conn.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY id DESC LIMIT 20", (session['user_id'],)).fetchall()
    conn.close()
    return render_template('history.html', txs=txs)

# 9. Support
@app.route('/support', methods=['GET', 'POST'])
def support():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    conn = get_db()
    
    if request.method == 'POST':
        issue = request.form['issue']
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute("INSERT INTO tickets (account_id, issue, timestamp) VALUES (?, ?, ?)", (uid, issue, ts))
        conn.commit()
        flash("Ticket Submitted", "success")
    
    tickets = conn.execute("SELECT * FROM tickets WHERE account_id=?", (uid,)).fetchall()
    conn.close()
    return render_template('support.html', tickets=tickets)

# 10. Admin
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('is_admin'): return redirect(url_for('index'))
    conn = get_db()
    
    if request.method == 'POST':
        if 'approve_card' in request.form:
            c_num = request.form['card_num']
            conn.execute("UPDATE cards SET status='ACTIVE' WHERE card_number=?", (c_num,))
            flash("Card Approved", "success")
        elif 'resolve_ticket' in request.form:
            tid = request.form['ticket_id']
            conn.execute("UPDATE tickets SET status='RESOLVED' WHERE id=?", (tid,))
            flash("Ticket Resolved", "success")
        conn.commit()

    pending_cards = conn.execute("SELECT * FROM cards WHERE status='PENDING'").fetchall()
    tickets = conn.execute("SELECT * FROM tickets WHERE status='OPEN'").fetchall()
    conn.close()
    return render_template('admin.html', cards=pending_cards, tickets=tickets)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)




