# Signup app image. Used by Railway (or any container host) instead of a
# buildpack, because the app shells out to `ssh` to reach the jump host and
# the OpenSSH client must be present in the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Seed pool for DRY_RUN=1 only; unused when a real jump host is configured.
COPY dryrun ./dryrun

# Run unprivileged. ssh needs a passwd entry and a home directory.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
