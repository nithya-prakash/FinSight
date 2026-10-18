FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml constraints.txt ./
COPY src ./src
# constraints.txt pins the exact versions verified by the test suite (regenerate with `make lock`)
RUN pip install -c constraints.txt -e ".[dev,warehouse,ml]"
COPY packages ./packages
COPY tests ./tests
COPY docs ./docs
COPY warehouse ./warehouse
COPY orchestration ./orchestration
COPY evals ./evals
COPY observability ./observability
COPY docker-compose.yml ./docker-compose.yml
