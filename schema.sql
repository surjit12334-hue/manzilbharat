-- Manzil Bharat - Database Schema
-- For fresh PostgreSQL setups only. On an existing DB, run the ALTER in
-- the "Existing DB fixes" section instead.

CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,
    email               VARCHAR(255) NOT NULL UNIQUE,
    contact             VARCHAR(15),
    name                VARCHAR(255) NOT NULL,
    gender              VARCHAR(20),
    dob                 DATE,
    marital_status      VARCHAR(20),
    residential_address VARCHAR(255),
    pincode             VARCHAR(10),
    password            VARCHAR(255),
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookings (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
    booking_type VARCHAR(50) NOT NULL,
    source       VARCHAR(255),
    destination  VARCHAR(255),
    travel_date  DATE,
    amount       NUMERIC(10,2) DEFAULT 0,
    status       VARCHAR(20) DEFAULT 'pending',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER REFERENCES users(id) ON DELETE CASCADE,
    booking_id         INTEGER REFERENCES bookings(id),
    amount             NUMERIC(10,2) NOT NULL,
    currency           VARCHAR(10) DEFAULT 'INR',
    razorpay_payment_id VARCHAR(255),
    razorpay_order_id  VARCHAR(255),
    booking_type       VARCHAR(50),
    status             VARCHAR(20) DEFAULT 'pending',
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Newsletter signups. No user_id: subscribers are often signed out, and
-- ON CONFLICT (email) makes a double-submit idempotent.
CREATE TABLE IF NOT EXISTS subscribers (
    id         SERIAL PRIMARY KEY,
    email      VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Contact-form messages, shown in the admin panel's Messages tab.
CREATE TABLE IF NOT EXISTS messages (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(255) NOT NULL,
    email      VARCHAR(255) NOT NULL,
    subject    VARCHAR(255) NOT NULL,
    body       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Existing DB fixes (run on the CURRENT live database only if migrating)
-- ============================================================================
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;

-- Required for the admin panel's "delete user" button to work: without these
-- the delete aborts on bookings_user_id_fkey / payments_user_id_fkey.
-- ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_user_id_fkey;
-- ALTER TABLE bookings ADD CONSTRAINT bookings_user_id_fkey
--     FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
-- ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_user_id_fkey;
-- ALTER TABLE payments ADD CONSTRAINT payments_user_id_fkey
--     FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

-- travel_date was VARCHAR(20); the booking API needs a real DATE (sorting,
-- validation, "no past dates"). Run AS THE TABLE OWNER (postgres):
--   sudo -u postgres psql -d manzil_bharat -c \
--     "ALTER TABLE bookings ALTER COLUMN travel_date TYPE DATE
--      USING NULLIF(travel_date,'')::date;"
-- Values must all be YYYY-MM-DD before running this; check with:
--   SELECT travel_date FROM bookings
--      WHERE travel_date IS NOT NULL AND travel_date !~ '^\d{4}-\d{2}-\d{2}$';
