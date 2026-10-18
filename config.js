// Same-origin: the Flask server delivers both the pages and the API, so a
// relative base works on localhost and on any deployed host. An absolute
// http://localhost URL here would break every API call in production.
window.API_BASE = '';
window.GEMINI_API_KEY = 'YOUR_GEMINI_API_KEY';
window.GOOGLE_CLIENT_ID = 'YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com';