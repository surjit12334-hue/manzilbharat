from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import bcrypt
import re
import os
from dotenv import load_dotenv
from datetime import date

load_dotenv()

app = Flask(__name__)
CORS(app)

def get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

@app.route('/api/signup', methods=['POST'])
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

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO users (name, email, contact, gender, dob, marital_status, residential_address, pincode, password)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING email, contact, name""",
            (name, email, contact, gender, dob, marital_status, residential_address, pincode, hashed)
        )
        user = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'message': 'Account created successfully', 'user': {'name': user[2], 'email': user[0], 'contact': user[1]}}), 201
    except psycopg2.IntegrityError as e:
        detail = str(e)
        if 'email' in detail:
            return jsonify({'error': 'Email already exists', 'errors': {'email': 'This email is already registered'}}), 409
        elif 'contact' in detail or 'pkey' in detail:
            return jsonify({'error': 'Contact already exists', 'errors': {'contact': 'This contact number is already registered'}}), 409
        return jsonify({'error': 'Email or contact already exists'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True)
