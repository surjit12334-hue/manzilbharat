// Same-origin: the Flask server delivers both the pages and the API, so a
// relative base works on localhost and on any deployed host. An absolute
// http://localhost URL here would break every API call in production.
window.API_BASE = '';

// Google OAuth client IDs are public (they travel in every sign-in request),
// so this is safe to commit. It MUST be the same id the server verifies
// tokens against (GOOGLE_CLIENT_ID in .env / the host's environment).
// When the Flask app is serving this file, /config.js is generated from that
// environment variable, so the two cannot drift.
window.GOOGLE_CLIENT_ID =
  '406958869754-9lr0216bormg22mgnq2ccnk7r5t09fek.apps.googleusercontent.com';
