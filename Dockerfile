FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY requirements.lock ./
RUN pip install -r requirements.lock

# Editable: compose bind-mounts the host tree over /app, so a baked-in copy is
# not what ends up running.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-deps -e .

COPY alembic.ini ./
COPY alembic ./alembic
# The entrypoint seeds, so the image carries the data it seeds from; compose bind-mounts
# the tree over /app, which would otherwise be the only reason the seed finds them.
COPY data ./data
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
