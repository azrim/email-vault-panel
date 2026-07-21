FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8787 \
    DEFAULT_DOMAIN=sukiliar.pro

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8787
VOLUME ["/data"]

CMD ["uvicorn", "email_vault_panel.main:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers"]
