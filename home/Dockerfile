FROM docker:cli AS docker-cli
FROM python:3.13-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./home/
COPY --from=redis_panel . ./src/redis/
COPY --from=postgres_panel . ./src/postgres/
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "600", "--access-logfile", "-", "home.app:create_app()"]
