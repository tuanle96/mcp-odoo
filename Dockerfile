FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy source code
COPY . /app/

# Create logs directory
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Install the package using the dependency constraints declared by the project.
RUN pip install --no-cache-dir .

# Runtime Odoo connection values should be supplied via `docker run -e ...`.
# Do not bake credential placeholders into the image; Docker flags password ENV
# declarations as secrets even when the default is empty.
ENV ODOO_TIMEOUT="30"
ENV ODOO_VERIFY_SSL="1"
ENV DEBUG="0"

# Set stdout/stderr to unbuffered mode
ENV PYTHONUNBUFFERED=1

# Streamable HTTP uses this port by default when enabled.
EXPOSE 8000

# Run through the public package entry point.
ENTRYPOINT ["odoo-mcp"]
