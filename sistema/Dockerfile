FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
RUN groupadd --gid 10001 masiscam && useradd --uid 10001 --gid masiscam --no-create-home masiscam
COPY --chown=masiscam:masiscam . .
RUN mkdir -p /app/media /app/staticfiles && chown -R masiscam:masiscam /app/media /app/staticfiles
USER masiscam
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=40s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=5)"
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
