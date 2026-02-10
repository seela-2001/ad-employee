FROM python:3.12-slim

WORKDIR /app
# Install system dependencies for SQL Server ODBC driver and debugging tools
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2

COPY requirements.txt .

RUN pip install --upgrade pip && pip install --default-timeout=100 -r requirements.txt

COPY . .

EXPOSE 8000

ENV PYTHONPATH=/app

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]