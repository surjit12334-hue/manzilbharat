from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import psycopg2
import psycopg2.extras
import bcrypt
import re
import os
import time
import hashlib
import hmac
import json
import secrets
import base64
from dotenv import load_dotenv
from datetime import date, datetime, timedelta
from functools import wraps
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests


def base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def base64url_decode(s):
    s += '=' * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
JWT_SECRET = os.getenv('JWT_SECRET', secrets.token_hex(32))
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@manzilbharat.in')
ADMIN_PASSWORD_HASH = bcrypt.hashpw(
    (os.getenv('ADMIN_PASSWORD', 'Admin@12345')).encode('utf-8'),
    bcrypt.gensalt()
).decode('utf-8')

app = Flask(__name__, static_folder='.', static_url_path='')

CORS(app, resources={r"/api/*": {"origins": [
    "http://localhost:5000",
    "http://localhost:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "https://surjit12334-hue.github.io"
]}})


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


rate_limit_store = {}

def rate_limit(max_requests=30, window=60):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr
            key = f"{f.__name__}:{ip}"
            now = time.time()
            if key not in rate_limit_store:
                rate_limit_store[key] = []
            rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window]
            if len(rate_limit_store[key]) >= max_requests:
                return jsonify({'error': 'Too many requests. Please try again later.'}), 429
            rate_limit_store[key].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def create_token(user_id, email):
    payload = {
        'user_id': user_id,
        'email': email,
        'exp': (datetime.utcnow() + timedelta(days=7)).isoformat(),
        'iat': datetime.utcnow().isoformat()
    }
    data = json.dumps(payload, sort_keys=True)
    sig = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    encoded = base64url_encode(data.encode())
    return f"{encoded}.{sig}"


def verify_token(token):
    try:
        parts = token.split('.')
        if len(parts) != 2:
            return None
        data = base64url_decode(parts[0]).decode()
        sig = parts[1]
        expected = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        if datetime.fromisoformat(payload['exp']) < datetime.utcnow():
            return None
        return payload
    except Exception:
        return None


def get_db():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )
    conn.autocommit = False
    return conn


def verify_google_token(credential):
    try:
        idinfo = id_token.verify_oauth2_token(credential, google_requests.Request(), GOOGLE_CLIENT_ID)
        return {
            'email': idinfo.get('email'),
            'name': idinfo.get('name', ''),
            'picture': idinfo.get('picture', '')
        }
    except Exception:
        return None


@app.route('/api/signup', methods=['POST'])
@rate_limit(max_requests=10, window=60)
def signup():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    contact = (data.get('contact') or '').strip()
    gender = (data.get('gender') or '').strip()
    dob = (data.get('dob') or '').strip()
    marital_status = (data.get('marital_status') or '').strip()
    residential_address = (data.get('residential_address') or '').strip()
    pincode = (data.get('pincode') or '').strip()
    password = data.get('password') or ''

    errors = {}

    if not name:
        errors['name'] = 'Full name is required'
    elif len(name) < 2:
        errors['name'] = 'Name must be at least 2 characters'
    elif not re.match(r'^[A-Za-z\s]+$', name):
        errors['name'] = 'Name must contain only letters and spaces'

    if not email:
        errors['email'] = 'Email is required'
    elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        errors['email'] = 'Enter a valid email address'

    if not contact:
        errors['contact'] = 'Contact number is required'
    elif not re.match(r'^[6-9]\d{9}$', contact):
        errors['contact'] = 'Enter a valid 10-digit Indian mobile number'

    if not gender:
        errors['gender'] = 'Please select your gender'
    elif gender not in ('Male', 'Female', 'Other'):
        errors['gender'] = 'Invalid gender selection'

    if not dob:
        errors['dob'] = 'Date of birth is required'
    else:
        try:
            dob_date = date.fromisoformat(dob)
            today = date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
            if dob_date > today:
                errors['dob'] = 'Date of birth cannot be in the future'
            elif age < 18:
                errors['dob'] = 'You must be at least 18 years old'
            elif age > 120:
                errors['dob'] = 'Enter a valid date of birth'
        except ValueError:
            errors['dob'] = 'Invalid date format'

    if not marital_status:
        errors['marital_status'] = 'Please select marital status'
    elif marital_status not in ('Single', 'Married', 'Divorced', 'Widowed'):
        errors['marital_status'] = 'Invalid marital status'

    if not residential_address:
        errors['residential_address'] = 'Residential address is required'
    elif len(residential_address) < 10:
        errors['residential_address'] = 'Address must be at least 10 characters'

    if not pincode:
        errors['pincode'] = 'Pincode is required'
    elif not re.match(r'^[1-9]\d{5}$', pincode):
        errors['pincode'] = 'Enter a valid 6-digit pincode'

    if not password:
        errors['password'] = 'Password is required'
    elif len(password) < 8:
        errors['password'] = 'Password must be at least 8 characters'
    elif not re.search(r'[A-Z]', password):
        errors['password'] = 'Password must contain at least one uppercase letter'
    elif not re.search(r'[0-9]', password):
        errors['password'] = 'Password must contain at least one number'
    elif not re.search(r'[^A-Za-z0-9]', password):
        errors['password'] = 'Password must contain at least one special character'

    if errors:
        return jsonify({'error': 'Validation failed', 'errors': errors}), 400

    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO users (name, email, contact, gender, dob, marital_status, residential_address, pincode, password)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, name, email, contact""",
            (name, email, contact, gender, dob, marital_status, residential_address, pincode, hashed)
        )
        user = cur.fetchone()
        conn.commit()
        token = create_token(user[0], user[2])
        return jsonify({'message': 'Account created successfully', 'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]}, 'token': token}), 201
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        return jsonify({'error': 'Email already exists', 'errors': {'email': 'This email is already registered'}}), 409
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/login', methods=['POST'])
@rate_limit(max_requests=10, window=60)
def login():
    data = request.get_json()
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, email, contact, password FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        if not user:
            return jsonify({'error': 'No account found with this email'}), 401

        if not user[4] or not bcrypt.checkpw(password.encode('utf-8'), user[4].encode('utf-8')):
            return jsonify({'error': 'Incorrect password'}), 401

        token = create_token(user[0], user[2])
        return jsonify({
            'message': 'Login successful',
            'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]},
            'token': token
        }), 200
    except Exception:
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/google-login', methods=['POST'])
@rate_limit(max_requests=15, window=60)
def google_login():
    data = request.get_json()
    credential = (data.get('credential') or '').strip()

    if not credential:
        return jsonify({'error': 'Google credential is required'}), 400

    google_user = verify_google_token(credential)
    if not google_user or not google_user.get('email'):
        return jsonify({'error': 'Invalid Google credential'}), 401

    name = google_user['name']
    email = google_user['email']

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id, name, email, contact FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user:
            token = create_token(user[0], user[2])
            cur.close()
            return jsonify({
                'message': 'Login successful',
                'user': {'id': user[0], 'name': user[1], 'email': user[2], 'contact': user[3]},
                'token': token
            }), 200
        else:
            cur.execute(
                """INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id, name, email, contact""",
                (name, email)
            )
            new_user = cur.fetchone()
            conn.commit()
            token = create_token(new_user[0], new_user[2])
            cur.close()
            return jsonify({
                'message': 'Account created and logged in',
                'user': {'id': new_user[0], 'name': new_user[1], 'email': new_user[2], 'contact': new_user[3]},
                'token': token
            }), 201

    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        return jsonify({'error': 'Email already exists'}), 409
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/forgot-password', methods=['POST'])
@rate_limit(max_requests=3, window=300)
def forgot_password():
    data = request.get_json()
    email = (data.get('email') or '').strip()
    new_password = (data.get('new_password') or '').strip()

    if not email or not new_password:
        return jsonify({'error': 'Email and new password are required'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if not re.search(r'[A-Z]', new_password):
        return jsonify({'error': 'Password must contain at least one uppercase letter'}), 400
    if not re.search(r'[0-9]', new_password):
        return jsonify({'error': 'Password must contain at least one number'}), 400
    if not re.search(r'[^A-Za-z0-9]', new_password):
        return jsonify({'error': 'Password must contain at least one special character'}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            return jsonify({'error': 'No account found with this email'}), 404

        hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cur.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, user[0]))
        conn.commit()
        cur.close()
        return jsonify({'message': 'Password updated successfully'}), 200
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred. Please try again later.'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/admin/login', methods=['POST'])
@rate_limit(max_requests=5, window=60)
def admin_login():
    data = request.get_json()
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''

    if email != ADMIN_EMAIL:
        return jsonify({'error': 'Invalid admin credentials'}), 401

    if not bcrypt.checkpw(password.encode('utf-8'), ADMIN_PASSWORD_HASH.encode('utf-8')):
        return jsonify({'error': 'Invalid admin credentials'}), 401

    token = create_token(0, email)
    return jsonify({'token': token, 'message': 'Admin login successful'}), 200


@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    return jsonify({'message': 'Logged out'}), 200


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get('x-admin-token', '')
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Unauthorized'}), 401
        if payload.get('user_id') != 0 or payload.get('email') != ADMIN_EMAIL:
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return wrapper


@app.route('/api/stats', methods=['GET'])
@require_admin
def get_stats():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bookings")
        total_bookings = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM payments")
        total_payments = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed'")
        total_revenue = cur.fetchone()[0]
        cur.close()
        return jsonify({
            'totalUsers': total_users,
            'totalBookings': total_bookings,
            'totalPayments': total_payments,
            'totalRevenue': float(total_revenue)
        })
    except Exception:
        return jsonify({'totalUsers': 0, 'totalBookings': 0, 'totalPayments': 0, 'totalRevenue': 0})
    finally:
        if conn:
            conn.close()


@app.route('/api/recent', methods=['GET'])
@require_admin
def get_recent():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT b.*, u.name as user_name FROM bookings b
                       LEFT JOIN users u ON b.user_id = u.id
                       ORDER BY b.created_at DESC LIMIT 5""")
        bookings = cur.fetchall()
        cur.execute("""SELECT p.*, u.name as user_name FROM payments p
                       LEFT JOIN users u ON p.user_id = u.id
                       ORDER BY p.created_at DESC LIMIT 5""")
        payments = cur.fetchall()
        cur.execute("SELECT * FROM users ORDER BY id DESC LIMIT 5")
        users = cur.fetchall()
        cur.close()
        return jsonify({
            'bookings': [dict(b) for b in bookings],
            'payments': [dict(p) for p in payments],
            'users': [dict(u) for u in users]
        })
    except Exception:
        return jsonify({'bookings': [], 'payments': [], 'users': []})
    finally:
        if conn:
            conn.close()


@app.route('/api/users', methods=['GET'])
@require_admin
def get_users():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email, contact as phone, created_at FROM users ORDER BY id DESC")
        users = cur.fetchall()
        cur.close()
        return jsonify([dict(u) for u in users])
    except Exception:
        return jsonify([])
    finally:
        if conn:
            conn.close()


@app.route('/api/users/<int:user_id>', methods=['GET'])
@require_admin
def get_user(user_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email, contact as phone, gender, dob, marital_status, residential_address, pincode, created_at FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        cur.close()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        return jsonify(dict(user))
    except Exception:
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@require_admin
def delete_user(user_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        cur.close()
        return jsonify({'message': 'User deleted'})
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings', methods=['GET'])
@require_admin
def admin_get_bookings():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT b.*, u.name as user_name, u.email as user_email
                       FROM bookings b LEFT JOIN users u ON b.user_id = u.id
                       ORDER BY b.created_at DESC""")
        bookings = cur.fetchall()
        cur.close()
        return jsonify([dict(b) for b in bookings])
    except Exception:
        return jsonify([])
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>', methods=['GET'])
@require_admin
def admin_get_booking(booking_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT b.*, u.name as user_name, u.email as user_email
                       FROM bookings b LEFT JOIN users u ON b.user_id = u.id
                       WHERE b.id = %s""", (booking_id,))
        booking = cur.fetchone()
        cur.close()
        if not booking:
            return jsonify({'error': 'Booking not found'}), 404
        return jsonify(dict(booking))
    except Exception:
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>', methods=['PUT'])
@require_admin
def admin_update_booking(booking_id):
    data = request.get_json()
    status = (data.get('status') or '').strip().lower()
    valid_statuses = ['pending', 'confirmed', 'completed', 'cancelled']
    if status not in valid_statuses:
        return jsonify({'error': 'Invalid status'}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))
        conn.commit()
        cur.close()
        return jsonify({'message': 'Booking updated'})
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/<int:booking_id>', methods=['DELETE'])
@require_admin
def admin_delete_booking(booking_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
        conn.commit()
        cur.close()
        return jsonify({'message': 'Booking deleted'})
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/payments', methods=['GET'])
@require_admin
def admin_get_payments():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT p.*, u.name as user_name, u.email as user_email
                       FROM payments p LEFT JOIN users u ON p.user_id = u.id
                       ORDER BY p.created_at DESC""")
        payments = cur.fetchall()
        cur.close()
        return jsonify([dict(p) for p in payments])
    except Exception:
        return jsonify([])
    finally:
        if conn:
            conn.close()


@app.route('/api/payments/<int:payment_id>', methods=['GET'])
@require_admin
def admin_get_payment(payment_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT p.*, u.name as user_name, u.email as user_email
                       FROM payments p LEFT JOIN users u ON p.user_id = u.id
                       WHERE p.id = %s""", (payment_id,))
        payment = cur.fetchone()
        cur.close()
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        return jsonify(dict(payment))
    except Exception:
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings/user', methods=['GET'])
def get_user_bookings():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    payload = verify_token(token)
    if not payload:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = payload.get('user_id')
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM bookings WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        bookings = cur.fetchall()
        cur.close()
        return jsonify([dict(b) for b in bookings])
    except Exception:
        return jsonify([])
    finally:
        if conn:
            conn.close()


@app.route('/api/bookings', methods=['POST'])
def create_booking():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    payload = verify_token(token)
    if not payload:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = payload.get('user_id')
    data = request.get_json()
    booking_type = (data.get('type') or '').strip()
    source = (data.get('from') or '').strip()
    destination = (data.get('to') or '').strip()
    travel_date = (data.get('date') or '').strip()
    amount = data.get('amount', 0)

    if not booking_type or not source or not destination:
        return jsonify({'error': 'Type, source, and destination are required'}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """INSERT INTO bookings (user_id, booking_type, source, destination, travel_date, amount, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING *""",
            (user_id, booking_type, source, destination, travel_date, amount)
        )
        booking = cur.fetchone()
        conn.commit()
        cur.close()
        return jsonify(dict(booking)), 201
    except Exception:
        if conn:
            conn.rollback()
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    app.run(port=int(os.getenv('PORT', 5000)))
