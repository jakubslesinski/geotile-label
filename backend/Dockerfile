FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev gcc g++ libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/src

COPY pyproject.toml /app/

# Install CPU-only torch first (smaller image), then remaining deps
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir $(python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path('/app/pyproject.toml').read_text()); print(' '.join(d['project']['dependencies']))")

COPY . /app/src/

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
