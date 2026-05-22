# build v4 - 2026-05-22
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV GEMINI_MODEL=gemini-2.5-flash

RUN playwright install chromium --with-deps

COPY . .

RUN mkdir -p schemas

CMD ["sh", "-c", "uvicorn dashboard:wrapped_app --host 0.0.0.0 --port ${PORT:-7000} --log-level info"]
