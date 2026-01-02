# syntax=docker/dockerfile:1
FROM python:3.12-slim



# ---- System deps for headless Chromium + sane runtime ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    libxrender1 \
    libxshmfence1 \
    libxss1 \
    libxtst6 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# ---- Python deps ----
# (curl_cffi uses native wheels for most platforms; no build chain needed in many cases.)
RUN python -m pip install --no-cache-dir -U pip setuptools wheel \
  && python -m pip install --no-cache-dir \
      "nodriver>=0.48.0" \
      pyotp \
      curl_cffi

# Optional: nodriver (and many headless setups) can behave better with these.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMIUM_BIN=/usr/bin/chromium

WORKDIR /app
RUN useradd -m -u 1000 appuser \
  && mkdir -p /home/appuser/.cache /home/appuser/.config \
  && chown -R appuser:appuser /home/appuser /app
USER appuser

# Copy your script(s) in; or mount at runtime instead.
# COPY . /app
# Example default command:
# CMD ["python", "your_script.py"]

COPY sessions.py /app/sessions.py
# Stall the container for debug purposes
# CMD ["tail", "-f", "/dev/null"]

ENTRYPOINT ["python", "/app/sessions.py"]

# If you want a safer default that proves Chromium exists:
# CMD ["python", "-c", "import shutil; print('chromium:', shutil.which('chromium'))"]
