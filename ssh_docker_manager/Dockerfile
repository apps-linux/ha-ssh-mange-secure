ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache openssh-keygen

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY rootfs /
COPY app /app

WORKDIR /app
