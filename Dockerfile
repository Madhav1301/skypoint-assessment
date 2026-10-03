FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ config/
COPY src/ src/
COPY tests/ tests/
COPY pytest.ini .
# README.md is part of the test surface: the suite executes its SQL examples.
COPY README.md .

CMD ["python", "-m", "pipeline.run"]
