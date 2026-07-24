FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations

# Package indexes and desktop proxies occasionally terminate a JSON response
# mid-stream. Keep image creation bounded and retry the whole resolver request
# so a transient index response does not make the one-click package unusable.
RUN for attempt in 1 2 3; do \
      pip install --no-cache-dir --timeout 60 --retries 5 . && exit 0; \
      echo "pip install attempt ${attempt} failed; retrying"; \
      sleep 2; \
    done; \
    exit 1

EXPOSE 8777

CMD ["uvicorn", "memcore.main:app", "--host", "0.0.0.0", "--port", "8777"]
