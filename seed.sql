-- Manzil Bharat - Seed Data for demos / presentations
-- All seeded users have password: Test@1234  (bcrypt hash below)
-- Idempotent: safe to run multiple times. Users dedupe on the UNIQUE email;
-- bookings and payments have no unique constraint to conflict on (their only
-- unique index is the auto-generated id), so they are guarded with WHERE NOT
-- EXISTS against their natural keys instead of ON CONFLICT.
-- Rows reference users by email, not by id, so they stay correct even if the
-- users table already had other rows in it.

INSERT INTO users (name, email, contact, gender, dob, marital_status, residential_address, pincode, password, created_at) VALUES
    ('Sameer Verma',   'sameer.verma@example.com',   '9876543210', 'Male',     '1990-05-14', 'Single',   'Andheri West, Mumbai',            '400053', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '45 days'),
    ('Priya Sharma',   'priya.sharma@example.com',   '9123456780', 'Female',   '1985-11-02', 'Married',  'Connaught Place, New Delhi',        '110001', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '38 days'),
    ('Rahul Mehta',    'rahul.mehta@example.com',    '9812345670', 'Male',     '1999-02-20', 'Single',   'Indiranagar, Bengaluru',            '560038', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '30 days'),
    ('Neha Gupta',     'neha.gupta@example.com',     '9765432109', 'Female',   '1993-07-08', 'Married',  'Malviya Nagar, Jaipur',             '302017', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '21 days'),
    ('Amit Patel',     'amit.patel@example.com',     '9654321098', 'Male',     '1988-12-25', 'Divorced', 'Navrangpura, Ahmedabad',            '380009', '$2b$12$LDORW4qFwpmujMBL8MB5I.xs7Yn.k4cXh9WFcdbfaueyuXEkmxqKC', CURRENT_TIMESTAMP - INTERVAL '12 days')
ON CONFLICT (email) DO NOTHING;

INSERT INTO bookings (user_id, booking_type, source, destination, travel_date, amount, status, created_at)
SELECT u.id, v.booking_type, v.source, v.destination, v.travel_date, v.amount, v.status, v.created_at
FROM (VALUES
    ('rahul.mehta@example.com',    'cab',    'Bengaluru', 'Goa',       '2026-10-08', 2450.00, 'confirmed', CURRENT_TIMESTAMP - INTERVAL '10 days'),
    ('priya.sharma@example.com',    'train',  'Mumbai',    'Delhi',     '2026-09-28', 1850.00, 'confirmed', CURRENT_TIMESTAMP - INTERVAL '8 days'),
    ('neha.gupta@example.com',      'flight', 'Jaipur',    'Bengaluru', '2026-11-02', 4200.00, 'pending',   CURRENT_TIMESTAMP - INTERVAL '6 days'),
    ('amit.patel@example.com',      'bus',    'Ahmedabad', 'Udaipur',   '2026-10-20',  900.00, 'completed', CURRENT_TIMESTAMP - INTERVAL '4 days'),
    ('rahul.mehta@example.com',     'hotel',  'Goa',       'Goa',       '2026-10-09', 5200.00, 'confirmed', CURRENT_TIMESTAMP - INTERVAL '3 days')
) AS v(user_email, booking_type, source, destination, travel_date, amount, status, created_at)
JOIN users u ON u.email = v.user_email
WHERE NOT EXISTS (
    SELECT 1 FROM bookings b
    WHERE b.user_id = u.id
      AND b.booking_type = v.booking_type
      AND b.source = v.source
      AND b.destination = v.destination
      AND b.travel_date = v.travel_date
);

INSERT INTO payments (user_id, booking_id, amount, currency, razorpay_payment_id, razorpay_order_id, booking_type, status, created_at)
SELECT u.id, b.id, v.amount, 'INR', v.razorpay_payment_id, v.razorpay_order_id, v.booking_type, v.status, v.created_at
FROM (VALUES
    ('rahul.mehta@example.com',  'Bengaluru', 'Goa',       '2026-10-08', 2450.00, 'pay_PX8k2mZt1Qab', 'order_N3dR7xLp2V', 'cab',    'completed', CURRENT_TIMESTAMP - INTERVAL '10 days'),
    ('priya.sharma@example.com',  'Mumbai',    'Delhi',     '2026-09-28', 1850.00, 'pay_PX7j1nYt9Rcd', 'order_N3cQ6wKm1U', 'train',  'completed', CURRENT_TIMESTAMP - INTERVAL '8 days'),
    ('neha.gupta@example.com',    'Jaipur',    'Bengaluru', '2026-11-02', 4200.00, 'pay_PX6i0mXt8Sef', 'order_N3bP5vLj0T', 'flight', 'completed', CURRENT_TIMESTAMP - INTERVAL '6 days'),
    ('amit.patel@example.com',    'Ahmedabad', 'Udaipur',   '2026-10-20',  900.00, 'pay_PX5h9lWs7Tgh', 'order_N3aO4uKi9S', 'bus',    'completed', CURRENT_TIMESTAMP - INTERVAL '4 days'),
    ('rahul.mehta@example.com',   'Goa',       'Goa',       '2026-10-09', 5200.00, 'pay_PX4g8kVr6Uij', 'order_N3zN3tHj8R', 'hotel',  'confirmed', CURRENT_TIMESTAMP - INTERVAL '3 days')
) AS v(user_email, source, destination, travel_date, amount, razorpay_payment_id, razorpay_order_id, booking_type, status, created_at)
JOIN users u ON u.email = v.user_email
JOIN bookings b ON b.user_id = u.id
              AND b.source = v.source
              AND b.destination = v.destination
              AND b.travel_date = v.travel_date
              AND b.booking_type = v.booking_type
WHERE NOT EXISTS (
    SELECT 1 FROM payments p WHERE p.razorpay_payment_id = v.razorpay_payment_id
);
