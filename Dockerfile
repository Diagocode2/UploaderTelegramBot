FROM python:3.12-slim-bookworm

WORKDIR /app/
COPY . .

RUN pip3 install -r requirements.txt

RUN apt-get update && apt-get install -y apache2

CMD ["bash","run.sh"]

EXPOSE 10000