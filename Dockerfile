# -----------------------------
# Stage 1: Base image
# -----------------------------
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (optional, for e.g. psycopg2, lxml, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
 && rm -rf /var/lib/apt/lists/*

# -----------------------------
# Stage 2: Install dependencies
# -----------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -----------------------------
# Stage 3: Copy application code
# -----------------------------
COPY . .

# -----------------------------
# Stage 4: Expose port & run
# -----------------------------
EXPOSE 8080

# Run FastAPI with Uvicorn
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
