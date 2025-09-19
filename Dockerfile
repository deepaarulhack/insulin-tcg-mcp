# Use Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (git, gcc)
RUN apt-get update && apt-get install -y gcc git && rm -rf /var/lib/apt/lists/*

# Copy dependency file and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Clone insulin pump repo (for code context in testcase.generate)
RUN git clone https://github.com/lmarza/Personal_Insulin_Pump-Integrated_System.git /app/insulin_repo

# Copy application code
COPY . .

# Expose FastAPI on port 8080
EXPOSE 8080

# Run with uvicorn
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]

