FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml alembic.ini requirements.lock ./
COPY alembic ./alembic
COPY dirima ./dirima
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps .
ENV PYTHONUNBUFFERED=1
CMD ["dirima", "serve"]
