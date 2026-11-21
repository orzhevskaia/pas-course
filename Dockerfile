FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pas pas
COPY dashboard dashboard
COPY sql sql
COPY tests tests
COPY scripts scripts
COPY README.md pyproject.toml ./
EXPOSE 8501
CMD ["python", "-m", "pas.cli", "run"]
