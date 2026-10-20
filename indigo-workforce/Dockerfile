FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INDIGO_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]==3.2.13"
COPY indigo ./indigo
COPY sample_data ./sample_data
RUN useradd --system --uid 1001 indigo && mkdir -p /app/var && chown indigo /app/var
USER indigo
EXPOSE 8000
CMD ["sh", "-c", "python -m indigo.cli migrate && uvicorn --factory indigo.web.app:create_app --host 0.0.0.0 --port 8000 --proxy-headers --workers 2"]
