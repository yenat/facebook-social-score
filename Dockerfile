# Use slim Python base image
FROM python:3.9-slim

# Set environment variables to avoid interactive prompts and save space
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

# Install system dependencies in one layer, then clean cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc python3-dev \
    libnss3 libnspr4 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
 && rm -rf /var/lib/apt/lists/*

# Install Playwright and Chromium in one step
RUN pip install --no-cache-dir playwright \
 && playwright install --with-deps chromium

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port
EXPOSE 7070

# Run app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7070"]
