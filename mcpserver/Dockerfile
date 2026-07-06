FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY mcpserver/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library modules
COPY shared/ ./shared/

# Copy service code
COPY mcpserver/ ./mcpserver/

EXPOSE 8080

CMD ["python", "-m", "mcpserver.main"]
