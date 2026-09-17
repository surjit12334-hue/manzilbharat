require('dotenv').config();
const express = require('express');
const { Pool } = require('pg');
const cors = require('cors');
const path = require('path');
const crypto = require('crypto');
const bcrypt = require('bcrypt');

const app = express();
const PORT = process.env.PORT || 3000;
const ADMIN_SESSIONS = new Map();

const pool = new Pool({
  host: process.env.DB_HOST,
  port: process.env.DB_PORT,
  database: process.env.DB_NAME,
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
});

pool.connect((err) => {
  if (err) {
    console.error('Database connection error:', err.stack);
    return;
  }
  console.log('Connected to PostgreSQL database: manzil_bharat');
});

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname)));

// ======== USER SIGNUP ========
app.post('/api/signup', async (req, res) => {
  try {
    const { name, email, contact, gender, dob, marital_status, residential_address, pincode, password } = req.body;
    if (!name || !email || !contact || !gender || !dob || !marital_status || !residential_address || !pincode || !password) {
      return res.status(400).json({ error: 'All fields are required' });
    }
    const hashedPassword = await bcrypt.hash(password, 10);
    const result = await pool.query(
      `INSERT INTO users (name, email, contact, gender, dob, marital_status, residential_address, pincode, password)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING email, contact, name`,
      [name, email, contact, gender, dob, marital_status, residential_address, pincode, hashedPassword]
    );
    res.status(201).json({ message: 'Account created successfully', user: result.rows[0] });
  } catch (err) {
    if (err.code === '23505') {
      return res.status(409).json({ error: 'Email or contact already exists' });
    }
    res.status(500).json({ error: err.message });
  }
});

// ======== ADMIN AUTH ========
function requireAdmin(req, res, next) {
  const token = req.headers['x-admin-token'];
  if (!token || !ADMIN_SESSIONS.has(token)) {
    return res.status(401).json({ error: 'Unauthorized. Admin login required.' });
  }
  req.admin = ADMIN_SESSIONS.get(token);
  next();
}

app.post('/api/admin/login', async (req, res) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });
    const result = await pool.query('SELECT * FROM admins WHERE email = $1', [email]);
    if (result.rows.length === 0) return res.status(401).json({ error: 'Invalid email or password' });
    const admin = result.rows[0];
    const validPassword = await bcrypt.compare(password, admin.password);
    if (!validPassword) return res.status(401).json({ error: 'Invalid email or password' });
    const token = crypto.randomBytes(32).toString('hex');
    ADMIN_SESSIONS.set(token, { id: admin.id, email: admin.email, name: admin.name });
    res.json({ token, admin: { id: admin.id, email: admin.email, name: admin.name } });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/admin/logout', (req, res) => {
  const token = req.headers['x-admin-token'];
  if (token) ADMIN_SESSIONS.delete(token);
  res.json({ message: 'Logged out' });
});

app.get('/api/admin/me', requireAdmin, (req, res) => {
  res.json({ admin: req.admin });
});

// ======== STATS (protected) ========
app.get('/api/stats', requireAdmin, async (req, res) => {
  try {
    const [users, bookings, payments, revenue] = await Promise.all([
      pool.query('SELECT COUNT(*) FROM users'),
      pool.query('SELECT COUNT(*) FROM bookings'),
      pool.query('SELECT COUNT(*) FROM payments'),
      pool.query("SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE status = 'completed'"),
    ]);
    res.json({
      totalUsers: parseInt(users.rows[0].count),
      totalBookings: parseInt(bookings.rows[0].count),
      totalPayments: parseInt(payments.rows[0].count),
      totalRevenue: parseFloat(revenue.rows[0].total),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ======== USERS (protected) ========
app.get('/api/users', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query('SELECT * FROM users ORDER BY created_at DESC');
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/users/:id', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query('SELECT * FROM users WHERE id = $1', [req.params.id]);
    if (result.rows.length === 0) return res.status(404).json({ error: 'User not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/users', requireAdmin, async (req, res) => {
  try {
    const { firebase_uid, email, name, phone } = req.body;
    const result = await pool.query(
      'INSERT INTO users (firebase_uid, email, name, phone) VALUES ($1, $2, $3, $4) RETURNING *',
      [firebase_uid, email, name, phone]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/users/:id', requireAdmin, async (req, res) => {
  try {
    const { name, email, phone } = req.body;
    const result = await pool.query(
      'UPDATE users SET name = $1, email = $2, phone = $3, updated_at = NOW() WHERE id = $4 RETURNING *',
      [name, email, phone, req.params.id]
    );
    if (result.rows.length === 0) return res.status(404).json({ error: 'User not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/users/:id', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query('DELETE FROM users WHERE id = $1 RETURNING *', [req.params.id]);
    if (result.rows.length === 0) return res.status(404).json({ error: 'User not found' });
    res.json({ message: 'User deleted', user: result.rows[0] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ======== BOOKINGS (protected) ========
app.get('/api/bookings', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT b.*, u.name AS user_name, u.email AS user_email
      FROM bookings b
      LEFT JOIN users u ON b.user_id = u.id
      ORDER BY b.created_at DESC
    `);
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/bookings/:id', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT b.*, u.name AS user_name, u.email AS user_email
      FROM bookings b
      LEFT JOIN users u ON b.user_id = u.id
      WHERE b.id = $1
    `, [req.params.id]);
    if (result.rows.length === 0) return res.status(404).json({ error: 'Booking not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/bookings', requireAdmin, async (req, res) => {
  try {
    const { user_id, booking_type, provider, source, destination, travel_date, amount, status, metadata } = req.body;
    const result = await pool.query(
      `INSERT INTO bookings (user_id, booking_type, provider, source, destination, travel_date, amount, status, metadata)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *`,
      [user_id, booking_type, provider, source, destination, travel_date, amount, status || 'pending', metadata ? JSON.stringify(metadata) : null]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/bookings/:id', requireAdmin, async (req, res) => {
  try {
    const { status } = req.body;
    const result = await pool.query(
      'UPDATE bookings SET status = $1, updated_at = NOW() WHERE id = $2 RETURNING *',
      [status, req.params.id]
    );
    if (result.rows.length === 0) return res.status(404).json({ error: 'Booking not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/bookings/:id', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query('DELETE FROM bookings WHERE id = $1 RETURNING *', [req.params.id]);
    if (result.rows.length === 0) return res.status(404).json({ error: 'Booking not found' });
    res.json({ message: 'Booking deleted', booking: result.rows[0] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ======== PAYMENTS (protected) ========
app.get('/api/payments', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT p.*, u.name AS user_name, u.email AS user_email, b.booking_type
      FROM payments p
      LEFT JOIN users u ON p.user_id = u.id
      LEFT JOIN bookings b ON p.booking_id = b.id
      ORDER BY p.created_at DESC
    `);
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/payments/:id', requireAdmin, async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT p.*, u.name AS user_name, u.email AS user_email, b.booking_type
      FROM payments p
      LEFT JOIN users u ON p.user_id = u.id
      LEFT JOIN bookings b ON p.booking_id = b.id
      WHERE p.id = $1
    `, [req.params.id]);
    if (result.rows.length === 0) return res.status(404).json({ error: 'Payment not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/payments/:id', requireAdmin, async (req, res) => {
  try {
    const { status } = req.body;
    const result = await pool.query(
      'UPDATE payments SET status = $1 WHERE id = $2 RETURNING *',
      [status, req.params.id]
    );
    if (result.rows.length === 0) return res.status(404).json({ error: 'Payment not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ======== RECENT ACTIVITY (protected) ========
app.get('/api/recent', requireAdmin, async (req, res) => {
  try {
    const [recentUsers, recentBookings, recentPayments] = await Promise.all([
      pool.query('SELECT id, name, email, created_at FROM users ORDER BY created_at DESC LIMIT 5'),
      pool.query('SELECT id, booking_type, source, destination, status, created_at FROM bookings ORDER BY created_at DESC LIMIT 5'),
      pool.query('SELECT id, amount, currency, status, created_at FROM payments ORDER BY created_at DESC LIMIT 5'),
    ]);
    res.json({
      users: recentUsers.rows,
      bookings: recentBookings.rows,
      payments: recentPayments.rows,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
  console.log(`Admin panel: http://localhost:${PORT}/admin.html`);
});
