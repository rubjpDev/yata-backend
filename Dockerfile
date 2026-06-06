FROM python:3.14-slim

# Install system dependencies for asyncpg compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir "poetry==2.4.1"

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml poetry.lock ./

# Configure Poetry: no virtualenv inside container, no interaction
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root

# Copy application source
COPY app ./app

# Run the API with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
