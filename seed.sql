-- Manzil Bharat - Seed Data for demos / presentations
-- All seeded users have password: Test@1234  (bcrypt hash below)
-- Idempotent: safe to run multiple times (ON CONFLICT DO NOTHING).

INSERT INTO users (name, email, contact, gender, dob, marital_status, residential_address, pincode, password, created_at) VALUES
    ('Sameer Verma',   'sameer.verma@example.com',   '9876543210', 'Male',     '1990-05-14', 'Single',   'Andheri West, Mumbai',            '400053', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '45 days'),
    ('Priya Sharma',   'priya.sharma@example.com',   '9123456780', 'Female',   '1985-11-02', 'Married',  'Connaught Place, New Delhi',        '110001', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '38 days'),
    ('Rahul Mehta',    'rahul.mehta@example.com',    '9812345670', 'Male',     '1999-02-20', 'Single',   'Indiranagar, Bengaluru',            '560038', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '30 days'),
    ('Neha Gupta',     'neha.gupta@example.com',     '9765432109', 'Female',   '1993-07-08', 'Married',  'Malviya Nagar, Jaipur',             '302017', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '21 days'),
    ('Amit Patel',     'amit.patel@example.com',     '9654321098', 'Male',     '1988-12-25', 'Divorced', 'Navrangpura, Ahmedabad',            '380009', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '12 days')
ON CONFLICT (email) DO NOTHING;

INSERT INTO bookings (user_id, booking_type, source, destination, travel_date, amount, status, created_at) VALUES
    (3, 'cab',    'Bengaluru', 'Goa',          '2026-10-08', 2450.00, 'confirmed', CURRENT_TIMESTAMP - INTERVAL '10 days'),
    (2, 'train',  'Mumbai',    'Delhi',        '2026-09-28', 1850.00, 'confirmed', CURRENT_TIMESTAMP - INTERVAL '8 days'),
    (4, 'flight', 'Jaipur',    'Bengaluru',    '2026-11-02', 4200.00, 'pending',   CURRENT_TIMESTAMP - INTERVAL '6 days'),
    (5, 'bus',    'Ahmedabad', 'Udaipur',      '2026-10-20',  900.00, 'completed', CURRENT_TIMESTAMP - INTERVAL '4 days'),
    (3, 'hotel',  'Goa',       'Goa',          '2026-10-09', 5200.00, 'confirmed', CURRENT_TIMESTAMP - INTERVAL '3 days')
ON CONFLICT DO NOTHING;

INSERT INTO payments (user_id, booking_id, amount, currency, razorpay_payment_id, razorpay_order_id, booking_type, status, created_at) VALUES
    (3, 1, 2450.00, 'INR', 'pay_PX8k2mZt1Qab', 'order_N3dR7xLp2V', 'cab',    'completed', CURRENT_TIMESTAMP - INTERVAL '10 days'),
    (2, 2, 1850.00, 'INR', 'pay_PX7j1nYt9Rcd', 'order_N3cQ6wKm1U', 'train',  'completed', CURRENT_TIMESTAMP - INTERVAL '8 days'),
    (4, 3, 4200.00, 'INR', 'pay_PX6i0mXt8Sef', 'order_N3bP5vLj0T', 'flight', 'completed', CURRENT_TIMESTAMP - INTERVAL '6 days'),
    (5, 4,  900.00, 'INR', 'pay_PX5h9lWs7Tgh', 'order_N3aO4uKi9S', 'bus',    'completed', CURRENT_TIMESTAMP - INTERVAL '4 days'),
    (3, 5, 5200.00, 'INR', 'pay_PX4g8kVr6Uij', 'order_N3zN3tHj8R', 'hotel',  'confirmed', CURRENT_TIMESTAMP - INTERVAL '3 days')
ON CONFLICT DO NOTHING;