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
    user_id      INTEGER REFERENCES users(id),
    booking_type VARCHAR(50) NOT NULL,
    source       VARCHAR(255),
    destination  VARCHAR(255),
    travel_date  VARCHAR(20),
    amount       NUMERIC(10,2) DEFAULT 0,
    status       VARCHAR(20) DEFAULT 'pending',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER REFERENCES users(id),
    booking_id         INTEGER REFERENCES bookings(id),
    amount             NUMERIC(10,2) NOT NULL,
    currency           VARCHAR(10) DEFAULT 'INR',
    razorpay_payment_id VARCHAR(255),
    razorpay_order_id  VARCHAR(255),
    booking_type       VARCHAR(50),
    status             VARCHAR(20) DEFAULT 'pending',
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Existing DB fixes (run on the CURRENT live database only if migrating)
-- ============================================================================
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;