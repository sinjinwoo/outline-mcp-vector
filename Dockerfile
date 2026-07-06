FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library modules + both services (single image, both run here)
COPY connector/ ./connector/
COPY shared/ ./shared/
COPY indexer/ ./indexer/
COPY mcpserver/ ./mcpserver/

COPY supervisord.conf /etc/supervisord.conf

# 8000: FastAPI (webhook + sync + health)   8080: MCP SSE
EXPOSE 8000 8080

CMD ["supervisord", "-c", "/etc/supervisord.conf"]
