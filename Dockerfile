FROM python:3.11-slim

WORKDIR /app

COPY requirements.lock.txt .
RUN pip install --no-cache-dir --requirement requirements.lock.txt

COPY . .

EXPOSE 8091

ENV RUNTIME_HOST=0.0.0.0
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('RUNTIME_PORT', '8091') + '/health', timeout=3)"

CMD ["python", "runtime_multi.py"]
