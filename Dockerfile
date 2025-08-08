FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc python3-dev libnss3 libnspr4 && \
    rm -rf /var/lib/apt/lists/*

# Install Playwright
RUN pip install playwright && \
    playwright install chromium && \
    playwright install-deps

WORKDIR /app
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7070"]