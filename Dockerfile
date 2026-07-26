FROM python:3.11-slim

WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=120
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.lock.txt .
RUN pip install --no-cache-dir --timeout 120 --requirement requirements.lock.txt

COPY . .

EXPOSE 8091

ENV RUNTIME_HOST=0.0.0.0
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('RUNTIME_PORT', '8091') + '/health', timeout=3)"

CMD ["python", "runtime_multi.py"]
