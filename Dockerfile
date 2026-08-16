# Use an official lightweight Python runtime
FROM python:3.10-slim

# Set working directory inside the container
WORKDIR /app

# Install system dependencies including ffmpeg for media transformation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Set default Cloud Run port
ENV PORT=8080

# Expose container port
EXPOSE 8080

# Launch FastAPI app with Uvicorn
CMD exec uvicorn src.api:app --host 0.0.0.0 --port ${PORT}
