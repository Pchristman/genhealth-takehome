FROM python:3.13-slim
RUN pip install --no-cache-dir uv
WORKDIR /srv
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY backend/scripts ./scripts
RUN mkdir /data
ENV DATABASE_URL=sqlite:////data/app.db
EXPOSE 8000
CMD ["sh", "-c", "exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
