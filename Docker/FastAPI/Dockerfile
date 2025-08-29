FROM python:3.12.4-slim

WORKDIR /app
ENV TZ=Asia/Baku

COPY ./Docker/FastAPI/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app /app

COPY ./Docker/FastAPI/entrypoint.sh /entrypoint.sh

# dos2unix для корректной работы файлов sh-формата на Windows.
# dos2unix изменяет формат переноса строки, типичная бага при работе
# с Docker + sh-файлами на Windows.
RUN apt-get update && apt-get install -y dos2unix && \
    dos2unix /entrypoint.sh && chmod +x /entrypoint.sh && \
    rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/entrypoint.sh"]
