FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY indexer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library modules
COPY connector/ ./connector/
COPY shared/ ./shared/

# Copy service code
COPY indexer/ ./indexer/

EXPOSE 8000

CMD ["uvicorn", "indexer.main:app", "--host", "0.0.0.0", "--port", "8000"]
