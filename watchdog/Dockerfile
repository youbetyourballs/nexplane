FROM python:3.12-alpine

RUN apk add --no-cache docker-cli postgresql-client

WORKDIR /app
COPY main.py .

CMD ["python", "main.py"]
