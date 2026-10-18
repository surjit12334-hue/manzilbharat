# Container image for Manzil Bharat. Optional (Render can build straight
# from the repo) but useful: `docker build -t manzil . && docker run -p
# 5000:5000 manzil` gives anyone the exact production setup.
FROM python:3.12-slim

# psycopg2-binary ships wheels, so no build tools needed here.
ENV PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The app serves the static frontend from the repo root, so the whole
# tree is needed - not just backend/.
COPY . .

EXPOSE 5000

# .env is intentionally NOT copied by the image (it is in .dockerignore
# and .gitignore): production secrets come from the host's environment.
CMD ["bash", "start-prod.sh"]
