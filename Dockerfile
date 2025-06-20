FROM python:3.11-alpine

# Установите необходимые системные зависимости
RUN apk update && apk add --no-cache libpq ffmpeg
RUN apk add --virtual .build-deps gcc python3-dev musl-dev postgresql-dev
WORKDIR /usr/src/app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN pip install --upgrade pip

COPY ./requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Укажите команду для запуска бота
CMD ["python", "main.py"]