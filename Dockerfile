FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir -e '.[runtime,dev]'

COPY migrations ./migrations
COPY tests ./tests
COPY scripts ./scripts

CMD ["python", "-m", "app.cli", "--help"]
